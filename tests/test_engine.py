"""Engine behaviour beyond the golden cases."""

from datetime import date

import pytest

from engine.config import CACPEF_TERMS as T, FundTerms
from engine.fees import quarterly_instalment
from engine.irr import preferred_return_outstanding, xirr
from engine.money import D, allocate, fmt
from engine.waterfall import REALISED, LPState, apportion_receipts, distribute


def test_float_rejected():
    with pytest.raises(TypeError):
        D(0.1)


def test_allocate_sums_exactly():
    out = allocate(D('100.00'), {'a': D(1), 'b': D(1), 'c': D(1)})
    assert sum(out.values()) == D('100.00')
    assert sorted(out.values()) == [D('33.33'), D('33.33'), D('33.34')]


def test_fmt_space_grouped_comma_decimal():
    assert fmt(D('1234567.891')) == '1 234 567,89'
    assert fmt(D('-5')) == '-5,00'


def test_pref_one_year_8pct():
    x = preferred_return_outstanding([(date(2026, 1, 1), D(100))], [], D(100), date(2027, 1, 1))
    assert x.quantize(D('0.01')) == D('8.00')


def test_pref_reduced_by_earlier_distribution():
    x = preferred_return_outstanding(
        [(date(2026, 1, 1), D(100))], [(date(2026, 1, 1), D(50))], D(50), date(2027, 1, 1))
    assert x.quantize(D('0.01')) == D('4.00')


def test_xirr_simple():
    assert xirr([(date(2026, 1, 1), D(-100)), (date(2027, 1, 1), D(110))]) == D('0.100000')


def test_receipts_apportioned_by_funding():
    out = apportion_receipts(D('300.00'), {'A': D(200), 'B': D(100)})
    assert out == {'A': D('200.00'), 'B': D('100.00')}


def test_partial_catch_up_rate():
    terms = FundTerms(catch_up_rate=D('0.8'))
    r = distribute(terms, LPState(funding={'D1': D(100)}), D('150'), investment_id='D1',
                   statuses={'D1': REALISED}, pref_accrued=D(8))
    # 80% catch-up: flow X with 0.8X = 0.2(8+X) -> X = 2.6667
    assert r.tier(3).total == D('2.67') and r.tier(3).to_cir == D('2.13')   # 20% of 8 + 2,67
    assert r.to_cir == D('10.00') and r.to_lp == D('140.00')


def test_whole_fund_waterfall_returns_all_capital_first():
    terms = FundTerms(waterfall_type='whole_fund')
    st = LPState(funding={'D1': D(40), 'D2': D(60)}, expenses=D(10))
    r = distribute(terms, st, D('130'), investment_id='D2', statuses={}, pref_accrued=D(12))
    assert r.hurdle == D('110')


def test_terms_round_trip():
    assert FundTerms.from_dict(T.as_dict()) == T
    with pytest.raises(ValueError):
        FundTerms.from_dict({'nonsense': 1})


def test_stub_quarter_and_suspension():
    full = quarterly_instalment(T, D('10000000'), date(2026, 4, 1), date(2026, 6, 30))
    stub = quarterly_instalment(T, D('10000000'), date(2026, 4, 1), date(2026, 6, 30), from_date=date(2026, 5, 16))
    susp = quarterly_instalment(T, D('10000000'), date(2026, 4, 1), date(2026, 6, 30),
                                suspensions=[(date(2026, 4, 1), date(2026, 6, 30))])
    assert full == D('50000.00')
    assert stub == D('25274.73')    # 46 of 91 days
    assert susp == D('0.00')


def test_determinism():
    args = dict(investment_id='D1', statuses={'D1': REALISED}, pref_accrued=D(8))
    a = distribute(T, LPState(funding={'D1': D(100)}), D('150'), **args)
    b = distribute(T, LPState(funding={'D1': D(100)}), D('150'), **args)
    assert a == b


def test_live_acceptance_page_cases_all_pass():
    from funds.golden import run_all
    failed = [r for r in run_all() if not r['passed']]
    assert not failed, failed
