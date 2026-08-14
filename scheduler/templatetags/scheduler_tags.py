from django import template

register = template.Library()


@register.filter
def get_item(obj, key):
    """Access dict by key in template: dict|get_item:key"""
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        return None


@register.filter
def make_range(n):
    """Return range(n) for use in templates."""
    try:
        return range(int(n))
    except (TypeError, ValueError):
        return range(0)
