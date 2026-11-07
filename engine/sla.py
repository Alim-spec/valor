"""Business-day calendar and FAA service levels (SLA-01..04, FAA cl. 3.4-3.5, Annex C).

Holidays are configuration. KZ_HOLIDAYS_2026_2027 is a default for Astana; the fund
administrator maintains the list (incl. government transfer days) in the platform.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .money import D

KZ_HOLIDAYS_2026_2027 = frozenset({
    date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 7), date(2026, 3, 9), date(2026, 3, 23),
    date(2026, 3, 24), date(2026, 3, 25), date(2026, 5, 1), date(2026, 5, 7), date(2026, 5, 11),
    date(2026, 7, 6), date(2026, 8, 31), date(2026, 10, 26), date(2026, 12, 16),
    date(2027, 1, 1), date(2027, 1, 2), date(2027, 1, 7), date(2027, 3, 8), date(2027, 3, 22),
    date(2027, 3, 23), date(2027, 3, 24), date(2027, 5, 1), date(2027, 5, 7), date(2027, 5, 10),
    date(2027, 7, 6), date(2027, 8, 30), date(2027, 10, 25), date(2027, 12, 16),
})


@dataclass(frozen=True)
class BusinessCalendar:
    holidays: frozenset = frozenset()

    def is_business_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def add(self, start: date, days: int) -> date:
        """start + N business days (start itself not counted)."""
        d = start
        left = days
        while left > 0:
            d += timedelta(days=1)
            if self.is_business_day(d):
                left -= 1
        return d

    def between(self, start: date, end: date) -> int:
        """Business days after start up to and including end."""
        n, d = 0, start
        while d < end:
            d += timedelta(days=1)
            if self.is_business_day(d):
                n += 1
        return n


@dataclass(frozen=True)
class SLATerms:
    """FAA Annex C, all configurable (Appendix C of the specification)."""
    cut_off_days: int = 15                 # calendar days after quarter-end
    draft_nav_bd: int = 10
    final_nav_bd: int = 3
    deemed_approval_bd: int = 5
    reconciliation_bd: int = 2
    register_update_bd: int = 2
    minutes_bd: int = 5
    nav_error_notify_bd: int = 1
    nav_error_correct_bd: int = 5
    register_discrepancy_bd: int = 1
    escalation_bd: tuple = (5, 3, 10)      # L1, L2, L3 resolution targets
    l3_plan_hours: int = 48


FAA_SLA = SLATerms()


@dataclass(frozen=True)
class QuarterTimeline:
    quarter_end: date
    cut_off: date
    clock_start: date
    inputs_late: bool
    draft_nav_due: date
    draft_issued: date | None
    deemed_approval: date | None
    approved: date | None
    approval_kind: str | None          # 'actual' | 'deemed' | None
    final_nav_due: date | None


def quarter_timeline(cal: BusinessCalendar, sla: SLATerms, *, quarter_end: date, inputs_received: date,
                     draft_issued: date | None = None, gp_approved: date | None = None) -> QuarterTimeline:
    """SLA clock starts on receipt of complete GP inputs, never before the cut-off (SLA-01..03).
    GP silence for N business days is deemed approval for timing only."""
    cut_off = quarter_end + timedelta(days=sla.cut_off_days)
    clock = max(inputs_received, cut_off)
    deemed = cal.add(draft_issued, sla.deemed_approval_bd) if draft_issued else None
    if gp_approved and (deemed is None or gp_approved <= deemed):
        approved, kind = gp_approved, 'actual'
    elif deemed:
        approved, kind = deemed, 'deemed'
    else:
        approved, kind = None, None
    return QuarterTimeline(
        quarter_end=quarter_end, cut_off=cut_off, clock_start=clock, inputs_late=inputs_received > cut_off,
        draft_nav_due=cal.add(clock, sla.draft_nav_bd), draft_issued=draft_issued,
        deemed_approval=deemed, approved=approved, approval_kind=kind,
        final_nav_due=cal.add(approved, sla.final_nav_bd) if approved else None,
    )


def period_for_information(sla: SLATerms, received: date, quarter_end: date) -> str:
    """Information received after the Quarterly Cut-Off goes to the next period (SLA-02)."""
    return 'current' if received <= quarter_end + timedelta(days=sla.cut_off_days) else 'next'


@dataclass(frozen=True)
class NavErrorAssessment:
    impact: Decimal                 # relative impact on NAV per unit
    material: bool
    treatment: str
    notify_gp_by: date | None
    corrective_nav_by: date | None
    escalation_level: int | None


def assess_nav_error(cal: BusinessCalendar, sla: SLATerms, *, published: Decimal, recomputed: Decimal,
                     threshold: Decimal, discovered: date, notified: date | None = None) -> NavErrorAssessment:
    """NAV-error regime (SLA-04, FAA Annex C)."""
    published, recomputed = D(published), D(recomputed)
    impact = abs(recomputed - published) / published
    if impact > D(threshold):
        start = min(discovered, notified) if notified else discovered
        return NavErrorAssessment(
            impact, True, 'Material: notify GP, corrective NAV, reissue reporting after GP approval',
            cal.add(discovered, sla.nav_error_notify_bd), cal.add(start, sla.nav_error_correct_bd), 3,
        )
    return NavErrorAssessment(impact, False, 'Below threshold: correct prospectively next cycle',
                              None, None, None)
