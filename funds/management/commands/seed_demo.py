"""Fill an empty database with a fictitious demo fund VCPEF.

3 LPs x USD 50m: LP01 and LP02 at the Initial Closing (01.11.2024), LP03 at a Subsequent
Closing a year later with equalisation computed by the engine; USD 120m contributed by the
second anniversary (01.11.2026). USD only.

Users get the password from DEMO_PASSWORD (never printed, never stored in git).
Run:  python manage.py seed_demo            (only if the database is empty)
      python manage.py flush && seed_demo   (start over)
"""

import os
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from engine.config import VCPEF_TERMS as T
from engine.equalisation import Drawdown, equalise
from engine.fees import quarterly_instalment, quarters_between
from engine.money import ZERO, D, allocate, rnd

from funds import services
from funds.models import (
    AdminInvoice, Commitment, Contribution, Event, Fund, FundTermsVersion, Investment, LedgerEntry, NavError,
    Partner, QuarterClose,
)

GROUPS = ['Administrator', 'Approver', 'GP', 'Auditor']
USERS = [('operator', 'Administrator'), ('checker', 'Approver'), ('gp', 'GP'), ('auditor', 'Auditor')]

INITIAL_CLOSING = date(2024, 11, 1)
SUBSEQUENT_CLOSING = date(2025, 11, 1)
SECOND_ANNIVERSARY = date(2026, 11, 1)
TOTAL_CONTRIBUTED = D('120000000')
COMMITMENT = D('50000000')

LPS = [
    # code, name, country, FATCA/CRS, side letter, admitted
    ('LP01', 'Steppe Pension Fund (demo)', 'KZ', 'CRS: Financial institution', '', INITIAL_CLOSING),
    ('LP02', 'Altyn Family Office (demo)', 'KZ', 'Passive NFFE', 'MFN', INITIAL_CLOSING),
    ('LP03', 'Caspian Holdings (demo)', 'AE', 'Active NFFE', 'Excused from tobacco investments',
     SUBSEQUENT_CLOSING),
]

INVESTMENTS = [
    # code, name, sector, acquired, cost, fair value
    ('INV1', 'Steppe Logistics', 'Logistics', date(2025, 1, 15), '30000000', '30000000'),
    ('INV2', 'AgroTech Kazakhstan', 'Agriculture', date(2025, 4, 15), '12000000', '0'),
    ('INV3', 'Qazaq Fintech', 'Financial services', date(2025, 9, 15), '35000000', '41000000'),
    ('INV4', 'Caspian Healthcare', 'Healthcare', date(2026, 6, 15), '36000000', '38000000'),
]


class Command(BaseCommand):
    help = 'Create the VCPEF demo fund: partners, drawdowns, equalisation, investments, distributions, FAA records'

    @transaction.atomic
    def handle(self, *args, **opts):
        if Fund.objects.exists():
            self.stdout.write('Demo data already present; nothing to do')
            return
        password = os.environ.get('DEMO_PASSWORD')
        if not password:
            raise CommandError('Set DEMO_PASSWORD in the environment (.env) first')

        users = {}
        for g in GROUPS:
            Group.objects.get_or_create(name=g)
        for username, group in USERS:
            u, _ = User.objects.get_or_create(username=username, defaults={'email': f'{username}@centrum.kz'})
            u.set_password(password)
            u.is_staff = group in ('Administrator', 'Approver')
            u.save()
            u.groups.add(Group.objects.get(name=group))
            users[username] = u
        op, ck = users['operator'], users['checker']

        fund = Fund.objects.create(
            code='VCPEF', name='VCPEF — Demo Private Equity Fund', initial_closing=INITIAL_CLOSING,
            lpa_reference='LPA dated 01.11.2024 (demo)', target_size=D('150000000'), min_commitment=D('10000000'),
            units_outstanding=D('1500000'),
        )
        FundTermsVersion.objects.create(fund=fund, version=1, terms=T.as_dict(), effective_from=INITIAL_CLOSING,
                                        reason='Initial configuration from LPA cl. 8-9', created_by=op, approved_by=ck)
        Partner.objects.create(fund=fund, code='GP', name='VCPEF GP Ltd (demo)', kind=Partner.GP, country='OM',
                               kyc_status=Partner.KYC_APPROVED)
        Partner.objects.create(fund=fund, code='CIV', name='VCPEF Carry LP (demo)', kind=Partner.CIV, country='KZ',
                               kyc_status=Partner.KYC_APPROVED)
        lps = {}
        for code, name, country, fatca, side, admitted in LPS:
            p = Partner.objects.create(fund=fund, code=code, name=name, country=country, fatca_crs=fatca,
                                       side_letter=side, kyc_status=Partner.KYC_APPROVED,
                                       email=f'{code.lower()}@example.com')
            Commitment.objects.create(partner=p, date=admitted, amount=COMMITMENT,
                                      closing=Commitment.INITIAL if admitted == INITIAL_CLOSING
                                      else Commitment.SUBSEQUENT)
            lps[code] = (p, admitted)

        dd_no = [0]

        def admitted_on(on):
            return {code: p for code, (p, adm) in lps.items() if adm <= on}

        def drawdown(on, purpose, per_lp: dict, investment=None):
            dd_no[0] += 1
            for code, amount in per_lp.items():
                Contribution.objects.create(
                    partner=lps[code][0], drawdown_no=dd_no[0], notice_date=on - timedelta(days=10), due_date=on,
                    funded_date=on, purpose=purpose, investment=investment, amount_local=amount,
                    amount_usd=amount, fx_source='USD')
            return dd_no[0]

        def pro_rata(on, total):
            members = admitted_on(on)
            return {code: v for code, v in allocate(D(total), {c: COMMITMENT for c in members}).items()}

        # --- Management fee: quarterly in advance from each LP's admission (FEE-02)
        fee_total = ZERO
        for q_start, q_end in quarters_between(INITIAL_CLOSING, SECOND_ANNIVERSARY):
            due = max(q_start, INITIAL_CLOSING)
            per = {}
            for code, (p, adm) in lps.items():
                if adm <= q_end:
                    amt = quarterly_instalment(T, COMMITMENT, q_start, q_end, from_date=max(INITIAL_CLOSING, adm))
                    if amt:
                        per[code] = amt
            # an LP admitted mid-quarter funds its stub with the admission, not before
            on_time = {c: a for c, a in per.items() if lps[c][1] <= due}
            late = {c: a for c, a in per.items() if lps[c][1] > due}
            if on_time:
                drawdown(due, Contribution.FEE, on_time)
            for c, a in late.items():
                drawdown(lps[c][1], Contribution.FEE, {c: a})
            fee_total += sum(per.values(), ZERO)

        # --- Investments
        investments = {}
        for code, name, sector, acquired, cost, fv in INVESTMENTS:
            inv = Investment.objects.create(fund=fund, code=code, name=name, sector=sector,
                                            holding_entity=f'VCPEF Holdco {code[-1]}', acquired=acquired,
                                            fair_value_local=D(fv))
            investments[code] = inv
            drawdown(acquired, Contribution.INVESTMENT, pro_rata(acquired, cost), inv)
        invested = sum((D(c[4]) for c in INVESTMENTS), ZERO)

        # --- Equalisation of LP03 at the Subsequent Closing (EQL-01..02), legs computed by the engine
        lp03, adm = lps['LP03']
        fee_eq_rows = []
        for q_start, q_end in quarters_between(INITIAL_CLOSING, adm):
            amt = quarterly_instalment(T, COMMITMENT, q_start, q_end, from_date=INITIAL_CLOSING, to_date=adm)
            if amt:
                fee_eq_rows.append((max(q_start, INITIAL_CLOSING), amt))
        fee_eq = sum((a for _, a in fee_eq_rows), ZERO)

        # Partnership and organisational expenses: the remainder up to USD 120m contributed
        expenses = TOTAL_CONTRIBUTED - invested - fee_total - fee_eq
        if expenses <= 0:
            raise CommandError(f'Demo inputs do not add up: expenses would be {expenses}')
        drawdown(date(2024, 11, 15), Contribution.EXPENSES, pro_rata(date(2024, 11, 15), expenses))

        before = Contribution.objects.filter(partner__fund=fund, funded_date__lt=adm).exclude(
            purpose=Contribution.FEE).order_by('drawdown_no')
        by_dd, dd_meta = {}, {}
        for c in before:
            by_dd.setdefault(c.drawdown_no, {})[c.partner.code] = c.amount_usd
            dd_meta[c.drawdown_no] = c
        statement = equalise(T, commitments={'LP01': COMMITMENT, 'LP02': COMMITMENT},
                             drawdowns=[Drawdown(dd_meta[n].funded_date, by_dd[n]) for n in sorted(by_dd)],
                             new_lp='LP03', new_commitment=COMMITMENT, admission_date=adm,
                             initial_closing=INITIAL_CLOSING)
        assert statement.fee_equalisation == fee_eq
        share = COMMITMENT / (COMMITMENT * 3)
        for n in sorted(by_dd):
            src = dd_meta[n]
            part = rnd(sum(by_dd[n].values(), ZERO) * share)
            refunds = allocate(part, by_dd[n])
            common = dict(drawdown_no=n, notice_date=adm - timedelta(days=10), due_date=adm, funded_date=adm,
                          purpose=src.purpose, investment=src.investment, equalisation=True,
                          deemed_date=src.funded_date, fx_source='USD')
            Contribution.objects.create(partner=lp03, amount_local=part, amount_usd=part, **common)
            for code, amt in refunds.items():
                Contribution.objects.create(partner=lps[code][0], amount_local=-amt, amount_usd=-amt, **common)
        for due, amt in fee_eq_rows:
            Contribution.objects.create(partner=lp03, drawdown_no=dd_no[0] + 1, notice_date=adm - timedelta(days=10),
                                        due_date=adm, funded_date=adm, purpose=Contribution.FEE, amount_local=amt,
                                        amount_usd=amt, equalisation=True, deemed_date=due, fx_source='USD')
        dd_no[0] += 1

        journal = 'EQL-LP03'
        total_in = statement.total_payable_by_new_lp
        LedgerEntry.objects.create(fund=fund, partner=lp03, journal=journal, date=adm, account='Cash — equalisation',
                                   debit=total_in, memo='LP03 equalisation payment (catch-up + interest + fee)',
                                   actor=op)
        for code in ('LP01', 'LP02'):
            LedgerEntry.objects.create(fund=fund, partner=lps[code][0], journal=journal, date=adm,
                                       account='Equalisation payable — existing LPs',
                                       credit=statement.refunds[code] + statement.interest[code],
                                       memo=f'Catch-up refund {statement.refunds[code]} + interest '
                                            f'{statement.interest[code]}', actor=op)
        LedgerEntry.objects.create(fund=fund, journal=journal, date=adm, account='Fee equalisation payable — GP',
                                   credit=statement.fee_equalisation + statement.fee_equalisation_interest,
                                   memo='Management fee from the Initial Closing with interest', actor=op)
        services.log(op, 'closing.subsequent', lp03, fund=fund,
                     after={'catch_up': str(statement.catch_up), 'interest': str(statement.interest_total),
                            'fee_equalisation': str(statement.fee_equalisation),
                            'fee_equalisation_interest': str(statement.fee_equalisation_interest)},
                     reason='LP03 admitted at Subsequent Closing; equalisation per LPA cl. 5, 9.3(c)(ii)')

        total = sum((c.amount_usd for c in Contribution.objects.filter(partner__fund=fund)), ZERO)
        assert total == TOTAL_CONTRIBUTED, total

        # --- Portfolio events and distributions
        inv2 = investments['INV2']
        inv2.status = Investment.WRITTEN_OFF
        inv2.save()
        services.log(op, 'investment.status', inv2, fund=fund, before={'status': 'unrealised'},
                     after={'status': 'written_off'}, reason='Insolvency of portfolio company; valuation committee')
        dist = services.propose_distribution(op, fund, investments['INV1'], date(2026, 9, 30), D('75000000'),
                                             Investment.REALISED)
        services.approve_distribution(ck, dist)
        services.propose_distribution(op, fund, investments['INV4'], date(2026, 10, 30), D('5000000'),
                                      Investment.PARTIAL)

        # --- FAA quarterly cycle (FAA cl. 3.4-3.5, Annex C)
        quarters = [
            # quarter end, inputs, draft, GP approved, final, NAV
            (date(2025, 12, 31), date(2026, 1, 14), date(2026, 1, 26), date(2026, 1, 29), date(2026, 2, 2), '88400000'),
            (date(2026, 3, 31), date(2026, 4, 20), date(2026, 4, 28), date(2026, 5, 5), date(2026, 5, 8), '95200000'),
            (date(2026, 6, 30), date(2026, 7, 15), date(2026, 7, 27), date(2026, 7, 30), date(2026, 8, 4), '112600000'),
            (date(2026, 9, 30), date(2026, 10, 21), date(2026, 11, 3), date(2026, 11, 6), date(2026, 11, 10),
             '79850000'),
        ]
        qs = {}
        for qe, inputs, draft, approved, final, nav in quarters:
            qs[qe] = QuarterClose.objects.create(
                fund=fund, quarter_end=qe, inputs_received=inputs, draft_issued=draft, gp_approved=approved,
                final_issued=final, nav=D(nav), locked=True,
                nav_per_unit=(D(nav) / fund.units_outstanding).quantize(D('0.000001')),
            )
        q1 = qs[date(2026, 3, 31)]
        Event.objects.create(fund=fund, actor=None, action='nav.deemed_approval', object_type='QuarterClose',
                             object_id=str(q1.pk), after={'approved': '2026-05-05', 'kind': 'deemed'},
                             reason='GP silence for 5 Business Days (FAA cl. 3.5(d)); timing only')
        q2 = qs[date(2026, 6, 30)]
        published = q2.nav_per_unit
        NavError.objects.create(quarter=q2, description='Accrued expense omitted from Q2 NAV package',
                                published_nav_per_unit=published,
                                recomputed_nav_per_unit=(published * D('1.0018')).quantize(D('0.000001')),
                                discovered=date(2026, 8, 10), gp_notified=date(2026, 8, 10), corrected=date(2026, 8, 14))
        NavError.objects.create(quarter=q2, description='Rounding on accrued bank interest',
                                published_nav_per_unit=published,
                                recomputed_nav_per_unit=(published * D('1.0005')).quantize(D('0.000001')),
                                discovered=date(2026, 8, 20))

        # --- Valor's own fees (FAA Annex B)
        invoices = [
            ('VAL-2024-001', INITIAL_CLOSING, date(2024, 12, 31), INITIAL_CLOSING, '0', 0, 2, True,
             date(2024, 11, 12)),
            ('VAL-2025-004', SUBSEQUENT_CLOSING, date(2025, 12, 31), SUBSEQUENT_CLOSING, '0', 0, 1, False,
             date(2025, 11, 14)),
            ('VAL-2026-003', date(2026, 7, 1), date(2026, 9, 30), date(2026, 7, 1), '95200000', 3, 0, False,
             date(2026, 8, 5)),
            ('VAL-2026-004', date(2026, 10, 1), date(2026, 12, 31), date(2026, 10, 1), '112600000', 3, 0, False, None),
        ]
        for number, ps, pe, issued, nav, notices, onboard, estab, paid in invoices:
            inv = services.draft_admin_invoice(fund, D(nav), notices, onboardings=onboard,
                                               include_establishment=estab)
            lines = [line for line in inv.lines if D(nav) or line.code != 'ADMIN']
            total_amt = sum((line.amount for line in lines), ZERO)
            AdminInvoice.objects.create(
                fund=fund, number=number, period_start=ps, period_end=pe, issued=issued, approved_nav=D(nav),
                lines=[{'code': line.code, 'description': line.description, 'amount': str(line.amount)}
                       for line in lines],
                total=total_amt, paid=paid,
            )
        self.stdout.write(self.style.SUCCESS(
            f'Demo fund {fund.code}: 3 LPs, contributed {total:,.2f} USD (investments {invested:,.2f}, '
            f'fees {fee_total + fee_eq:,.2f}, expenses {expenses:,.2f}); equalisation LP03 catch-up '
            f'{statement.catch_up:,.2f}; users {", ".join(u for u, _ in USERS)}'))
