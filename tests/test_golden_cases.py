"""Appendix A golden-case acceptance tests (TST-01, TST-04). Every figure must tie out exactly."""

from datetime import date

import pytest

from engine.admin_fees import FAA_SCHEDULE, cpi_escalate, days_overdue, late_interest, quarterly_invoice
from engine.config import VCPEF_TERMS as T
from engine.equalisation import Drawdown, equalise
from engine.fees import annual_fee, apply_offset, fee_basis, quarterly_instalment
from engine.fx import translate_investment, usd_acquisition_cost
from engine.money import D
from engine.sla import FAA_SLA, KZ_HOLIDAYS_2026_2027, BusinessCalendar, assess_nav_error, quarter_timeline
from engine.waterfall import (
    REALISED, UNREALISED, WRITTEN_OFF, LPState, clawback_exposure, distribute,
)


def tiers(result):
    return [(t.to_lp, t.to_cir) for t in result.tiers]


# ---------- Waterfall ----------

def test_gc_wf_01_single_investment():
    st = LPState(funding={'D1': D(100)})
    r = distribute(T, st, D('150.00'), investment_id='D1', statuses={'D1': REALISED}, pref_accrued=D('8.00'))
    assert tiers(r) == [(D('100.00'), 0), (D('8.00'), 0), (0, D('2.00')), (D('32.00'), D('8.00'))]
    assert (r.to_lp, r.to_cir, r.to_gp, r.to_vehicle) == (D('140.00'), D('10.00'), D('6.00'), D('4.00'))


def test_gc_wf_02_written_off_deal_absorbed_before_carry():
    st = LPState(funding={'D1': D(40), 'D2': D(60)}, expenses=D(10))
    r = distribute(T, st, D('130.00'), investment_id='D2',
                   statuses={'D1': WRITTEN_OFF, 'D2': REALISED}, pref_accrued=D('12.00'))
    assert r.hurdle == D('110.00') and r.hurdle_parts == {'A': 60, 'B': 40, 'C': 10}
    assert tiers(r) == [(D('110.00'), 0), (D('12.00'), 0), (0, D('3.00')), (D('4.00'), D('1.00'))]
    assert r.to_cir == D('4.00')
    assert clawback_exposure(T, r.state, D('12.00')) == 0


def test_gc_wf_03_unresolved_deal_flags_clawback_exposure():
    st = LPState(funding={'D1': D(40), 'D2': D(60)}, expenses=D(10))
    r = distribute(T, st, D('130.00'), investment_id='D2',
                   statuses={'D1': UNREALISED, 'D2': REALISED}, pref_accrued=D('12.00'))
    assert r.hurdle_parts['B'] == 0 and r.hurdle == D('70.00')
    assert tiers(r) == [(D('70.00'), 0), (D('12.00'), 0), (0, D('3.00')), (D('36.00'), D('9.00'))]
    assert r.to_cir == D('12.00')
    # if Deal1's 40,00 cost is never recovered, 8,00 of the 12,00 carry is excess
    assert clawback_exposure(T, r.state, D('12.00')) == D('8.00')


def test_gc_wf_04_realised_loss_uses_cost_not_proceeds():
    st = LPState(funding={'D1': D(40), 'D2': D(60)}, expenses=D(10))
    r1 = distribute(T, st, D('20.00'), investment_id='D1',
                    statuses={'D1': REALISED, 'D2': UNREALISED}, pref_accrued=D('12.00'))
    assert tiers(r1)[0] == (D('20.00'), 0) and r1.to_cir == 0
    r2 = distribute(T, r1.state, D('130.00'), investment_id='D2',
                    statuses={'D1': REALISED, 'D2': REALISED}, pref_accrued=D('12.00'))
    assert r2.hurdle == D('110.00')
    assert tiers(r2) == [(D('90.00'), 0), (D('12.00'), 0), (0, D('3.00')), (D('20.00'), D('5.00'))]
    assert r2.state.lp_total == D('142.00') and r2.state.cir_total == D('8.00')
    assert r2.state.roc_paid == D('110.00')
    assert clawback_exposure(T, r2.state, D('12.00')) == 0


def test_gc_wf_05_no_over_distribution():
    st = LPState(funding={'D1': D(40), 'D2': D(60)}, expenses=D(10))
    r = distribute(T, st, D('70.00'), investment_id='D2',
                   statuses={'D1': WRITTEN_OFF, 'D2': REALISED}, pref_accrued=D('12.00'))
    assert tiers(r) == [(D('70.00'), 0), (0, 0), (0, 0), (0, 0)]
    assert r.to_cir == 0
    assert r.hurdle - r.state.roc_paid == D('40.00')   # capital still unrecovered


# ---------- Management fee ----------

def test_gc_fee_01_basis_switch():
    ic = date(2026, 1, 1)
    early = fee_basis(T, date(2026, 4, 1), ic, D('10000000'), D('6000000'))
    late = fee_basis(T, date(2031, 1, 1), ic, D('10000000'), D('6000000'))
    assert early.kind == 'commitment' and late.kind == 'net_invested_cost'
    assert annual_fee(T, early.amount) == D('200000.00')
    assert quarterly_instalment(T, early.amount, date(2026, 4, 1), date(2026, 6, 30)) == D('50000.00')
    assert annual_fee(T, late.amount) == D('120000.00')
    assert quarterly_instalment(T, late.amount, date(2031, 1, 1), date(2031, 3, 31)) == D('30000.00')


def test_gc_fee_02_offset_not_below_zero_and_carry_forward():
    r = apply_offset(T, D('50000.00'), D('60000.00'))
    assert r.net_fee == D('0.00') and r.carried_forward == D('10000.00')
    nxt = apply_offset(T, D('50000.00'), D('0'), r.carried_forward)
    assert nxt.net_fee == D('40000.00') and nxt.carried_forward == 0


# ---------- FX ----------

def test_gc_fx_01_usd_cost_frozen():
    cost = usd_acquisition_cost([(D('4800000000'), D('480.00'))])
    assert cost.usd == D('10000000.00')
    tr = translate_investment(cost, D('4800000000'), D('520.00'))
    assert tr.fair_value_usd == D('9230769.23')     # NAV translated at closing rate (FVTPL)
    assert tr.fee_base_usd == D('10000000.00')       # fee base FX-insulated
    assert tr.cost_usd == D('10000000.00')           # cost basis not retranslated


def test_fx_tranched_weighted_average():
    cost = usd_acquisition_cost([(D('2400000000'), D('480')), (D('2600000000'), D('520'))])
    assert cost.usd == D('10000000.00')
    assert cost.weighted_rate == D('500.000000')


# ---------- Equalisation ----------

def test_gc_eql_01_three_legs():
    st = equalise(
        T, commitments={'A': D('10000000')}, drawdowns=[Drawdown(date(2026, 1, 15), {'A': D('2000000')})],
        new_lp='B', new_commitment=D('10000000'), admission_date=date(2027, 1, 15),
        initial_closing=date(2026, 1, 15),
    )
    assert st.catch_up == D('1000000.00')
    assert st.refunds == {'A': D('1000000.00')}
    assert st.drawn_after == {'A': D('1000000.00'), 'B': D('1000000.00')}
    assert st.interest == {'A': D('80000.00')}
    assert st.fee_equalisation == D('200000.00')
    assert st.fee_equalisation_interest > 0      # separate leg, routed to GP


# ---------- FAA administration fees ----------

def test_gc_adm_01_tier_and_capital_activity():
    inv = quarterly_invoice(FAA_SCHEDULE, approved_nav=D('60000000'), notices=40)
    lines = {line.code: line.amount for line in inv.lines}
    assert inv.tier.annual == D('149000.00')
    assert lines == {'ADMIN': D('37250.00'), 'CAPACT': D('10000.00')}   # no establishment fee


def test_gc_adm_02_cpi_cap_and_late_interest():
    assert cpi_escalate(FAA_SCHEDULE, D('149000.00'), D('0.062')) == D('156450.00')
    assert cpi_escalate(FAA_SCHEDULE, D('149000.00'), D('-0.01')) == D('149000.00')   # upward only
    overdue = days_overdue(FAA_SCHEDULE, date(2026, 4, 1), date(2026, 5, 6))
    assert overdue == 20
    assert late_interest(FAA_SCHEDULE, D('37250.00'), overdue) == D('372.50')
    # 40 days: one full month compounded + 10 days pro rata
    assert late_interest(FAA_SCHEDULE, D('37250.00'), 40) == D('747.79')


# ---------- FAA NAV-error regime and SLA ----------

PLAIN = BusinessCalendar()


def test_gc_nav_01_material_error():
    a = assess_nav_error(PLAIN, FAA_SLA, published=D('100.000000'), recomputed=D('100.180000'),
                         threshold=T.nav_materiality, discovered=date(2026, 4, 6), notified=date(2026, 4, 6))
    assert a.impact == D('0.0018') and a.material
    assert a.notify_gp_by == date(2026, 4, 7)
    assert a.corrective_nav_by == date(2026, 4, 13)
    small = assess_nav_error(PLAIN, FAA_SLA, published=D('100'), recomputed=D('100.05'),
                             threshold=T.nav_materiality, discovered=date(2026, 4, 6))
    assert not small.material and small.corrective_nav_by is None


@pytest.mark.parametrize('cal, draft_due', [
    (PLAIN, date(2026, 5, 4)),
    (BusinessCalendar(KZ_HOLIDAYS_2026_2027), date(2026, 5, 5)),   # 1 May is a holiday
])
def test_gc_sla_01_clock_start_and_deemed_approval(cal, draft_due):
    t = quarter_timeline(cal, FAA_SLA, quarter_end=date(2026, 3, 31), inputs_received=date(2026, 4, 20),
                         draft_issued=date(2026, 4, 28))
    assert t.cut_off == date(2026, 4, 15)
    assert t.clock_start == date(2026, 4, 20) and t.inputs_late
    assert t.draft_nav_due == draft_due
    assert t.approval_kind == 'deemed'
    assert t.approved == cal.add(date(2026, 4, 28), 5)
    assert t.final_nav_due == cal.add(t.approved, 3)
