"""Fund economics as configuration (ARC-02).

A new fund is onboarded by a new FundTerms value, never by new code.
CACPEF_TERMS mirrors the CACPEF LPA; every field names the clause it comes from.
"""

from dataclasses import asdict, dataclass, field
from decimal import Decimal

from .money import D

DEAL_BY_DEAL = 'deal_by_deal'
WHOLE_FUND = 'whole_fund'


@dataclass(frozen=True)
class FundTerms:
    # Waterfall (cl. 8.1-8.2)
    waterfall_type: str = DEAL_BY_DEAL
    hurdle_rate: Decimal = D('0.08')            # preferred return, cl. 8.1
    hurdle_compounding: str = 'annual'          # IRR compounded annually
    day_count: int = 365                        # actual/365
    catch_up_rate: Decimal = D('1')             # 1 = full catch-up, cl. 8.2(b)(iii)
    carry_rate: Decimal = D('0.20')             # 80/20 residual split, cl. 8.2(b)(iv)
    carry_split_gp: Decimal = D('0.60')         # 60/40 GP / Carried Interest Vehicle, cl. 8.2(c)
    roc_includes_expenses: bool = True          # limb (C), cl. 8.2(b)(i)
    # Management fee (cl. 9.3-9.4)
    mgmt_fee_rate: Decimal = D('0.02')
    fee_basis_switch_years: int = 5             # commitment -> net invested cost
    fee_offset_rate: Decimal = D('1')           # 100% offset of Fee Income, cl. 9.4
    # Equalisation (cl. 5, 9.3(c)(ii))
    equalisation_interest_rate: Decimal = D('0.08')
    fee_equalisation_interest_rate: Decimal = D('0.08')
    # Currency and rounding (ARC-06)
    base_currency: str = 'USD'
    rounding_scale: Decimal = D('0.01')
    # FAA
    nav_materiality: Decimal = D('0.0010')      # 0,10% of NAV per unit, FAA Annex C
    clauses: dict = field(default_factory=lambda: {
        'roc': 'LPA cl. 8.2(b)(i)',
        'pref': 'LPA cl. 8.1, 8.2(b)(ii)',
        'catch_up': 'LPA cl. 8.2(b)(iii)',
        'residual': 'LPA cl. 8.2(b)(iv)',
        'carry_split': 'LPA cl. 8.2(c)',
        'fee': 'LPA cl. 9.3',
        'offset': 'LPA cl. 9.4',
        'equalisation': 'LPA cl. 5, 9.3(c)(ii)',
        'fx': 'LPA cl. 4.2, 9.3(c)(i)',
        'clawback': 'LPA cl. 15.4',
    })

    def as_dict(self) -> dict:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, data: dict) -> 'FundTerms':
        defaults = cls()
        kwargs = {}
        for key, value in data.items():
            if not hasattr(defaults, key):
                raise ValueError(f'unknown fund term: {key}')
            kwargs[key] = D(value) if isinstance(getattr(defaults, key), Decimal) else value
        return cls(**kwargs)


CACPEF_TERMS = FundTerms()
