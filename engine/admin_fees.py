"""Valor's own administration fees (ADM-01..03, FAA Annex B). All amounts USD.

Distinct from the LPA management fee. Ranks senior to distributions and carry.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .money import ZERO, D, rnd


@dataclass(frozen=True)
class AdminFeeSchedule:
    # (upper bound of AUM exclusive or None for the top tier, annual fee)
    tiers: tuple = (
        (D('20000000'), D('99000.00')),
        (D('50000000'), D('119000.00')),
        (D('100000000'), D('149000.00')),
        (D('500000000'), D('199000.00')),
    )
    renegotiation_above: Decimal = D('500000000')
    establishment_fee: Decimal = D('20000.00')
    capital_activity_fee: Decimal = D('250.00')     # per investor per call / distribution notice
    onboarding_fee: Decimal = D('2000.00')          # per LP
    hourly_rate: Decimal = D('250.00')              # extraordinary / special filings
    cpi_cap: Decimal = D('0.05')                    # upward only, capped at 5% p.a.
    payment_terms_days: int = 15
    late_interest_monthly: Decimal = D('0.015')     # 1,5% per month compounded
    days_per_month: int = 30
    vat_rate: Decimal = ZERO


FAA_SCHEDULE = AdminFeeSchedule()


@dataclass(frozen=True)
class TierPick:
    annual: Decimal
    label: str
    renegotiate: bool


def admin_fee_tier(schedule: AdminFeeSchedule, nav: Decimal) -> TierPick:
    nav = D(nav)
    lower = ZERO
    for upper, fee in schedule.tiers:
        if nav < upper:
            return TierPick(fee, f'{lower / 1000000:.0f}m – {upper / 1000000:.0f}m', False)
        lower = upper
    return TierPick(schedule.tiers[-1][1], f'> {lower / 1000000:.0f}m', nav > schedule.renegotiation_above)


def cpi_escalate(schedule: AdminFeeSchedule, annual: Decimal, cpi_change: Decimal) -> Decimal:
    """Annual CPI-U escalation: upward only, capped (ADM-03)."""
    factor = min(max(D(cpi_change), ZERO), schedule.cpi_cap)
    return rnd(D(annual) * (1 + factor))


def late_interest(schedule: AdminFeeSchedule, amount: Decimal, days_overdue: int) -> Decimal:
    """1,5% per month compounded on overdue amounts. Complete 30-day months compound;
    the remaining days accrue pro rata within the month (configurable convention)."""
    if days_overdue <= 0:
        return ZERO
    months, days = divmod(days_overdue, schedule.days_per_month)
    m = schedule.late_interest_monthly
    grown = D(amount) * (1 + m) ** months
    grown *= 1 + m * D(days) / D(schedule.days_per_month)
    return rnd(grown - D(amount))


def days_overdue(schedule: AdminFeeSchedule, invoice_date: date, paid_date: date) -> int:
    """Interest accrues from day 16 after a 15-day payment term."""
    return max((paid_date - invoice_date).days - schedule.payment_terms_days, 0)


@dataclass(frozen=True)
class InvoiceLine:
    code: str
    description: str
    quantity: Decimal
    unit: Decimal
    amount: Decimal


@dataclass(frozen=True)
class Invoice:
    lines: tuple
    subtotal: Decimal
    vat: Decimal
    total: Decimal
    tier: TierPick
    notes: tuple = field(default_factory=tuple)


def quarterly_invoice(schedule: AdminFeeSchedule, *, approved_nav: Decimal, annual_override: Decimal | None = None,
                      notices: int = 0, onboardings: int = 0, hours: Decimal = ZERO,
                      disbursements: Decimal = ZERO, include_establishment: bool = False) -> Invoice:
    """Quarterly-in-advance invoice on the most recently approved NAV (ADM-01, ADM-02)."""
    tier = admin_fee_tier(schedule, approved_nav)
    annual = D(annual_override) if annual_override is not None else tier.annual
    lines = [InvoiceLine('ADMIN', f'Administration fee, quarter in advance (AUM tier {tier.label})',
                         D(1), rnd(annual / 4), rnd(annual / 4))]
    if include_establishment:
        lines.append(InvoiceLine('ESTAB', 'Establishment fee (Effective Date)', D(1),
                                 schedule.establishment_fee, schedule.establishment_fee))
    if notices:
        lines.append(InvoiceLine('CAPACT', 'Capital-activity fees (per investor per notice)', D(notices),
                                 schedule.capital_activity_fee, schedule.capital_activity_fee * notices))
    if onboardings:
        lines.append(InvoiceLine('ONBRD', 'Investor onboarding', D(onboardings),
                                 schedule.onboarding_fee, schedule.onboarding_fee * onboardings))
    if hours:
        lines.append(InvoiceLine('HOURS', 'Extraordinary / special filings', D(hours),
                                 schedule.hourly_rate, rnd(schedule.hourly_rate * D(hours))))
    if disbursements:
        lines.append(InvoiceLine('DISB', 'AFSA levies and pre-approved disbursements at cost', D(1),
                                 D(disbursements), D(disbursements)))
    subtotal = sum((line.amount for line in lines), ZERO)
    vat = rnd(subtotal * schedule.vat_rate)
    notes = ('AUM above USD 500m: renegotiation required',) if tier.renegotiate else ()
    return Invoice(tuple(lines), subtotal, vat, subtotal + vat, tier, notes)
