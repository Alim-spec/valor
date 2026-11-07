from decimal import Decimal

from django import template

from engine.money import fmt

from ..i18n import translate

register = template.Library()


@register.simple_tag(takes_context=True)
def t(context, text):
    return translate(text, context.get('lang', 'en'))


@register.filter
def money(value, places=2):
    if value in (None, ''):
        return '—'
    return fmt(Decimal(str(value)), int(places))


@register.filter
def pct(value, places=1):
    if value in (None, ''):
        return '—'
    return fmt(Decimal(str(value)) * 100, int(places)) + '%'


@register.filter
def mult(value, places=2):
    if value in (None, ''):
        return '—'
    return fmt(Decimal(str(value)), int(places)) + 'x'


@register.filter
def get(d, key):
    return d.get(str(key)) if d else None


@register.filter
def tr(text, lang):
    return translate(str(text), lang)
