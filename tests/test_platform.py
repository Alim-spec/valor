"""Platform controls (TST-03): maker-checker, append-only ledger and audit, access, pages, waterfall wiring."""

from datetime import date

import pytest
from django.core.management import call_command
from django.urls import reverse

from engine.money import D
from funds import services
from funds.models import Distribution, Event, Fund, Investment, LedgerEntry

pytestmark = pytest.mark.django_db

PAGES = ['dashboard', 'register', 'capital_accounts', 'investments', 'distributions', 'fees', 'equalisation',
         'service_levels', 'billing', 'acceptance', 'terms', 'audit']


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setenv('DEMO_PASSWORD', 'test-password-123')
    call_command('seed_demo', verbosity=0)
    return Fund.objects.get()


@pytest.fixture
def users(demo, django_user_model):
    return {u.username: u for u in django_user_model.objects.all()}


def test_pages_require_login(client, demo):
    for name in PAGES:
        r = client.get(reverse(name))
        assert r.status_code == 302 and reverse('login') in r['Location'], name
    assert client.get(reverse('healthz')).status_code == 200


@pytest.mark.parametrize('lang', ['en', 'ru'])
def test_all_pages_render(client, users, lang):
    client.force_login(users['operator'])
    for name in PAGES:
        r = client.get(reverse(name), {'lang': lang})
        assert r.status_code == 200, name
    assert 'Обзор' in client.get(reverse('dashboard'), {'lang': 'ru'}).content.decode()


def test_seeded_distribution_follows_waterfall(demo):
    dist = Distribution.objects.get(state=Distribution.APPROVED)
    t = dist.result['totals']
    assert D(t['lp']) + D(t['cir']) == dist.gross_amount
    assert D(t['gp']) + D(t['vehicle']) == D(t['cir'])
    # hurdle for each LP includes the written-off INV2 cost (limb B) and fees/expenses (limb C)
    for row in dist.result['partners'].values():
        assert D(row['hurdle_parts']['B']) > 0 and D(row['hurdle_parts']['C']) > 0
    lines = LedgerEntry.objects.filter(journal=f'DIST-{dist.pk}')
    assert sum(e.debit for e in lines) == sum(e.credit for e in lines)


def test_maker_cannot_approve(demo, users):
    dist = Distribution.objects.get(state=Distribution.DRAFT)
    with pytest.raises(services.SegregationOfDuties):
        services.approve_distribution(users['operator'], dist)
    services.approve_distribution(users['checker'], dist)
    dist.refresh_from_db()
    assert dist.state == Distribution.APPROVED
    assert dist.investment.status == Investment.PARTIAL


def test_operator_cannot_reach_approval_endpoint(client, demo, users):
    dist = Distribution.objects.get(state=Distribution.DRAFT)
    client.force_login(users['operator'])
    r = client.post(reverse('distribution_decide', args=[dist.pk]), {'action': 'approve'})
    assert r.status_code == 403


def test_ledger_and_events_are_append_only(demo):
    entry = LedgerEntry.objects.first()
    entry.memo = 'changed'
    with pytest.raises(PermissionError):
        entry.save()
    with pytest.raises(PermissionError):
        entry.delete()
    with pytest.raises(PermissionError):
        Event.objects.first().delete()


def test_distribution_preview_and_submit(client, demo, users):
    client.force_login(users['operator'])
    inv = Investment.objects.get(code='INV3')
    data = {'investment': inv.pk, 'date': '2026-11-01', 'gross': '15000000', 'status': Investment.REALISED}
    r = client.post(reverse('distributions'), {**data, 'action': 'preview'})
    assert r.status_code == 200 and Distribution.objects.filter(investment=inv).count() == 0
    r = client.post(reverse('distributions'), {**data, 'action': 'submit'})
    assert r.status_code == 302
    assert Distribution.objects.get(investment=inv).state == Distribution.DRAFT


def test_capital_accounts_tie_out(demo):
    s = services.fund_summary(demo, date(2026, 11, 7))
    t = s['totals']
    assert t['commitment'] == D('150000000')
    assert t['paid_in'] == D('120000000')
    assert t['commitment'] - t['paid_in'] == t['unfunded']
    for a in s['accounts']:
        assert a.pnl == a.nav + a.distributed - a.paid_in


def test_equalisation_preview(demo):
    st = services.equalisation_preview(demo, 'LP07', D('10000000'), date(2026, 11, 7))
    assert st.catch_up > 0 and st.fee_equalisation > 0
    assert st.drawn_after['LP07'] == st.catch_up == sum(st.refunds.values())
    # after rebalancing every LP is drawn pro rata to Commitment (3 x 50m existing + 10m new)
    assert abs(st.drawn_after['LP01'] / D(50) - st.drawn_after['LP07'] / D(10)) < D('0.01')


def _csrf(client, url):
    client.get(url)
    return client.cookies['valor_csrf'].value


def test_double_submitted_login_lands_in_dashboard(demo):
    """Login form sent twice (double click): the second POST carries a rotated-out token.
    The user must end up in the workbench, not on a bare 403 page."""
    from django.test import Client
    c = Client(enforce_csrf_checks=True)
    url = reverse('login') + '?next=' + reverse('dashboard')
    token = _csrf(c, url)
    form = {'csrfmiddlewaretoken': token, 'username': 'operator', 'password': 'test-password-123'}
    assert c.post(url, form).status_code == 302
    r = c.post(url, form)                      # same, now stale, token
    assert r.status_code == 302 and r['Location'] == reverse('dashboard')
    assert c.get(r['Location']).status_code == 200


def test_stale_form_when_signed_out_returns_to_login(demo):
    from django.test import Client
    c = Client(enforce_csrf_checks=True)
    url = reverse('login')
    _csrf(c, url)
    r = c.post(url, {'csrfmiddlewaretoken': 'x' * 64, 'username': 'operator', 'password': 'test-password-123'})
    assert r.status_code == 302 and r['Location'].startswith(url)
    page = c.get(r['Location']).content.decode()
    assert 'out of date' in page


def test_vcpef_demo_shape(demo):
    """Valor's brief: VCPEF, 3 LPs x USD 50m, two at the start, one a year later, USD 120m contributed
    by the second anniversary, USD only."""
    from funds.models import Contribution, Partner
    assert demo.code == 'VCPEF'
    lps = list(demo.partners.filter(kind=Partner.LP).order_by('code'))
    assert [p.code for p in lps] == ['LP01', 'LP02', 'LP03']
    admitted = [p.commitments.get().date for p in lps]
    assert admitted == [date(2024, 11, 1), date(2024, 11, 1), date(2025, 11, 1)]
    assert all(p.commitments.get().amount == D('50000000') for p in lps)
    contributed = Contribution.objects.filter(partner__fund=demo, funded_date__lte=date(2026, 11, 1))
    assert sum(c.amount_usd for c in contributed) == D('120000000')
    assert set(Contribution.objects.values_list('currency', flat=True)) == {'USD'}


def test_late_lp_is_equalised_as_if_admitted_at_initial_closing(demo):
    """After equalisation all three LPs hold the same position, and LP03's catch-up counts
    from the original drawdown dates (deemed), so its preferred return is in line with the others."""
    from funds.models import LedgerEntry
    accounts = {a.partner.code: a for a in services.capital_accounts(demo, date(2026, 11, 14))}
    assert accounts['LP01'].paid_in_investments - accounts['LP03'].paid_in_investments in (D('-0.01'), 0, D('0.01'))
    assert accounts['LP03'].unfunded == accounts['LP01'].unfunded == D('10000000.00')
    assert abs(accounts['LP03'].pref_accrued - accounts['LP01'].pref_accrued) < D('5000')
    lp03 = demo.partners.get(code='LP03')
    eq = lp03.contributions.filter(equalisation=True)
    assert eq.exists() and all(c.deemed_date < c.funded_date for c in eq)
    lines = LedgerEntry.objects.filter(journal='EQL-LP03')
    assert sum(e.debit for e in lines) == sum(e.credit for e in lines) > 0
