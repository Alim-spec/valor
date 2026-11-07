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
    assert t['commitment'] == D('60000000')
    assert t['commitment'] - t['paid_in'] == t['unfunded']
    for a in s['accounts']:
        assert a.pnl == a.nav + a.distributed - a.paid_in


def test_equalisation_preview(demo):
    st = services.equalisation_preview(demo, 'LP07', D('10000000'), date(2026, 11, 7))
    assert st.catch_up > 0 and st.fee_equalisation > 0
    assert st.drawn_after['LP07'] == st.catch_up == sum(st.refunds.values())
    # after rebalancing every LP is drawn pro rata to Commitment (60m existing + 10m new)
    assert abs(st.drawn_after['LP01'] / D(20) - st.drawn_after['LP07'] / D(10)) < D('0.01')
