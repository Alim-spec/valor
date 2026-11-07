"""Equalisation on a Subsequent Closing (EQL-01..02, LPA cl. 5, 9.3(c)(ii)).

Places every partner as if all had been admitted at the Initial Closing, with three legs that
are computed and routed separately:
  1. catch-up contribution — Additional LP buys in at cost; refunded to earlier LPs;
  2. equalisation interest on that catch-up — to earlier LPs;
  3. management fee equalisation — Additional LP's fee from Initial Closing, to the GP, with interest.
Interest is simple, actual/365, from each funding (or fee) date to the admission date.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .config import FundTerms
from .fees import quarterly_instalment, quarters_between
from .money import ZERO, D, allocate, rnd


@dataclass(frozen=True)
class Drawdown:
    date: date
    funded: dict            # lp -> amount funded


@dataclass(frozen=True)
class EqualisationStatement:
    admission_date: date
    new_lp: str
    catch_up: Decimal
    refunds: dict                       # existing lp -> refund of catch-up
    interest: dict                      # existing lp -> equalisation interest
    fee_equalisation: Decimal
    fee_equalisation_interest: Decimal
    drawn_after: dict                   # lp -> drawn after rebalancing
    lines: list = field(default_factory=list)  # human-readable audit trail

    @property
    def interest_total(self):
        return sum(self.interest.values(), ZERO)

    @property
    def total_payable_by_new_lp(self):
        return self.catch_up + self.interest_total + self.fee_equalisation + self.fee_equalisation_interest


def simple_interest(amount: Decimal, rate: Decimal, start: date, end: date, day_count=365) -> Decimal:
    return D(amount) * D(rate) * D((end - start).days) / D(day_count)


def equalise(terms: FundTerms, *, commitments: dict, drawdowns: list[Drawdown], new_lp: str,
             new_commitment: Decimal, admission_date: date, initial_closing: date) -> EqualisationStatement:
    scale = terms.rounding_scale
    new_commitment = D(new_commitment)
    total_commitment = sum(commitments.values(), ZERO) + new_commitment
    share = new_commitment / total_commitment
    lines = []

    catch_up = ZERO
    refunds = {lp: ZERO for lp in commitments}
    interest_raw = {lp: ZERO for lp in commitments}
    for dd in drawdowns:
        drawn = sum(dd.funded.values(), ZERO)
        part = rnd(drawn * share, scale)
        catch_up += part
        # refunded to earlier LPs pro rata to what each funded in that drawdown
        per_lp = allocate(part, dd.funded, scale)
        for lp, amt in per_lp.items():
            refunds[lp] += amt
            i = simple_interest(amt, terms.equalisation_interest_rate, dd.date, admission_date, terms.day_count)
            interest_raw[lp] += i
            lines.append(f'{dd.date}: catch-up {amt} -> {lp}; interest {terms.equalisation_interest_rate} x '
                         f'{(admission_date - dd.date).days}/{terms.day_count} = {rnd(i, scale)}')
    interest = {lp: rnd(v, scale) for lp, v in interest_raw.items()}

    fee_eq = ZERO
    fee_int = ZERO
    for q_start, q_end in quarters_between(initial_closing, admission_date):
        # fee the Additional LP would have paid from the Initial Closing up to its admission
        inst = quarterly_instalment(terms, new_commitment, q_start, q_end,
                                    from_date=initial_closing, to_date=admission_date)
        if not inst:
            continue
        due = max(q_start, initial_closing)
        fee_eq += inst
        fee_int += simple_interest(inst, terms.fee_equalisation_interest_rate, due, admission_date, terms.day_count)
        lines.append(f'fee {q_start}..{q_end}: {inst} due {due}')

    drawn_total = sum((sum(dd.funded.values(), ZERO) for dd in drawdowns), ZERO)
    after = {}
    for lp, c in commitments.items():
        funded = sum((dd.funded.get(lp, ZERO) for dd in drawdowns), ZERO)
        after[lp] = funded - refunds[lp]
    after[new_lp] = catch_up
    assert sum(after.values(), ZERO) == drawn_total

    return EqualisationStatement(
        admission_date=admission_date, new_lp=new_lp, catch_up=catch_up, refunds=refunds,
        interest=interest, fee_equalisation=fee_eq, fee_equalisation_interest=rnd(fee_int, scale),
        drawn_after=after, lines=lines,
    )
