"""Preferred return and IRR (PR-01, ACC-02).

Compounding is annual with an actual/365 day count, so a flow t days before the as-of date
grows by (1 + r) ** (t / 365). Pure Decimal arithmetic: no floats.
"""

from datetime import date
from decimal import Decimal, localcontext

from .money import ZERO, D


def growth(rate: Decimal, start: date, end: date, day_count: int = 365) -> Decimal:
    days = (end - start).days
    if days == 0:
        return D(1)
    return (D(1) + D(rate)) ** (D(days) / D(day_count))


def preferred_return_outstanding(
    contributions: list[tuple[date, Decimal]],
    distributions: list[tuple[date, Decimal]],
    unreturned_capital: Decimal,
    as_of: date,
    rate: Decimal = D('0.08'),
    day_count: int = 365,
) -> Decimal:
    """Amount X such that paying (unreturned capital + X) at as_of gives the LP exactly `rate` IRR.

    `contributions` must already exclude recallable contributions and equalisation payments,
    and use deemed (Initial Closing) dates for Additional LPs (cl. 8.1).
    """
    fv_in = sum((D(a) * growth(rate, d, as_of, day_count) for d, a in contributions), ZERO)
    fv_out = sum((D(a) * growth(rate, d, as_of, day_count) for d, a in distributions), ZERO)
    return max(fv_in - fv_out - D(unreturned_capital), ZERO)


def npv(rate: Decimal, flows: list[tuple[date, Decimal]], day_count: int = 365) -> Decimal:
    t0 = min(d for d, _ in flows)
    return sum((D(a) / growth(rate, t0, d, day_count) for d, a in flows), ZERO)


IRR_PRECISION = 18          # significant digits while searching; the result is reported to 6 dp
IRR_TOLERANCE = D('1e-9')   # stop once the bracket is narrower than this


def xirr(flows: list[tuple[date, Decimal]], day_count: int = 365) -> Decimal | None:
    """IRR of dated flows (contributions negative, distributions/NAV positive) by bisection.

    IRR is a reported performance metric, never a bookable amount, so the search runs at
    IRR_PRECISION digits and stops at IRR_TOLERANCE. Still deterministic: fixed bounds, fixed
    precision, fixed stopping rule. Flows on the same date are netted first. None if no sign change.
    """
    netted: dict[date, Decimal] = {}
    for d, a in flows:
        netted[d] = netted.get(d, ZERO) + D(a)
    flows = sorted((d, a) for d, a in netted.items() if a)
    if not flows or all(a >= 0 for _, a in flows) or all(a <= 0 for _, a in flows):
        return None
    with localcontext() as ctx:
        ctx.prec = IRR_PRECISION
        lo, hi = D('-0.99'), D('10')
        f_lo = npv(lo, flows, day_count)
        if f_lo * npv(hi, flows, day_count) > 0:
            return None
        while hi - lo > IRR_TOLERANCE:
            mid = (lo + hi) / 2
            f_mid = npv(mid, flows, day_count)
            if f_mid == 0:
                lo = hi = mid
                break
            if (f_mid > 0) == (f_lo > 0):
                lo, f_lo = mid, f_mid
            else:
                hi = mid
        result = (lo + hi) / 2
    return result.quantize(D('0.000001'))
