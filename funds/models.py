"""Core data model (specification section 3).

Monetary fields are Decimal with currency. The ledger and the event log are append-only
(ARC-03): rows can be inserted, never updated or deleted; corrections are reversing entries.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

from engine.config import FundTerms

MONEY = dict(max_digits=20, decimal_places=2)
RATE = dict(max_digits=18, decimal_places=6)


class AppendOnly(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and not kwargs.pop('_allow_update', False):
            raise PermissionError(f'{type(self).__name__} is append-only; post a reversing entry instead')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError(f'{type(self).__name__} is append-only')


class Fund(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    legal_form = models.CharField(max_length=100, default='AIFC Limited Partnership')
    base_currency = models.CharField(max_length=3, default='USD')
    initial_closing = models.DateField()
    term_years = models.PositiveSmallIntegerField(default=10)
    lpa_reference = models.CharField(max_length=200, blank=True)
    target_size = models.DecimalField(**MONEY, null=True, blank=True)
    min_commitment = models.DecimalField(**MONEY, null=True, blank=True)
    units_outstanding = models.DecimalField(max_digits=20, decimal_places=6, default=Decimal('0'))

    def __str__(self):
        return f'{self.code} — {self.name}'

    @property
    def current_terms_version(self):
        return self.terms_versions.filter(approved_by__isnull=False).order_by('-version').first()

    @property
    def terms(self) -> FundTerms:
        v = self.current_terms_version
        return FundTerms.from_dict(v.terms) if v else FundTerms()


class FundTermsVersion(AppendOnly):
    """Versioned LPA-derived configuration (ARC-02). A change is a new version, approved by a checker."""
    fund = models.ForeignKey(Fund, on_delete=models.PROTECT, related_name='terms_versions')
    version = models.PositiveIntegerField()
    terms = models.JSONField()
    effective_from = models.DateField()
    reason = models.CharField(max_length=300)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+',
                                    null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('fund', 'version')]


class Partner(models.Model):
    LP, GP, SPONSOR, CIV, FEEDER = 'LP', 'GP', 'SPONSOR', 'CIV', 'FEEDER'
    KINDS = [(LP, 'Limited Partner'), (GP, 'General Partner'), (SPONSOR, 'Sponsor LP'),
             (CIV, 'Carried Interest Vehicle'), (FEEDER, 'Feeder')]
    KYC_PENDING, KYC_SCREENED, KYC_APPROVED, KYC_REJECTED = 'pending', 'screened', 'approved', 'rejected'
    KYC = [(KYC_PENDING, 'Pending'), (KYC_SCREENED, 'Screened, awaiting GP approval'),
           (KYC_APPROVED, 'Approved by GP'), (KYC_REJECTED, 'Rejected')]

    fund = models.ForeignKey(Fund, on_delete=models.PROTECT, related_name='partners')
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=10, choices=KINDS, default=LP)
    country = models.CharField(max_length=2, default='KZ')
    email = models.EmailField(blank=True)
    kyc_status = models.CharField(max_length=10, choices=KYC, default=KYC_PENDING)
    risk_rating = models.CharField(max_length=10, default='low')
    fatca_crs = models.CharField(max_length=60, blank=True, help_text='Classification only, not tax advice')
    is_defaulting = models.BooleanField(default=False)
    side_letter = models.CharField(max_length=300, blank=True)

    class Meta:
        unique_together = [('fund', 'code')]
        ordering = ['code']

    def __str__(self):
        return f'{self.code} {self.name}'

    @property
    def can_transact(self):
        """REG-02 / AML-01: no capital activity before GP final onboarding approval."""
        return self.kyc_status == self.KYC_APPROVED


class Commitment(models.Model):
    INITIAL, SUBSEQUENT, FINAL = 'initial', 'subsequent', 'final'
    CLOSINGS = [(INITIAL, 'Initial'), (SUBSEQUENT, 'Subsequent'), (FINAL, 'Final')]
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='commitments')
    closing = models.CharField(max_length=10, choices=CLOSINGS, default=INITIAL)
    date = models.DateField()
    amount = models.DecimalField(**MONEY)

    class Meta:
        ordering = ['date', 'id']


class Investment(models.Model):
    UNREALISED, PARTIAL, REALISED, WRITTEN_OFF = 'unrealised', 'partial', 'realised', 'written_off'
    STATUSES = [(UNREALISED, 'Unrealised'), (PARTIAL, 'Partially realised'),
                (REALISED, 'Fully realised'), (WRITTEN_OFF, 'Fully written off')]
    fund = models.ForeignKey(Fund, on_delete=models.PROTECT, related_name='investments')
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=200)
    sector = models.CharField(max_length=100, blank=True)
    holding_entity = models.CharField(max_length=200, blank=True)
    currency = models.CharField(max_length=3, default='USD')
    acquired = models.DateField()
    status = models.CharField(max_length=12, choices=STATUSES, default=UNREALISED)
    fair_value_local = models.DecimalField(**MONEY, default=Decimal('0'))
    closing_rate = models.DecimalField(**RATE, default=Decimal('1'), help_text='local per 1 USD')

    class Meta:
        unique_together = [('fund', 'code')]
        ordering = ['acquired', 'code']

    def __str__(self):
        return f'{self.code} {self.name}'


class Contribution(models.Model):
    """Actual funding of a drawdown by one partner, with USD equivalent and the dealt rate (FX-01)."""
    INVESTMENT, FEE, EXPENSES = 'investment', 'fee', 'expenses'
    PURPOSES = [(INVESTMENT, 'Investment'), (FEE, 'Management Fee'), (EXPENSES, 'Partnership expenses')]
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='contributions')
    drawdown_no = models.PositiveIntegerField()
    notice_date = models.DateField()
    due_date = models.DateField()
    funded_date = models.DateField(null=True, blank=True)
    purpose = models.CharField(max_length=12, choices=PURPOSES)
    investment = models.ForeignKey(Investment, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='contributions')
    currency = models.CharField(max_length=3, default='USD')
    amount_local = models.DecimalField(**MONEY)
    fx_rate = models.DecimalField(**RATE, default=Decimal('1'), help_text='Investment Exchange Rate, local per USD')
    fx_source = models.CharField(max_length=100, blank=True)
    amount_usd = models.DecimalField(**MONEY)
    recallable = models.BooleanField(default=False)
    equalisation = models.BooleanField(default=False, help_text='Rebalancing entry on a Subsequent Closing')
    deemed_date = models.DateField(null=True, blank=True,
                                   help_text='Date the flow counts from as if all LPs were admitted at the '
                                             'Initial Closing (equalisation, LPA cl. 5, 8.1)')

    @property
    def effective_date(self):
        return self.deemed_date or self.funded_date

    class Meta:
        ordering = ['notice_date', 'drawdown_no', 'partner__code']


class Distribution(models.Model):
    """A proposed distribution from an Investment. Maker creates, a different user approves (NFR-04)."""
    DRAFT, APPROVED, REJECTED = 'draft', 'approved', 'rejected'
    STATES = [(DRAFT, 'Awaiting approval'), (APPROVED, 'Approved'), (REJECTED, 'Rejected')]
    fund = models.ForeignKey(Fund, on_delete=models.PROTECT, related_name='distributions')
    investment = models.ForeignKey(Investment, on_delete=models.PROTECT, related_name='distributions')
    date = models.DateField()
    gross_amount = models.DecimalField(**MONEY)
    investment_status_after = models.CharField(max_length=12, choices=Investment.STATUSES)
    in_kind = models.BooleanField(default=False)
    state = models.CharField(max_length=10, choices=STATES, default=DRAFT)
    result = models.JSONField(help_text='Per-LP tier allocation from the engine')
    terms_version = models.PositiveIntegerField()
    engine_version = models.CharField(max_length=20)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+',
                                    null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['date', 'id']


class LedgerEntry(AppendOnly):
    """Double-entry posting. Every balance derives from these rows."""
    fund = models.ForeignKey(Fund, on_delete=models.PROTECT, related_name='ledger')
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, null=True, blank=True)
    journal = models.CharField(max_length=40, help_text='Groups the lines of one transaction')
    date = models.DateField()
    account = models.CharField(max_length=60)
    debit = models.DecimalField(**MONEY, default=Decimal('0'))
    credit = models.DecimalField(**MONEY, default=Decimal('0'))
    memo = models.CharField(max_length=300)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reversal_of = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'id']


class Event(AppendOnly):
    """Audit trail: who, what, when, why, before/after (ARC-03, NFR-02)."""
    fund = models.ForeignKey(Fund, on_delete=models.PROTECT, null=True, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name='+')
    action = models.CharField(max_length=60)
    object_type = models.CharField(max_length=60)
    object_id = models.CharField(max_length=40)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    reason = models.CharField(max_length=300, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-at', '-id']


class QuarterClose(models.Model):
    """FAA quarterly cycle for one fund (SLA-01..03)."""
    fund = models.ForeignKey(Fund, on_delete=models.PROTECT, related_name='quarters')
    quarter_end = models.DateField()
    inputs_received = models.DateField(null=True, blank=True)
    draft_issued = models.DateField(null=True, blank=True)
    gp_approved = models.DateField(null=True, blank=True)
    final_issued = models.DateField(null=True, blank=True)
    nav = models.DecimalField(**MONEY, null=True, blank=True)
    nav_per_unit = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    locked = models.BooleanField(default=False)

    class Meta:
        unique_together = [('fund', 'quarter_end')]
        ordering = ['-quarter_end']


class NavError(models.Model):
    """NAV Error Register (SLA-04)."""
    quarter = models.ForeignKey(QuarterClose, on_delete=models.PROTECT, related_name='nav_errors')
    description = models.CharField(max_length=300)
    published_nav_per_unit = models.DecimalField(max_digits=20, decimal_places=6)
    recomputed_nav_per_unit = models.DecimalField(max_digits=20, decimal_places=6)
    discovered = models.DateField()
    gp_notified = models.DateField(null=True, blank=True)
    corrected = models.DateField(null=True, blank=True)


class AdminInvoice(models.Model):
    """Valor's own fees under FAA Annex B (ADM-01..03)."""
    fund = models.ForeignKey(Fund, on_delete=models.PROTECT, related_name='admin_invoices')
    number = models.CharField(max_length=30, unique=True)
    period_start = models.DateField()
    period_end = models.DateField()
    issued = models.DateField()
    approved_nav = models.DecimalField(**MONEY)
    lines = models.JSONField()
    total = models.DecimalField(**MONEY)
    paid = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['-issued']
