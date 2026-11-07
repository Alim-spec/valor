from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from engine import ENGINE_VERSION
from engine.money import D
from engine.sla import FAA_SLA, KZ_HOLIDAYS_2026_2027, BusinessCalendar, assess_nav_error, quarter_timeline

from . import golden, services
from .i18n import translate
from .models import AdminInvoice, Distribution, Event, Fund, Investment, LedgerEntry, Partner

CAL = BusinessCalendar(KZ_HOLIDAYS_2026_2027)
ADMINISTRATOR, APPROVER = 'Administrator', 'Approver'


def today():
    return timezone.localdate()


def fund_or_404():
    fund = Fund.objects.order_by('id').first()
    if not fund:
        raise Fund.DoesNotExist('Run: python manage.py seed_demo')
    return fund


def in_group(user, name):
    return user.is_superuser or user.groups.filter(name=name).exists()


def healthz(request):
    return HttpResponse('ok')


class LoginView(auth_views.LoginView):
    template_name = 'funds/login.html'
    redirect_authenticated_user = True


def dashboard(request):
    fund = fund_or_404()
    as_of = today()
    summary = services.fund_summary(fund, as_of)
    pending = Distribution.objects.filter(fund=fund, state=Distribution.DRAFT)
    q = fund.quarters.first()
    timeline = None
    if q and q.inputs_received:
        timeline = quarter_timeline(CAL, FAA_SLA, quarter_end=q.quarter_end, inputs_received=q.inputs_received,
                                    draft_issued=q.draft_issued, gp_approved=q.gp_approved)
    return render(request, 'funds/dashboard.html', {
        'summary': summary, 't': summary['totals'], 'as_of': as_of, 'pending': pending,
        'quarter': q, 'timeline': timeline,
        'events': Event.objects.filter(fund=fund).select_related('actor')[:8],
        'invoices_open': AdminInvoice.objects.filter(fund=fund, paid__isnull=True),
    })


def register(request):
    fund = fund_or_404()
    accounts = {a.partner.pk: a for a in services.capital_accounts(fund, today())}
    partners = fund.partners.prefetch_related('commitments')
    return render(request, 'funds/register.html', {'partners': partners, 'accounts': accounts})


def capital_accounts(request):
    fund = fund_or_404()
    as_of = today()
    summary = services.fund_summary(fund, as_of)
    return render(request, 'funds/capital_accounts.html', {'summary': summary, 'as_of': as_of})


def investments(request):
    fund = fund_or_404()
    rows = []
    for inv in fund.investments.all():
        cost = services.investment_cost(inv)
        rows.append({'inv': inv, 'cost': cost, 'fv_usd': services.investment_fair_value_usd(inv)})
    return render(request, 'funds/investments.html', {'rows': rows})


def _parse_distribution_form(request, fund):
    errors = []
    try:
        inv = fund.investments.get(pk=request.POST.get('investment'))
    except (Investment.DoesNotExist, ValueError):
        inv = None
        errors.append('Choose an investment')
    try:
        on = datetime.strptime(request.POST.get('date', ''), '%Y-%m-%d').date()
    except ValueError:
        on = None
        errors.append('Enter a valid date')
    try:
        gross = D(request.POST.get('gross', ''))
        if gross <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        gross = None
        errors.append('Enter a positive amount')
    status = request.POST.get('status')
    if status not in dict(Investment.STATUSES):
        errors.append('Choose the status after distribution')
    return inv, on, gross, status, errors


def distributions(request):
    fund = fund_or_404()
    preview = None
    form = {'date': today().isoformat(), 'status': Investment.REALISED}
    if request.method == 'POST':
        if not in_group(request.user, ADMINISTRATOR):
            return HttpResponseForbidden('Administrator role required')
        inv, on, gross, status, errors = _parse_distribution_form(request, fund)
        form = {'investment': request.POST.get('investment'), 'date': request.POST.get('date'),
                'gross': request.POST.get('gross'), 'status': status}
        for e in errors:
            messages.error(request, e)
        if not errors:
            if request.POST.get('action') == 'submit':
                dist = services.propose_distribution(request.user, fund, inv, on, gross, status)
                messages.success(request, f'Distribution #{dist.pk} submitted for four-eyes approval')
                return redirect('distribution', pk=dist.pk)
            preview = services.compute_distribution(fund, inv, on, gross, status)
    return render(request, 'funds/distributions.html', {
        'items': Distribution.objects.filter(fund=fund).select_related('investment', 'created_by', 'approved_by')
        .order_by('-date', '-id'),
        'investments': fund.investments.exclude(status__in=[Investment.REALISED, Investment.WRITTEN_OFF]),
        'statuses': Investment.STATUSES, 'preview': preview, 'form': form,
        'can_make': in_group(request.user, ADMINISTRATOR),
    })


def distribution(request, pk):
    fund = fund_or_404()
    dist = get_object_or_404(Distribution, pk=pk, fund=fund)
    return render(request, 'funds/distribution.html', {
        'dist': dist, 'r': dist.result,
        'can_approve': in_group(request.user, APPROVER) and dist.created_by_id != request.user.pk
        and dist.state == Distribution.DRAFT,
        'ledger': LedgerEntry.objects.filter(journal=f'DIST-{dist.pk}').select_related('partner'),
    })


@require_POST
def distribution_decide(request, pk):
    fund = fund_or_404()
    dist = get_object_or_404(Distribution, pk=pk, fund=fund)
    if not in_group(request.user, APPROVER):
        return HttpResponseForbidden('Approver role required')
    try:
        if request.POST.get('action') == 'approve':
            services.approve_distribution(request.user, dist)
            messages.success(request, 'Approved and posted to the ledger')
        else:
            services.reject_distribution(request.user, dist, request.POST.get('reason') or 'Rejected by checker')
            messages.info(request, 'Rejected')
    except (services.SegregationOfDuties, ValueError) as e:
        messages.error(request, str(e))
    return redirect('distribution', pk=pk)


def fees(request):
    fund = fund_or_404()
    fee_income = {date(2026, 4, 1): D('60000')}   # demo: transaction fee received in Q1 2026
    rows = services.fee_schedule(fund, today(), fee_income)
    quarters = []
    for r in rows:
        if not quarters or quarters[-1]['start'] != r['quarter']:
            quarters.append({'start': r['quarter'], 'end': r['quarter_end'], 'rows': [], 'gross': D(0),
                             'net': D(0), 'offset': D(0)})
        q = quarters[-1]
        q['rows'].append(r)
        q['gross'] += r['gross']
        q['net'] += r['net']
        q['offset'] += r['offset']
    quarters.reverse()
    return render(request, 'funds/fees.html', {'quarters': quarters, 'fee_income': fee_income})


def equalisation(request):
    fund = fund_or_404()
    form = {'name': request.GET.get('name', 'New LP'), 'commitment': request.GET.get('commitment', '10000000'),
            'admission': request.GET.get('admission', today().isoformat())}
    statement = error = None
    try:
        statement = services.equalisation_preview(
            fund, form['name'], D(form['commitment']),
            datetime.strptime(form['admission'], '%Y-%m-%d').date())
    except (InvalidOperation, ValueError, ZeroDivisionError) as e:
        error = str(e)
    return render(request, 'funds/equalisation.html', {'form': form, 's': statement, 'error': error})


def service_levels(request):
    fund = fund_or_404()
    rows = []
    for q in fund.quarters.all():
        tl = None
        if q.inputs_received:
            tl = quarter_timeline(CAL, FAA_SLA, quarter_end=q.quarter_end, inputs_received=q.inputs_received,
                                  draft_issued=q.draft_issued, gp_approved=q.gp_approved)
        errors = []
        for e in q.nav_errors.all():
            errors.append({'e': e, 'a': assess_nav_error(
                CAL, FAA_SLA, published=e.published_nav_per_unit, recomputed=e.recomputed_nav_per_unit,
                threshold=fund.terms.nav_materiality, discovered=e.discovered, notified=e.gp_notified)})
        rows.append({'q': q, 'tl': tl, 'errors': errors})
    return render(request, 'funds/service_levels.html', {'rows': rows, 'sla': FAA_SLA, 'today': today()})


def billing(request):
    fund = fund_or_404()
    as_of = today()
    invoices = [services.invoice_view(i, as_of) for i in fund.admin_invoices.all()]
    last = fund.quarters.filter(nav__isnull=False, gp_approved__isnull=False).first()
    nav = last.nav if last else D(0)
    lps = fund.partners.filter(kind=Partner.LP).count()
    draft = services.draft_admin_invoice(fund, nav, notices=lps)
    return render(request, 'funds/billing.html', {'invoices': invoices, 'draft': draft, 'nav': nav, 'last': last})


def acceptance(request):
    results = golden.run_all()
    return render(request, 'funds/acceptance.html', {
        'results': results, 'passed': sum(r['passed'] for r in results), 'engine_version': ENGINE_VERSION})


def terms(request):
    fund = fund_or_404()
    return render(request, 'funds/terms.html', {
        'versions': fund.terms_versions.select_related('created_by', 'approved_by').order_by('-version'),
        'current': fund.terms.as_dict()})


def audit(request):
    fund = fund_or_404()
    return render(request, 'funds/audit.html', {
        'events': Event.objects.filter(fund=fund).select_related('actor')[:200],
        'ledger': LedgerEntry.objects.filter(fund=fund).select_related('partner', 'actor').order_by('-id')[:100],
    })
