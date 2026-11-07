from django.contrib import admin

from . import models


class ReadOnly(admin.ModelAdmin):
    """Append-only records: visible, never editable from the admin."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.Fund)
class FundAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'base_currency', 'initial_closing')


class CommitmentInline(admin.TabularInline):
    model = models.Commitment
    extra = 0


@admin.register(models.Partner)
class PartnerAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'kind', 'country', 'kyc_status', 'is_defaulting')
    list_filter = ('kind', 'kyc_status')
    inlines = [CommitmentInline]


@admin.register(models.Investment)
class InvestmentAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'currency', 'status', 'acquired')


@admin.register(models.Contribution)
class ContributionAdmin(admin.ModelAdmin):
    list_display = ('drawdown_no', 'partner', 'purpose', 'investment', 'currency', 'amount_local', 'fx_rate',
                    'amount_usd', 'funded_date')
    list_filter = ('purpose', 'currency')


@admin.register(models.Distribution)
class DistributionAdmin(ReadOnly):
    list_display = ('id', 'investment', 'date', 'gross_amount', 'state', 'created_by', 'approved_by')


@admin.register(models.QuarterClose)
class QuarterCloseAdmin(admin.ModelAdmin):
    list_display = ('fund', 'quarter_end', 'inputs_received', 'draft_issued', 'gp_approved', 'nav', 'locked')


@admin.register(models.NavError)
class NavErrorAdmin(admin.ModelAdmin):
    list_display = ('quarter', 'description', 'published_nav_per_unit', 'recomputed_nav_per_unit', 'discovered')


@admin.register(models.AdminInvoice)
class AdminInvoiceAdmin(admin.ModelAdmin):
    list_display = ('number', 'fund', 'issued', 'total', 'paid')


admin.site.register(models.LedgerEntry, ReadOnly)
admin.site.register(models.Event, ReadOnly)
admin.site.register(models.FundTermsVersion, ReadOnly)
