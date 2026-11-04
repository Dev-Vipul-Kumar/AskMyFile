"""
core/views.py
Landing page, authenticated home, static pages, health check.
"""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from shares.models import Share
from shares.services import get_daily_limit, get_quota_remaining


# ---------------------------------------------------------------------------
# Public landing page
# ---------------------------------------------------------------------------

@require_GET
def landing(request):
    """
    Public landing page.
    If the user is already authenticated and verified, show a simplified
    landing that links to the Home page rather than the upload panel.
    """
    if request.user.is_authenticated and getattr(request.user, "is_fully_verified", False):
        return home(request)

    context = {
        "max_file_size_mb": settings.MAX_FILE_SIZE_BYTES // (1024 * 1024),
        "guest_daily_limit": settings.GUEST_SHARES_PER_DAY,
        "pbkdf2_iterations": settings.PBKDF2_ITERATIONS,
    }
    return render(request, "core/landing.html", context)


# ---------------------------------------------------------------------------
# Authenticated Home
# ---------------------------------------------------------------------------

@login_required
@require_GET
def home(request):
    """
    Protected home page — mirrors the landing page structure with added
    account, plan, quota, and recent-share information.
    Redirects to verify flow if not fully verified.
    """
    user = request.user
    if not user.is_fully_verified:
        from django.shortcuts import redirect
        from django.contrib import messages
        messages.warning(request, "Please complete account verification to access your home page.")
        if not user.email_verified:
            return redirect("accounts:resend_email_verification")
        return redirect("accounts:verify_phone")

    recent_shares = (
        Share.objects
        .filter(owner_user=user)
        .order_by("-created_at")[:10]
    )

    quota_remaining = get_quota_remaining(user=user)
    daily_limit = get_daily_limit(user=user)

    # Active subscription
    active_sub = None
    try:
        active_sub = (
            user.subscriptions
            .filter(status="active")
            .select_related("plan")
            .latest("period_start")
        )
        if not active_sub.is_currently_active:
            active_sub = None
    except Exception:
        pass

    context = {
        "user": user,
        "recent_shares": recent_shares,
        "quota_remaining": quota_remaining,
        "quota_limit": daily_limit,
        "quota_used": daily_limit - quota_remaining,
        "active_subscription": active_sub,
        "plan_name": active_sub.plan.name if active_sub else "Free",
        "max_file_size_mb": settings.MAX_FILE_SIZE_BYTES // (1024 * 1024),
        "pbkdf2_iterations": settings.PBKDF2_ITERATIONS,
        "show_upgrade_prompt": quota_remaining == 0,
    }

    # Load plans for subscription overlay if quota is hit
    if quota_remaining == 0 and not active_sub:
        from billing.models import Plan
        context["upgrade_plans"] = Plan.objects.filter(active=True, is_free=False).order_by("sort_order")

    return render(request, "core/home.html", context)


# ---------------------------------------------------------------------------
# Static / informational pages
# ---------------------------------------------------------------------------

@require_GET
def privacy(request):
    return render(request, "core/privacy.html")


@require_GET
def terms(request):
    return render(request, "core/terms.html")


@require_GET
def help_page(request):
    return render(request, "core/help.html")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@require_GET
def health(request):
    """
    Lightweight health check.  Returns 200 if the app can reach the database.
    Does not expose secrets or internal state.
    """
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False

    status = 200 if db_ok else 503
    return JsonResponse({"status": "ok" if db_ok else "degraded", "db": db_ok}, status=status)
