"""Management fee and Fee Income offset (FEE-01..03, LPA cl. 9.3-9.4).

Convention: an instalment for a full quarter is annual / 4. A quarter with fewer chargeable
days (stub, admission mid-quarter, Suspension Period) is pro-rated by chargeable days / days
in the quarter. Daily accrual for the ledger is annual x days / 365 (actual/365).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .config import FundTerms
from .money import ZERO, D, rnd


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year + years, day=28)


def quarter_bounds(d: date) -> tuple[date, date]:
    """First day and last day of the calendar quarter containing d."""
    q_start = date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
    nxt = date(q_start.year + (q_start.month + 2) // 12, (q_start.month + 2) % 12 + 1, 1)
    return q_start, nxt - timedelta(days=1)


def quarters_between(start: date, end: date) -> list[tuple[date, date]]:
    """Calendar quarters overlapping [start, end)."""
    out = []
    cur = quarter_bounds(start)[0]
    while cur < end:
        q = quarter_bounds(cur)
        out.append(q)
        cur = q[1] + timedelta(days=1)
    return out


@dataclass(frozen=True)
class FeeBasis:
    amount: Decimal
    kind: str          # 'commitment' | 'net_invested_cost'
    switch_date: date


def fee_basis(terms: FundTerms, period_start: date, initial_closing: date,
              commitment: Decimal, net_invested_cost: Decimal) -> FeeBasis:
    """Life-cycle switch (FEE-01): Commitment until the 5th anniversary of the Initial Closing,
    then Contributions funding Acquisition Costs less the cost of Investments disposed of or
    fully written off as at the most recent quarter-end (net invested cost, USD-frozen)."""
    switch = add_years(initial_closing, terms.fee_basis_switch_years)
    if period_start < switch:
        return FeeBasis(D(commitment), 'commitment', switch)
    return FeeBasis(D(net_invested_cost), 'net_invested_cost', switch)


def annual_fee(terms: FundTerms, basis: Decimal) -> Decimal:
    return rnd(D(basis) * terms.mgmt_fee_rate, terms.rounding_scale)


def chargeable_days(q_start: date, q_end: date, from_date: date | None = None,
                    suspensions: list[tuple[date, date]] = (), to_date: date | None = None) -> int:
    """Days in [q_start, q_end] on or after from_date, before to_date (exclusive)
    and outside every Suspension Period."""
    start = max(q_start, from_date) if from_date else q_start
    end = min(q_end, to_date - timedelta(days=1)) if to_date else q_end
    days = 0
    d = start
    while d <= end:
        if not any(s <= d <= e for s, e in suspensions):
            days += 1
        d += timedelta(days=1)
    return days


def quarterly_instalment(terms: FundTerms, basis: Decimal, q_start: date, q_end: date,
                         from_date: date | None = None,
                         suspensions: list[tuple[date, date]] = (),
                         to_date: date | None = None) -> Decimal:
    """Fee payable quarterly in advance for one quarter (FEE-02)."""
    annual = D(basis) * terms.mgmt_fee_rate
    q_days = (q_end - q_start).days + 1
    days = chargeable_days(q_start, q_end, from_date, suspensions, to_date)
    if days == q_days:
        return rnd(annual / 4, terms.rounding_scale)
    return rnd(annual / 4 * D(days) / D(q_days), terms.rounding_scale)


def daily_accrual(terms: FundTerms, basis: Decimal, days: int) -> Decimal:
    return rnd(D(basis) * terms.mgmt_fee_rate * D(days) / D(terms.day_count), terms.rounding_scale)


@dataclass(frozen=True)
class OffsetResult:
    gross_fee: Decimal
    available: Decimal       # this LP's Fee Income share + carried forward
    offset_used: Decimal
    net_fee: Decimal
    carried_forward: Decimal


def apply_offset(terms: FundTerms, gross_fee: Decimal, fee_income_share: Decimal,
                 carried_forward: Decimal = ZERO) -> OffsetResult:
    """Fee Income offset (FEE-03, cl. 9.4): reduce the instalment, never below zero;
    carry any excess forward to the next instalment."""
    gross_fee = D(gross_fee)
    available = rnd(D(fee_income_share) * terms.fee_offset_rate, terms.rounding_scale) + D(carried_forward)
    used = min(gross_fee, available)
    return OffsetResult(gross_fee, available, used, gross_fee - used, available - used)


def fee_income_shares(fee_income: Decimal, commitments: dict) -> dict:
    """Each LP's pro-rata share of Fee Income by Commitment."""
    from .money import allocate
    return allocate(D(fee_income), commitments)
