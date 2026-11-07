"""Exact money arithmetic (ARC-06): Decimal only, explicit rounding."""

from decimal import ROUND_HALF_UP, Decimal, getcontext

getcontext().prec = 34  # IEEE 754 decimal128 precision — far beyond cent accuracy

ZERO = Decimal('0')
CENT = Decimal('0.01')


def D(value) -> Decimal:
    """Convert to Decimal. Floats are rejected: they are never exact."""
    if isinstance(value, float):
        raise TypeError('float is not allowed for money; pass str or Decimal')
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value).replace(' ', '').replace(' ', '').replace(',', '.'))


def rnd(value: Decimal, scale: Decimal = CENT, rounding: str = ROUND_HALF_UP) -> Decimal:
    return D(value).quantize(scale, rounding=rounding)


def split(amount: Decimal, share: Decimal, scale: Decimal = CENT) -> tuple[Decimal, Decimal]:
    """Split amount into (share part, remainder) so the two always add up exactly."""
    first = rnd(amount * share, scale)
    return first, amount - first


def allocate(amount: Decimal, weights: dict, scale: Decimal = CENT) -> dict:
    """Pro-rata allocation that sums exactly to amount (largest-remainder method, stable order)."""
    total = sum(weights.values(), ZERO)
    if total == ZERO:
        return {k: ZERO for k in weights}
    raw = {k: amount * w / total for k, w in weights.items()}
    out = {k: v.quantize(scale, rounding='ROUND_DOWN') for k, v in raw.items()}
    rest = amount - sum(out.values(), ZERO)
    steps = int(rest / scale)
    order = sorted(weights, key=lambda k: (-(raw[k] - out[k]), str(k)))
    for k in order[:steps]:
        out[k] += scale
    return out


def fmt(value, places: int = 2, lang: str = 'en') -> str:
    """Space-grouped, comma-decimal notation (NFR-07): 1 234 567,89."""
    if value is None:
        return '—'
    q = D(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    sign = '-' if q < 0 else ''
    whole, _, frac = f'{abs(q):f}'.partition('.')
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    out = ' '.join(groups) or '0'
    if places:
        out += ',' + frac.ljust(places, '0')
    return sign + out
