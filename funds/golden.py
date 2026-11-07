"""Appendix A golden cases, runnable live from the workbench (TST-01, TST-04).

Each case recomputes through the engine on every page view and compares to the specification.
"""

from datetime import date

from engine.admin_fees import FAA_SCHEDULE, cpi_escalate, days_overdue, late_interest, quarterly_invoice
from engine.config import CACPEF_TERMS as T
from engine.equalisation import Drawdown, equalise
from engine.fees import annual_fee, apply_offset, fee_basis, quarterly_instalment
from engine.fx import translate_investment, usd_acquisition_cost
from engine.money import D, fmt
from engine.sla import FAA_SLA, BusinessCalendar, assess_nav_error, quarter_timeline
from engine.waterfall import REALISED, UNREALISED, WRITTEN_OFF, LPState, clawback_exposure, distribute


def _tiers(r):
    return ' | '.join(f'T{t.tier} {fmt(t.to_lp)}/{fmt(t.to_cir)}' for t in r.tiers)


def wf01():
    r = distribute(T, LPState(funding={'D1': D(100)}), D(150), investment_id='D1',
                   statuses={'D1': REALISED}, pref_accrued=D(8))
    return ('T1 100,00/0,00 | T2 8,00/0,00 | T3 0,00/2,00 | T4 32,00/8,00; LP 140,00; CIR 10,00; GP 6,00; Vehicle 4,00',
            f'{_tiers(r)}; LP {fmt(r.to_lp)}; CIR {fmt(r.to_cir)}; GP {fmt(r.to_gp)}; Vehicle {fmt(r.to_vehicle)}')


def _two_deals(d1_status, amount):
    st = LPState(funding={'D1': D(40), 'D2': D(60)}, expenses=D(10))
    return distribute(T, st, D(amount), investment_id='D2', statuses={'D1': d1_status, 'D2': REALISED},
                      pref_accrued=D(12))


def wf02():
    r = _two_deals(WRITTEN_OFF, 130)
    return ('Hurdle 110,00 | T1 110,00/0,00 | T2 12,00/0,00 | T3 0,00/3,00 | T4 4,00/1,00; carry 4,00',
            f'Hurdle {fmt(r.hurdle)} | {_tiers(r)}; carry {fmt(r.to_cir)}')


def wf03():
    r = _two_deals(UNREALISED, 130)
    exp = clawback_exposure(T, r.state, D(12))
    return ('Hurdle 70,00 | T1 70,00/0,00 | T2 12,00/0,00 | T3 0,00/3,00 | T4 36,00/9,00; carry 12,00; clawback exposure flagged',
            f'Hurdle {fmt(r.hurdle)} | {_tiers(r)}; carry {fmt(r.to_cir)}; clawback exposure '
            f'{"flagged" if exp > 0 else "none"}')


def wf04():
    st = LPState(funding={'D1': D(40), 'D2': D(60)}, expenses=D(10))
    r1 = distribute(T, st, D(20), investment_id='D1', statuses={'D1': REALISED, 'D2': UNREALISED}, pref_accrued=D(12))
    r2 = distribute(T, r1.state, D(130), investment_id='D2', statuses={'D1': REALISED, 'D2': REALISED},
                    pref_accrued=D(12))
    return ('Deal1 T1 20,00; Deal2 T1 90,00, T2 12,00, T3 3,00 CIR, T4 20,00/5,00; LP 142,00; CIR 8,00',
            f'Deal1 T1 {fmt(r1.tier(1).to_lp)}; Deal2 T1 {fmt(r2.tier(1).to_lp)}, T2 {fmt(r2.tier(2).to_lp)}, '
            f'T3 {fmt(r2.tier(3).to_cir)} CIR, T4 {fmt(r2.tier(4).to_lp)}/{fmt(r2.tier(4).to_cir)}; '
            f'LP {fmt(r2.state.lp_total)}; CIR {fmt(r2.state.cir_total)}')


def wf05():
    r = _two_deals(WRITTEN_OFF, 70)
    return ('T1 70,00 LP; CIR 0,00; capital shortfall 40,00',
            f'T1 {fmt(r.tier(1).to_lp)} LP; CIR {fmt(r.to_cir)}; capital shortfall {fmt(r.hurdle - r.state.roc_paid)}')


def fee01():
    ic = date(2026, 1, 1)
    a = fee_basis(T, date(2026, 4, 1), ic, D(10000000), D(6000000))
    b = fee_basis(T, date(2031, 1, 1), ic, D(10000000), D(6000000))
    return ('Years 1-5: 200 000,00 p.a., 50 000,00 per quarter; after: 120 000,00 p.a., 30 000,00 per quarter',
            f'Years 1-5: {fmt(annual_fee(T, a.amount))} p.a., '
            f'{fmt(quarterly_instalment(T, a.amount, date(2026, 4, 1), date(2026, 6, 30)))} per quarter; '
            f'after: {fmt(annual_fee(T, b.amount))} p.a., '
            f'{fmt(quarterly_instalment(T, b.amount, date(2031, 1, 1), date(2031, 3, 31)))} per quarter')


def fee02():
    r = apply_offset(T, D(50000), D(60000))
    return ('Fee 0,00; carried forward 10 000,00', f'Fee {fmt(r.net_fee)}; carried forward {fmt(r.carried_forward)}')


def fx01():
    c = usd_acquisition_cost([(D(4800000000), D(480))])
    tr = translate_investment(c, D(4800000000), D(520))
    return ('USD cost 10 000 000,00; fee base 10 000 000,00 at closing rate 520,00; cost not retranslated (NAV 9 230 769,23)',
            f'USD cost {fmt(c.usd)}; fee base {fmt(tr.fee_base_usd)} at closing rate 520,00; '
            f'cost {"not retranslated" if tr.cost_usd == c.usd else "RETRANSLATED"} (NAV {fmt(tr.fair_value_usd)})')


def eql01():
    s = equalise(T, commitments={'A': D(10000000)}, drawdowns=[Drawdown(date(2026, 1, 15), {'A': D(2000000)})],
                 new_lp='B', new_commitment=D(10000000), admission_date=date(2027, 1, 15),
                 initial_closing=date(2026, 1, 15))
    return ('Catch-up 1 000 000,00 to A; A and B 1 000 000,00 drawn each; interest B->A 80 000,00; fee equalisation 200 000,00 + interest',
            f'Catch-up {fmt(s.catch_up)} to A; A and B {fmt(s.drawn_after["A"])} drawn each; interest B->A '
            f'{fmt(s.interest["A"])}; fee equalisation {fmt(s.fee_equalisation)} + interest')


def adm01():
    inv = quarterly_invoice(FAA_SCHEDULE, approved_nav=D(60000000), notices=40)
    lines = {line.code: line.amount for line in inv.lines}
    return ('Tier 149 000,00 p.a.; quarter 37 250,00; capital activity 10 000,00; no establishment fee',
            f'Tier {fmt(inv.tier.annual)} p.a.; quarter {fmt(lines["ADMIN"])}; capital activity {fmt(lines["CAPACT"])}; '
            f'{"no establishment fee" if "ESTAB" not in lines else "establishment fee BILLED"}')


def adm02():
    fee = cpi_escalate(FAA_SCHEDULE, D(149000), D('0.062'))
    days = days_overdue(FAA_SCHEDULE, date(2026, 4, 1), date(2026, 5, 6))
    return ('CPI capped at 5% -> 156 450,00 p.a.; late interest on 37 250,00 from day 16, 20 days -> 372,50',
            f'CPI capped at 5% -> {fmt(fee)} p.a.; late interest on 37 250,00 from day 16, {days} days -> '
            f'{fmt(late_interest(FAA_SCHEDULE, D(37250), days))}')


def nav01():
    cal = BusinessCalendar()
    a = assess_nav_error(cal, FAA_SLA, published=D(100), recomputed=D('100.18'), threshold=T.nav_materiality,
                         discovered=date(2026, 4, 6), notified=date(2026, 4, 6))
    b = assess_nav_error(cal, FAA_SLA, published=D(100), recomputed=D('100.05'), threshold=T.nav_materiality,
                         discovered=date(2026, 4, 6))
    return ('0,18% material: notify GP 07.04.2026, corrective NAV 13.04.2026; 0,05% prospective',
            f'{fmt(a.impact * 100)}% {"material" if a.material else "not material"}: notify GP '
            f'{a.notify_gp_by:%d.%m.%Y}, corrective NAV {a.corrective_nav_by:%d.%m.%Y}; '
            f'{fmt(b.impact * 100)}% {"prospective" if not b.material else "MATERIAL"}')


def sla01():
    cal = BusinessCalendar()
    t = quarter_timeline(cal, FAA_SLA, quarter_end=date(2026, 3, 31), inputs_received=date(2026, 4, 20),
                         draft_issued=date(2026, 4, 28))
    return ('Cut-off 15.04.2026; clock 20.04.2026; draft NAV due 04.05.2026; deemed approval 05.05.2026; final NAV due 08.05.2026',
            f'Cut-off {t.cut_off:%d.%m.%Y}; clock {t.clock_start:%d.%m.%Y}; draft NAV due {t.draft_nav_due:%d.%m.%Y}; '
            f'{t.approval_kind} approval {t.approved:%d.%m.%Y}; final NAV due {t.final_nav_due:%d.%m.%Y}')


CASES = [
    ('GC-WF-01', 'Single Investment, exit 150,00, pref 8,00', wf01),
    ('GC-WF-02', 'Deal1 written off, Deal2 exit 130,00', wf02),
    ('GC-WF-03', 'Deal1 unresolved, Deal2 exit 130,00', wf03),
    ('GC-WF-04', 'Deal1 realised at a loss, then Deal2', wf04),
    ('GC-WF-05', 'Deal2 exit only 70,00', wf05),
    ('GC-FEE-01', 'Fee basis switch after 5th anniversary', fee01),
    ('GC-FEE-02', 'Fee Income offset, not below zero', fee02),
    ('GC-FX-01', 'KZT investment, frozen USD cost', fx01),
    ('GC-EQL-01', 'Subsequent closing, three legs', eql01),
    ('GC-ADM-01', 'FAA Annex B tier and capital-activity fees', adm01),
    ('GC-ADM-02', 'CPI cap and late interest', adm02),
    ('GC-NAV-01', 'Material NAV error 0,18%', nav01),
    ('GC-SLA-01', 'Clock start and deemed approval', sla01),
]


def run_all():
    out = []
    for code, title, fn in CASES:
        expected, computed = fn()
        computed = computed.replace('\u00a0', ' ')
        out.append({'code': code, 'title': title, 'expected': expected, 'computed': computed,
                    'passed': expected == computed})
    return out
