"""
admin_panel/decorators.py
Reusable guard for admin-only views.
Redirects non-staff users to the regular login page.
"""
from functools import wraps
from django.shortcuts import redirect


def admin_required(view_func):
    """Decorator: requires request.user.is_staff == True."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/accounts/login/?next={request.path}")
        if not request.user.is_staff:
            # Authenticated but not admin → send to regular home, not an error page
            return redirect("/app/")
        return view_func(request, *args, **kwargs)
    return wrapper
