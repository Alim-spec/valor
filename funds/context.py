from .models import Fund


def common(request):
    fund = None
    if getattr(request, 'user', None) and request.user.is_authenticated:
        fund = Fund.objects.order_by('id').first()
    roles = []
    if getattr(request, 'user', None) and request.user.is_authenticated:
        roles = list(request.user.groups.values_list('name', flat=True))
    return {'lang': getattr(request, 'lang', 'en'), 'fund': fund, 'roles': roles}
