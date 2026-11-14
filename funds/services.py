"""Application services: read the database, call the deterministic engine, write results.

The engine never touches the database; these functions are the only bridge (ARC-01).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from engine import ENGINE_VERSION
from engine.admin_fees import FAA_SCHEDULE, days_overdue, late_interest, quarterly_invoice
from engine.equalisation import Drawdown, equalise
from engine.fees import apply_offset, fee_basis, quarterly_instalment, quarters_between
from engine.fx import AcquisitionCost, translate_investment, usd_acquisition_cost
from engine.irr import preferred_return_outstanding, xirr
from engine.money import ZERO, D, allocate, rnd
from engine.waterfall import (
    REALISED, UNREALISED, WRITTEN_OFF, LPState, clawback_exposure, distribute,
)

from .models import Contribution, Distribution, Event, Fund, Investment, LedgerEntry, Partner

WATERFALL_KINDS = (Partner.LP, Partner.SPONSOR, Partner.FEEDER)
ENGINE_STATUS = {
    Investment.UNREALISED: UNREALISED, Investment.PARTIAL: UNREALISED,
    Investment.REALISED: REALISED, Investment.WRITTEN_OFF: WRITTEN_OFF,
}


class SegregationOfDuties(PermissionError):
    pass


def log(actor, action, obj, *, fund=None, before=None, after=None, reason=''):
    Event.objects.create(
        fund=fund, actor=actor, action=action, object_type=type(obj).__name__, object_id=str(obj.pk),
        before=before, after=after, reason=reason,
    )


def terms_of(fund: Fund):
    v = fund.current_terms_version
    return fund.terms, (v.version if v else 0)


def waterfall_partners(fund):
    return list(fund.partners.filter(kind__in=WATERFALL_KINDS))


# ---------- Commitments, contributions, investments ----------

def commitments(fund, as_of: date | None = None) -> dict:
    out = defaultdict(lambda: ZERO)
    for p in fund.partners.prefetch_related('commitments'):
        for c in p.commitments.all():
            if as_of is None or c.date <= as_of:
                out[p.pk] += c.amount
    return dict(out)


def contributions(fund, as_of: date | None = None):
    qs = Contribution.objects.filter(partner__fund=fund, funded_date__isnull=False).select_related('partner')
    if as_of:
        qs = qs.filter(funded_date__lte=as_of)
    return list(qs)


def investment_cost(inv: Investment) -> AcquisitionCost:
    """USD Acquisition Cost frozen at the dealt rates of the funding contributions (FX-01)."""
    tranches = [(c.amount_local, c.fx_rate) for c in inv.contributions.filter(funded_date__isnull=False)]
    if not tranches:
        return AcquisitionCost(ZERO, ZERO, ZERO)
    return usd_acquisition_cost(tranches)


def investment_fair_value_usd(inv: Investment) -> Decimal:
    if inv.status in (Investment.REALISED, Investment.WRITTEN_OFF):
        return ZERO
    return translate_investment(investment_cost(inv), inv.fair_value_local, inv.closing_rate).fair_value_usd


def funding_by_partner(inv: Investment) -> dict:
    out = defaultdict(lambda: ZERO)
    for c in inv.contributions.filter(funded_date__isnull=False):
        out[c.partner_id] += c.amount_usd
    return dict(out)


# ---------- Waterfall ----------

def lp_state(partner: Partner, *, exclude: Distribution | None = None) -> LPState:
    """Rebuild the cumulative waterfall position from approved distributions (replay)."""
    funding = defaultdict(lambda: ZERO)
    expenses = ZERO
    for c in partner.contributions.filter(funded_date__isnull=False):
        if c.purpose == Contribution.INVESTMENT and c.investment_id:
            funding[c.investment_id] += c.amount_usd
        else:
            expenses += c.amount_usd
    st = LPState(funding=dict(funding), expenses=expenses)
    qs = Distribution.objects.filter(fund=partner.fund, state=Distribution.APPROVED).order_by('date', 'id')
    if exclude and exclude.pk:
        qs = qs.exclude(pk=exclude.pk)
    for dist in qs:
        r = dist.result['partners'].get(str(partner.pk))
        if not r:
            continue
        st = LPState(
            funding=st.funding, expenses=st.expenses,
            roc_paid=st.roc_paid + D(r['tiers'][0]['lp']),
            pref_paid=st.pref_paid + D(r['tiers'][1]['lp']),
            catch_up_flow=st.catch_up_flow + D(r['tiers'][2]['lp']) + D(r['tiers'][2]['cir']),
            catch_up_cir=st.catch_up_cir + D(r['tiers'][2]['cir']),
            lp_total=st.lp_total + D(r['to_lp']),
            cir_total=st.cir_total + D(r['to_cir']),
        )
    return st


def lp_distributions_paid(partner: Partner, before: date | None = None):
    out = []
    for dist in Distribution.objects.filter(fund=partner.fund, state=Distribution.APPROVED).order_by('date', 'id'):
        r = dist.result['partners'].get(str(partner.pk))
        if r and (before is None or dist.date <= before):
            out.append((dist.date, D(r['to_lp'])))
    return out


def pref_accrued(partner: Partner, state: LPState, as_of: date) -> Decimal:
    """Total preferred return accrued to as_of (PR-01): 8% IRR compounded annually, excluding
    recallable contributions; equalisation entries count from their deemed dates, so an Additional
    LP accrues as if admitted at the Initial Closing and earlier LPs only on what stayed theirs."""
    terms = partner.fund.terms
    flows = [(c.effective_date, c.amount_usd) for c in partner.contributions.filter(
        funded_date__isnull=False, funded_date__lte=as_of, recallable=False)]
    contributed = sum((a for _, a in flows), ZERO)
    outstanding = preferred_return_outstanding(
        flows, lp_distributions_paid(partner, as_of), contributed - state.roc_paid, as_of,
        terms.hurdle_rate, terms.day_count,
    )
    return rnd(outstanding + state.pref_paid)


def fund_statuses(fund, override: dict | None = None) -> dict:
    st = {i.pk: ENGINE_STATUS[i.status] for i in fund.investments.all()}
    st.update(override or {})
    return st


def compute_distribution(fund: Fund, investment: Investment, on: date, gross: Decimal, status_after: str,
                         *, exclude: Distribution | None = None) -> dict:
    """Apportion an Investment's receipts by funding, then run each LP's slice (WF-01..02)."""
    terms, version = terms_of(fund)
    funding = funding_by_partner(investment)
    lps = {p.pk: p for p in waterfall_partners(fund) if funding.get(p.pk)}
    slices = allocate(D(gross), {pk: funding[pk] for pk in lps})
    statuses = fund_statuses(fund, {investment.pk: ENGINE_STATUS[status_after]})
    rows = {}
    totals = defaultdict(lambda: ZERO)
    for pk, p in lps.items():
        st = lp_state(p, exclude=exclude)
        pref = pref_accrued(p, st, on)
        r = distribute(terms, st, slices[pk], investment_id=investment.pk, statuses=statuses, pref_accrued=pref)
        exposure = clawback_exposure(terms, r.state, pref)
        rows[str(pk)] = {
            'code': p.code, 'name': p.name, 'slice': str(slices[pk]), 'funding': str(funding[pk]),
            'hurdle': str(r.hurdle), 'hurdle_parts': {k: str(v) for k, v in r.hurdle_parts.items()},
            'roc_paid_before': str(st.roc_paid), 'pref_accrued': str(pref),
            'tiers': [{'tier': t.tier, 'name': t.name, 'clause': t.clause,
                       'lp': str(t.to_lp), 'cir': str(t.to_cir)} for t in r.tiers],
            'to_lp': str(r.to_lp), 'to_cir': str(r.to_cir), 'to_gp': str(r.to_gp),
            'to_vehicle': str(r.to_vehicle), 'clawback_exposure': str(exposure),
        }
        totals['lp'] += r.to_lp
        totals['cir'] += r.to_cir
        totals['gp'] += r.to_gp
        totals['vehicle'] += r.to_vehicle
        totals['exposure'] += exposure
        for t in r.tiers:
            totals[f't{t.tier}_lp'] += t.to_lp
            totals[f't{t.tier}_cir'] += t.to_cir
    return {
        'partners': rows,
        'totals': {k: str(v) for k, v in totals.items()},
        'terms_version': version, 'engine_version': ENGINE_VERSION,
        'statuses': {str(k): v for k, v in statuses.items()},
    }


@transaction.atomic
def propose_distribution(user, fund, investment, on, gross, status_after) -> Distribution:
    terms, version = terms_of(fund)
    result = compute_distribution(fund, investment, on, gross, status_after)
    dist = Distribution.objects.create(
        fund=fund, investment=investment, date=on, gross_amount=D(gross), investment_status_after=status_after,
        result=result, terms_version=version, engine_version=ENGINE_VERSION, created_by=user,
    )
    log(user, 'distribution.proposed', dist, fund=fund, after={'gross': str(gross), 'investment': investment.code},
        reason='Maker proposal; awaiting four-eyes approval')
    return dist


@transaction.atomic
def approve_distribution(user, dist: Distribution) -> Distribution:
    """Four-eyes (NFR-04): the approver cannot be the maker. Recomputes before posting so the
    approved figures are exactly what the engine gives on approval-time data."""
    dist = Distribution.objects.select_for_update().get(pk=dist.pk)
    if dist.state != Distribution.DRAFT:
        raise ValueError('Only a draft distribution can be approved')
    if dist.created_by_id == user.pk:
        raise SegregationOfDuties('The maker of a distribution cannot approve it')
    fresh = compute_distribution(dist.fund, dist.investment, dist.date, dist.gross_amount,
                                 dist.investment_status_after, exclude=dist)
    if fresh['totals'] != dist.result['totals']:
        raise ValueError('Inputs changed since the proposal; reject and propose again')
    dist.state = Distribution.APPROVED
    dist.approved_by = user
    dist.approved_at = timezone.now()
    dist.save()
    inv = dist.investment
    before = inv.status
    inv.status = dist.investment_status_after
    inv.save(update_fields=['status'])
    journal = f'DIST-{dist.pk}'
    fund = dist.fund
    LedgerEntry.objects.create(fund=fund, journal=journal, date=dist.date, account='Cash — proceeds receivable',
                               debit=dist.gross_amount, memo=f'Proceeds {inv.code}', actor=user)
    for pk, row in dist.result['partners'].items():
        partner = Partner.objects.get(pk=pk)
        LedgerEntry.objects.create(fund=fund, partner=partner, journal=journal, date=dist.date,
                                   account='Distributions payable — LP', credit=D(row['to_lp']),
                                   memo=f'{inv.code} tiers 1-4 LP share', actor=user)
    totals = dist.result['totals']
    if D(totals.get('gp', '0')):
        LedgerEntry.objects.create(fund=fund, journal=journal, date=dist.date, account='Carried interest payable — GP',
                                   credit=D(totals['gp']), memo='Carry 60%', actor=user)
    if D(totals.get('vehicle', '0')):
        LedgerEntry.objects.create(fund=fund, journal=journal, date=dist.date,
                                   account='Carried interest payable — Carry Vehicle',
                                   credit=D(totals['vehicle']), memo='Carry 40%', actor=user)
    log(user, 'distribution.approved', dist, fund=fund, before={'state': 'draft'}, after={'state': 'approved'},
        reason='Four-eyes approval; ledger posted')
    if before != inv.status:
        log(user, 'investment.status', inv, fund=fund, before={'status': before}, after={'status': inv.status},
            reason=f'Distribution {dist.pk}')
    return dist


@transaction.atomic
def reject_distribution(user, dist: Distribution, reason: str):
    dist.state = Distribution.REJECTED
    dist.save(update_fields=['state'])
    log(user, 'distribution.rejected', dist, fund=dist.fund, after={'state': 'rejected'}, reason=reason)


# ---------- Capital accounts and performance ----------

@dataclass
class CapitalAccount:
    partner: Partner
    commitment: Decimal = ZERO
    paid_in: Decimal = ZERO
    paid_in_investments: Decimal = ZERO
    paid_in_fees: Decimal = ZERO
    unfunded: Decimal = ZERO
    distributed: Decimal = ZERO
    tiers: list = field(default_factory=lambda: [ZERO] * 4)
    nav: Decimal = ZERO
    accrued_carry: Decimal = ZERO
    pnl: Decimal = ZERO
    pref_accrued: Decimal = ZERO
    clawback_exposure: Decimal = ZERO
    dpi: Decimal | None = None
    rvpi: Decimal | None = None
    tvpi: Decimal | None = None
    irr: Decimal | None = None
    share: Decimal = ZERO


def capital_accounts(fund: Fund, as_of: date) -> list[CapitalAccount]:
    """Per-partner capital account (ACC-01/02). NAV per partner is a hypothetical liquidation:
    every unrealised Investment is run through the waterfall at fair value, so P&L reflects
    waterfall entitlements (cl. 9.2) and the CIR share shows as accrued carry (CI-01)."""
    terms, _ = terms_of(fund)
    comm = commitments(fund, as_of)
    total_comm = sum((comm.get(p.pk, ZERO) for p in waterfall_partners(fund)), ZERO)
    unrealised = [i for i in fund.investments.all() if i.status in (Investment.UNREALISED, Investment.PARTIAL)]
    fv = {i.pk: investment_fair_value_usd(i) for i in unrealised}
    funding = {i.pk: funding_by_partner(i) for i in unrealised}
    statuses = fund_statuses(fund)
    out = []
    for p in waterfall_partners(fund):
        ca = CapitalAccount(partner=p, commitment=comm.get(p.pk, ZERO))
        flows = []
        for c in p.contributions.filter(funded_date__isnull=False, funded_date__lte=as_of):
            ca.paid_in += c.amount_usd
            if c.purpose == Contribution.INVESTMENT:
                ca.paid_in_investments += c.amount_usd
            else:
                ca.paid_in_fees += c.amount_usd
            flows.append((c.funded_date, -c.amount_usd))
        ca.unfunded = ca.commitment - ca.paid_in + _recallable_returned(p)
        for dist in Distribution.objects.filter(fund=fund, state=Distribution.APPROVED, date__lte=as_of):
            r = dist.result['partners'].get(str(p.pk))
            if r:
                ca.distributed += D(r['to_lp'])
                for i in range(4):
                    ca.tiers[i] += D(r['tiers'][i]['lp'])
                flows.append((dist.date, D(r['to_lp'])))
        st = lp_state(p)
        ca.pref_accrued = pref_accrued(p, st, as_of)
        ca.clawback_exposure = clawback_exposure(terms, st, ca.pref_accrued)
        for inv in unrealised:
            share = funding[inv.pk].get(p.pk, ZERO)
            total = sum(funding[inv.pk].values(), ZERO)
            if not share or not fv[inv.pk]:
                continue
            slice_ = rnd(fv[inv.pk] * share / total)
            r = distribute(terms, st, slice_, investment_id=inv.pk, statuses={**statuses, inv.pk: REALISED},
                           pref_accrued=ca.pref_accrued)
            ca.nav += r.to_lp
            ca.accrued_carry += r.to_cir
            st = r.state
        ca.pnl = ca.nav + ca.distributed - ca.paid_in
        ca.share = (ca.commitment / total_comm) if total_comm else ZERO
        if ca.paid_in:
            ca.dpi = (ca.distributed / ca.paid_in).quantize(D('0.01'))
            ca.rvpi = (ca.nav / ca.paid_in).quantize(D('0.01'))
            ca.tvpi = ca.dpi + ca.rvpi
            if ca.nav:
                flows.append((as_of, ca.nav))
            ca.irr = xirr(flows)
        out.append(ca)
    return out


def _recallable_returned(partner) -> Decimal:
    return ZERO   # recycling (CAL-02) is tracked when recallable distributions are configured


def fund_summary(fund: Fund, as_of: date) -> dict:
    accounts = capital_accounts(fund, as_of)
    keys = ('commitment', 'paid_in', 'unfunded', 'distributed', 'nav', 'accrued_carry', 'clawback_exposure')
    tot = {k: sum((getattr(a, k) for a in accounts), ZERO) for k in keys}
    flows = []
    for a in accounts:
        for c in a.partner.contributions.filter(funded_date__isnull=False, funded_date__lte=as_of):
            flows.append((c.funded_date, -c.amount_usd))
    for d in Distribution.objects.filter(fund=fund, state=Distribution.APPROVED, date__lte=as_of):
        flows.append((d.date, D(d.result['totals']['lp'])))
    if tot['nav']:
        flows.append((as_of, tot['nav']))
    paid = tot['paid_in']
    tot['dpi'] = (tot['distributed'] / paid).quantize(D('0.01')) if paid else None
    tot['rvpi'] = (tot['nav'] / paid).quantize(D('0.01')) if paid else None
    tot['tvpi'] = (tot['dpi'] + tot['rvpi']) if paid else None
    tot['irr'] = xirr(flows)
    tot['gross_nav'] = sum((investment_fair_value_usd(i) for i in fund.investments.all()), ZERO)
    tot['called_pct'] = (paid / tot['commitment'] * 100).quantize(D('0.1')) if tot['commitment'] else ZERO
    return {'accounts': accounts, 'totals': tot}


# ---------- Management fee schedule ----------

def fee_schedule(fund: Fund, upto: date, fee_income: dict | None = None) -> list[dict]:
    """Quarterly-in-advance fee per LP with basis switch and Fee Income offset (FEE-01..03).
    fee_income: {quarter_start: amount received since previous payment date}."""
    terms, _ = terms_of(fund)
    fee_income = fee_income or {}
    lps = waterfall_partners(fund)
    rows = []
    carried = {p.pk: ZERO for p in lps}
    for q_start, q_end in quarters_between(fund.initial_closing, upto):
        comm = commitments(fund, q_end)
        shares = allocate(D(fee_income.get(q_start, 0)), {p.pk: comm.get(p.pk, ZERO) for p in lps})
        for p in lps:
            c = comm.get(p.pk, ZERO)
            if not c:
                continue
            admitted = p.commitments.order_by('date').first().date
            net_cost = _net_invested_cost(p, q_start)
            basis = fee_basis(terms, q_start, fund.initial_closing, c, net_cost)
            # Fee before an Additional LP's admission is collected as fee equalisation (EQL-02);
            # from admission it pays the regular instalments.
            gross = quarterly_instalment(terms, basis.amount, q_start, q_end,
                                         from_date=max(fund.initial_closing, admitted))
            off = apply_offset(terms, gross, shares.get(p.pk, ZERO), carried[p.pk])
            carried[p.pk] = off.carried_forward
            rows.append({'quarter': q_start, 'quarter_end': q_end, 'partner': p, 'basis': basis,
                         'gross': gross, 'offset': off.offset_used, 'net': off.net_fee,
                         'carried_forward': off.carried_forward, 'fee_income_share': shares.get(p.pk, ZERO)})
    return rows


def _net_invested_cost(partner, as_of) -> Decimal:
    total = ZERO
    for c in partner.contributions.filter(purpose=Contribution.INVESTMENT, funded_date__isnull=False,
                                          funded_date__lte=as_of).select_related('investment'):
        if c.investment and c.investment.status not in (Investment.REALISED, Investment.WRITTEN_OFF):
            total += c.amount_usd
    return total


# ---------- Equalisation preview ----------

def equalisation_preview(fund: Fund, new_lp: str, new_commitment: Decimal, admission: date):
    terms, _ = terms_of(fund)
    lps = waterfall_partners(fund)
    comm = commitments(fund, admission)
    by_dd = defaultdict(dict)
    dates = {}
    for c in contributions(fund, admission):
        # capital drawn for investments and expenses; the fee is equalised separately (leg 3)
        if c.partner.kind in WATERFALL_KINDS and c.purpose != Contribution.FEE:
            by_dd[c.drawdown_no][c.partner.code] = by_dd[c.drawdown_no].get(c.partner.code, ZERO) + c.amount_usd
            dates.setdefault(c.drawdown_no, c.effective_date)
    drawdowns = [Drawdown(dates[n], by_dd[n]) for n in sorted(by_dd)]
    return equalise(terms, commitments={p.code: comm.get(p.pk, ZERO) for p in lps if comm.get(p.pk)},
                    drawdowns=drawdowns, new_lp=new_lp, new_commitment=D(new_commitment),
                    admission_date=admission, initial_closing=fund.initial_closing)


# ---------- FAA billing ----------

def invoice_view(inv, as_of: date) -> dict:
    overdue = days_overdue(FAA_SCHEDULE, inv.issued, inv.paid or as_of)
    return {'invoice': inv, 'overdue_days': overdue,
            'late_interest': late_interest(FAA_SCHEDULE, inv.total, overdue)}


def draft_admin_invoice(fund, approved_nav, notices, onboardings=0, include_establishment=False):
    return quarterly_invoice(FAA_SCHEDULE, approved_nav=approved_nav, notices=notices, onboardings=onboardings,
                             include_establishment=include_establishment)
