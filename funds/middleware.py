from django.conf import settings
from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse

from .i18n import LANGS

LANG_COOKIE = 'valor_lang'


class LanguageMiddleware:
    """EN/RU switch: ?lang=ru sets a cookie; default English."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        asked = request.GET.get('lang')
        request.lang = asked if asked in LANGS else request.COOKIES.get(LANG_COOKIE, 'en')
        if request.lang not in LANGS:
            request.lang = 'en'
        response = self.get_response(request)
        if asked in LANGS:
            response.set_cookie(LANG_COOKIE, asked, max_age=365 * 86400, samesite='Lax',
                                path=settings.SESSION_COOKIE_PATH)
        return response


class LoginRequiredMiddleware:
    """Closed demo: every page requires sign-in except login, health check and static files.
    Django admin has its own login."""

    OPEN = {'login', 'healthz'}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            path = request.path_info
            if not (path.startswith('/static/') or path.startswith('/admin/')):
                try:
                    name = resolve(path).url_name
                except Resolver404:
                    name = None
                if name not in self.OPEN:
                    return redirect(f"{reverse('login')}?next={request.get_full_path()}")
        return self.get_response(request)
