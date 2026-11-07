"""Distribution waterfall engine (WF-01..04, CI-01..02).

CACPEF (cl. 8.2) is a per-LP, per-Investment waterfall:
  1. Return of capital — 100% to the LP until cumulative tier-1 equals, without duplication,
     its Contributions funding (A) the realised Investment, (B) every other Investment fully
     realised or fully written off, (C) Partnership/Organisational Expenses incl. the fee.
  2. Preferred return — 100% to the LP until it has its 8% IRR.
  3. Catch-up — to the Carried Interest Recipients (CIR) until they hold carry% of tiers 2+3.
  4. Residual — (1 - carry)% LP / carry% CIR.
CIR amounts split GP / Carried Interest Vehicle (60/40, cl. 8.2(c)).

"Without duplication" is achieved by making the tier-1 hurdle a cumulative set of costs and
paying only the difference against tier-1 already paid.
"""

from dataclasses import dataclass, field, replace
from decimal import Decimal

from .config import DEAL_BY_DEAL, WHOLE_FUND, FundTerms
from .money import ZERO, D, allocate, rnd

REALISED = 'realised'
WRITTEN_OFF = 'written_off'
UNREALISED = 'unrealised'
RESOLVED = {REALISED, WRITTEN_OFF}


@dataclass(frozen=True)
class LPState:
    """Cumulative waterfall position of one LP. Immutable: every step returns a new state."""
    funding: dict = field(default_factory=dict)   # investment_id -> contributions funding its cost
    expenses: Decimal = ZERO                      # contributions for expenses incl. fee (limb C)
    roc_paid: Decimal = ZERO
    pref_paid: Decimal = ZERO
    catch_up_flow: Decimal = ZERO                 # total that has passed through tier 3
    catch_up_cir: Decimal = ZERO                  # CIR part of that flow
    lp_total: Decimal = ZERO
    cir_total: Decimal = ZERO


@dataclass(frozen=True)
class TierLine:
    tier: int
    name: str
    clause: str
    to_lp: Decimal
    to_cir: Decimal

    @property
    def total(self):
        return self.to_lp + self.to_cir


@dataclass(frozen=True)
class LPDistribution:
    amount: Decimal
    hurdle: Decimal
    hurdle_parts: dict
    tiers: tuple
    to_lp: Decimal
    to_cir: Decimal
    to_gp: Decimal
    to_vehicle: Decimal
    state: LPState

    def tier(self, n: int) -> TierLine:
        return self.tiers[n - 1]


def roc_hurdle(terms: FundTerms, state: LPState, investment_id, statuses: dict) -> tuple[Decimal, dict]:
    """Cumulative return-of-capital hurdle and its limbs (A), (B), (C)."""
    if terms.waterfall_type == WHOLE_FUND:
        a = sum(state.funding.values(), ZERO)
        b = ZERO
    elif terms.waterfall_type == DEAL_BY_DEAL:
        a = state.funding.get(investment_id, ZERO)
        b = sum(
            (amt for inv, amt in state.funding.items()
             if inv != investment_id and statuses.get(inv) in RESOLVED),
            ZERO,
        )
    else:
        raise ValueError(f'unknown waterfall type {terms.waterfall_type}')
    c = state.expenses if terms.roc_includes_expenses else ZERO
    return a + b + c, {'A': a, 'B': b, 'C': c}


def catch_up_target(terms: FundTerms, pref_paid: Decimal) -> Decimal:
    """Total tier-3 flow at which CIR hold carry% of tiers 2+3.

    CIR receive catch_up_rate of each tier-3 unit, so with flow X:
    catch_up_rate * X = carry * (pref + X)  =>  X = carry * pref / (catch_up_rate - carry).
    Full catch-up (rate 1, carry 20%) gives X = 0,25 x preferred return.
    """
    if terms.catch_up_rate <= terms.carry_rate:
        raise ValueError('catch-up rate must exceed carry rate')
    return terms.carry_rate * pref_paid / (terms.catch_up_rate - terms.carry_rate)


def distribute(
    terms: FundTerms,
    state: LPState,
    amount: Decimal,
    *,
    investment_id,
    statuses: dict,
    pref_accrued: Decimal,
) -> LPDistribution:
    """Run one LP's slice of one Investment's receipts through the tiers."""
    amount = D(amount)
    pref_accrued = D(pref_accrued)
    scale = terms.rounding_scale
    cl = terms.clauses
    left = amount

    hurdle, parts = roc_hurdle(terms, state, investment_id, statuses)
    t1 = min(left, max(hurdle - state.roc_paid, ZERO))
    left -= t1

    t2 = min(left, max(pref_accrued - state.pref_paid, ZERO))
    left -= t2
    pref_paid = state.pref_paid + t2

    due = rnd(catch_up_target(terms, pref_paid), scale) - state.catch_up_flow
    t3 = min(left, max(due, ZERO))
    if t3 and t3 == due:
        # fully caught up: CIR hold exactly carry% of tiers 2+3 (no rounding drift)
        t3_cir = rnd(terms.carry_rate * (pref_paid + state.catch_up_flow + t3), scale) - state.catch_up_cir
    else:
        t3_cir = rnd(t3 * terms.catch_up_rate, scale)
    t3_lp = t3 - t3_cir
    left -= t3

    t4_cir = rnd(left * terms.carry_rate, scale)
    t4_lp = left - t4_cir

    tiers = (
        TierLine(1, 'Return of capital', cl['roc'], t1, ZERO),
        TierLine(2, 'Preferred return', cl['pref'], t2, ZERO),
        TierLine(3, 'GP catch-up', cl['catch_up'], t3_lp, t3_cir),
        TierLine(4, 'Residual split', cl['residual'], t4_lp, t4_cir),
    )
    to_lp = sum((t.to_lp for t in tiers), ZERO)
    to_cir = sum((t.to_cir for t in tiers), ZERO)
    to_gp = rnd(to_cir * terms.carry_split_gp, scale)
    new_state = replace(
        state,
        roc_paid=state.roc_paid + t1,
        pref_paid=pref_paid,
        catch_up_flow=state.catch_up_flow + t3,
        catch_up_cir=state.catch_up_cir + t3_cir,
        lp_total=state.lp_total + to_lp,
        cir_total=state.cir_total + to_cir,
    )
    return LPDistribution(
        amount=amount, hurdle=hurdle, hurdle_parts=parts, tiers=tiers,
        to_lp=to_lp, to_cir=to_cir, to_gp=to_gp, to_vehicle=to_cir - to_gp, state=new_state,
    )


def apportion_receipts(amount: Decimal, funding_by_lp: dict, scale=D('0.01')) -> dict:
    """Investment receipts are first apportioned among LPs pro rata to the contributions
    that funded that Investment (cl. 8.2(a))."""
    return allocate(D(amount), funding_by_lp, scale)


def lifetime_carry_entitlement(terms: FundTerms, state: LPState, pref_accrued: Decimal) -> Decimal:
    """Carry the CIR would be entitled to if everything distributed to date went through one
    whole-fund waterfall, treating every unresolved Investment as worth nothing (CI-02)."""
    total = state.lp_total + state.cir_total
    whole = replace(terms, waterfall_type=WHOLE_FUND)
    fresh = LPState(funding=state.funding, expenses=state.expenses)
    return distribute(whole, fresh, total, investment_id=None, statuses={}, pref_accrued=pref_accrued).to_cir


def clawback_exposure(terms: FundTerms, state: LPState, pref_accrued: Decimal) -> Decimal:
    """Interim clawback exposure for one LP: carry received above lifetime entitlement."""
    return max(state.cir_total - lifetime_carry_entitlement(terms, state, pref_accrued), ZERO)


def clawback_due(terms: FundTerms, state: LPState, pref_accrued: Decimal, taxes_paid: Decimal) -> Decimal:
    """Clawback at liquidation, limited to carry net of taxes actually paid (cl. 15.4)."""
    excess = clawback_exposure(terms, state, pref_accrued)
    return min(excess, max(state.cir_total - D(taxes_paid), ZERO))
