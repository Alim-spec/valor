"""Multi-currency (FX-01..02, LPA cl. 4.2, 9.3(c)(i), IAS 21).

The Investment Exchange Rate is the actual dealt rate of each funding drawdown. A non-USD
Investment's USD Acquisition Cost is fixed from the aggregate funding at those rates and never
retranslated: it is the fee base and cost basis. Fair value is retranslated at closing rate.
Rates are quoted as local currency per 1 USD (e.g. 480,00 KZT/USD).
"""

from dataclasses import dataclass
from decimal import Decimal

from .money import ZERO, D, rnd


@dataclass(frozen=True)
class AcquisitionCost:
    local: Decimal
    usd: Decimal
    weighted_rate: Decimal


def usd_acquisition_cost(tranches: list[tuple[Decimal, Decimal]]) -> AcquisitionCost:
    """tranches: [(local amount, dealt rate)]. Weighted average if tranched."""
    local = sum((D(a) for a, _ in tranches), ZERO)
    usd = rnd(sum((D(a) / D(r) for a, r in tranches), ZERO))
    rate = (local / usd).quantize(D('0.000001')) if usd else ZERO
    return AcquisitionCost(local, usd, rate)


def translate(local_amount: Decimal, rate: Decimal) -> Decimal:
    return rnd(D(local_amount) / D(rate))


@dataclass(frozen=True)
class Translation:
    fair_value_usd: Decimal
    cost_usd: Decimal
    unrealised_gain_usd: Decimal   # FX embedded in the fair-value movement (FVTPL)
    fee_base_usd: Decimal


def translate_investment(cost: AcquisitionCost, fair_value_local: Decimal, closing_rate: Decimal) -> Translation:
    fv = translate(fair_value_local, closing_rate)
    return Translation(fv, cost.usd, fv - cost.usd, cost.usd)
