"""Fill an empty database with a fictitious demo fund (CACPEF-style economics).

Users get the password from DEMO_PASSWORD (never printed, never stored in git).
Run once:  python manage.py seed_demo
"""

import os
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from engine.config import CACPEF_TERMS
from engine.fees import quarterly_instalment, quarters_between
from engine.money import D, allocate

from funds import services
from funds.models import (
    AdminInvoice, Commitment, Contribution, Event, Fund, FundTermsVersion, Investment, NavError, Partner,
    QuarterClose,
)

GROUPS = ['Administrator', 'Approver', 'GP', 'Auditor']
USERS = [('operator', 'Administrator'), ('checker', 'Approver'), ('gp', 'GP'), ('auditor', 'Auditor')]

LPS = [
    ('LP01', 'Steppe Pension Fund (demo)', 'KZ', '20000000', 'Active NFFE / CRS: Financial institution'),
    ('LP02', 'Altyn Family Office (demo)', 'KZ', '15000000', 'Passive NFFE'),
    ('LP03', 'Caspian Holdings (demo)', 'AE', '10000000', 'Active NFFE', 'MFN; excused from tobacco investments'),
    ('LP04', 'Nomad Ventures (demo)', 'KZ', '5000000', 'Active NFFE'),
    ('LP05', 'Ural Insurance (demo)', 'KZ', '10000000', 'CRS: Financial institution'),
]


class Command(BaseCommand):
    help = 'Create a demo fund with partners, drawdowns, investments, a distribution and FAA records'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Seed even if a fund already exists')

    @transaction.atomic
    def handle(self, *args, **opts):
        if Fund.objects.exists() and not opts['force']:
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

        ic = date(2025, 10, 1)
        fund = Fund.objects.create(
            code='CACPEF', name='CACPEF — Demo Private Equity Fund', initial_closing=ic,
            lpa_reference='LPA dated 01.10.2025 (demo)', target_size=D('100000000'), min_commitment=D('1000000'),
            units_outstanding=D('600000'),
        )
        FundTermsVersion.objects.create(fund=fund, version=1, terms=CACPEF_TERMS.as_dict(), effective_from=ic,
                                        reason='Initial configuration from LPA cl. 8-9', created_by=op,
                                        approved_by=ck)

        Partner.objects.create(fund=fund, code='GP', name='CACPEF GP Ltd (demo)', kind=Partner.GP, country='OM',
                               kyc_status=Partner.KYC_APPROVED)
        Partner.objects.create(fund=fund, code='CIV', name='CACPEF Carry LP (demo)', kind=Partner.CIV, country='KZ',
                               kyc_status=Partner.KYC_APPROVED)
        lps = []
        for row in LPS:
            code, name, country, amount, fatca = row[:5]
            p = Partner.objects.create(fund=fund, code=code, name=name, country=country, fatca_crs=fatca,
                                       kyc_status=Partner.KYC_APPROVED, side_letter=row[5] if len(row) > 5 else '',
                                       email=f'{code.lower()}@example.com')
            Commitment.objects.create(partner=p, date=ic, amount=D(amount))
            lps.append(p)
        Partner.objects.create(fund=fund, code='LP06', name='Silk Road Capital (demo)', country='UZ',
                               kyc_status=Partner.KYC_SCREENED, risk_rating='medium',
                               fatca_crs='Pending self-certification')
        weights = {p.pk: p.commitments.first().amount for p in lps}

        dd = [0]

        def drawdown(on, purpose, usd, investment=None, currency='USD', rate=D('1'), source=''):
            dd[0] += 1
            for p in lps:
                part = allocate(D(usd), weights)[p.pk]
                Contribution.objects.create(
                    partner=p, drawdown_no=dd[0], notice_date=on, due_date=on, funded_date=on, purpose=purpose,
                    investment=investment, currency=currency, amount_local=part * rate, fx_rate=rate,
                    fx_source=source, amount_usd=part,
                )

        # Management fee, quarterly in advance, called with each quarter
        for q_start, q_end in quarters_between(ic, date(2026, 12, 31)):
            total = sum((quarterly_instalment(CACPEF_TERMS, w, q_start, q_end) for w in weights.values()), D(0))
            drawdown(q_start + (date(2025, 10, 15) - date(2025, 10, 1)), Contribution.FEE, total)
        drawdown(date(2025, 10, 15), Contribution.EXPENSES, '450000')

        inv1 = Investment.objects.create(fund=fund, code='INV1', name='Steppe Logistics', sector='Logistics',
                                         holding_entity='CACPEF Holdco 1', acquired=date(2025, 11, 20),
                                         fair_value_local=D('12000000'))
        drawdown(date(2025, 11, 20), Contribution.INVESTMENT, '12000000', inv1)
        inv2 = Investment.objects.create(fund=fund, code='INV2', name='AgroTech Kazakhstan', sector='Agriculture',
                                         holding_entity='CACPEF Holdco 2', acquired=date(2026, 1, 20))
        drawdown(date(2026, 1, 20), Contribution.INVESTMENT, '4000000', inv2)
        inv3 = Investment.objects.create(fund=fund, code='INV3', name='Qazaq Fintech', sector='Financial services',
                                         holding_entity='CACPEF Holdco 3', currency='KZT', acquired=date(2026, 3, 10),
                                         fair_value_local=D('5600000000'), closing_rate=D('520'))
        drawdown(date(2026, 3, 10), Contribution.INVESTMENT, '10000000', inv3, 'KZT', D('480'),
                 'Dealt rate, Halyk Bank FX desk (demo)')
        inv4 = Investment.objects.create(fund=fund, code='INV4', name='Caspian Healthcare', sector='Healthcare',
                                         holding_entity='CACPEF Holdco 4', acquired=date(2026, 6, 15),
                                         fair_value_local=D('9500000'))
        drawdown(date(2026, 6, 15), Contribution.INVESTMENT, '8000000', inv4)

        inv2.status = Investment.WRITTEN_OFF
        inv2.save()
        services.log(op, 'investment.status', inv2, fund=fund, before={'status': 'unrealised'},
                     after={'status': 'written_off'}, reason='Insolvency of portfolio company; valuation committee')

        dist = services.propose_distribution(op, fund, inv1, date(2026, 9, 30), D('21000000'), Investment.REALISED)
        services.approve_distribution(ck, dist)
        services.propose_distribution(op, fund, inv4, date(2026, 10, 30), D('3000000'), Investment.PARTIAL)

        quarters = [
            (date(2025, 12, 31), date(2026, 1, 14), date(2026, 1, 26), date(2026, 1, 29), date(2026, 2, 2),
             '16450000', True),
            (date(2026, 3, 31), date(2026, 4, 20), date(2026, 4, 28), None, date(2026, 5, 8), '31800000', True),
            (date(2026, 6, 30), date(2026, 7, 15), date(2026, 7, 27), date(2026, 7, 30), date(2026, 8, 4),
             '41250000', True),
            (date(2026, 9, 30), date(2026, 10, 21), date(2026, 11, 3), None, None, None, False),
        ]
        qs = {}
        for qe, inputs, draft, approved, final, nav, locked in quarters:
            qs[qe] = QuarterClose.objects.create(
                fund=fund, quarter_end=qe, inputs_received=inputs, draft_issued=draft, gp_approved=approved,
                final_issued=final, nav=D(nav) if nav else None,
                nav_per_unit=(D(nav) / fund.units_outstanding).quantize(D('0.000001')) if nav else None,
                locked=locked,
            )
        q1 = qs[date(2026, 3, 31)]
        q1.gp_approved = date(2026, 5, 5)   # deemed approval recorded (5 BD of GP silence)
        q1.save()
        Event.objects.create(fund=fund, actor=None, action='nav.deemed_approval', object_type='QuarterClose',
                             object_id=str(q1.pk), after={'approved': '2026-05-05', 'kind': 'deemed'},
                             reason='GP silence for 5 Business Days (FAA cl. 3.5(d)); timing only')
        q2 = qs[date(2026, 6, 30)]
        NavError.objects.create(quarter=q2, description='Accrued expense omitted from Q2 NAV package',
                                published_nav_per_unit=D('68.750000'), recomputed_nav_per_unit=D('68.873750'),
                                discovered=date(2026, 8, 10), gp_notified=date(2026, 8, 10),
                                corrected=date(2026, 8, 14))
        NavError.objects.create(quarter=q2, description='FX rounding on KZT cash balance',
                                published_nav_per_unit=D('68.750000'), recomputed_nav_per_unit=D('68.784375'),
                                discovered=date(2026, 8, 20))

        n = len(lps)
        invoices = [
            ('VAL-2025-001', ic, date(2025, 12, 31), date(2025, 10, 1), '0', None, True, date(2025, 10, 10)),
            ('VAL-2026-003', date(2026, 7, 1), date(2026, 9, 30), date(2026, 7, 1), '41250000', n, False,
             date(2026, 8, 5)),
            ('VAL-2026-004', date(2026, 10, 1), date(2026, 12, 31), date(2026, 10, 1), '41250000', n, False, None),
        ]
        for number, ps, pe, issued, nav, notices, estab, paid in invoices:
            inv = services.draft_admin_invoice(fund, D(nav), notices or 0, onboardings=n if estab else 0,
                                               include_establishment=estab)
            AdminInvoice.objects.create(
                fund=fund, number=number, period_start=ps, period_end=pe, issued=issued, approved_nav=D(nav),
                lines=[{'code': line.code, 'description': line.description, 'amount': str(line.amount)}
                       for line in inv.lines],
                total=inv.total, paid=paid,
            )
        self.stdout.write(self.style.SUCCESS(
            f'Demo fund {fund.code} created; users: {", ".join(u for u, _ in USERS)} (password from DEMO_PASSWORD)'))
