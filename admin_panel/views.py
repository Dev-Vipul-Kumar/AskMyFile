"""
admin_panel/views.py
Custom admin dashboard — visible only to staff users.
Provides: overview stats, user management, share management,
subscription management, share creation (admin unlimited).
"""
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Count, Sum, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from accounts.models import User
from billing.models import Plan, Subscription
from shares.models import GuestQuotaIdentity, QuotaUsage, Share, ShareOTPDelivery
from shares.services import create_share, get_file_bytes

from .decorators import admin_required

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard overview
# ─────────────────────────────────────────────────────────────────────────────

@admin_required
@require_GET
def dashboard(request):
    now  = timezone.now()
    today = timezone.localdate()

    # ── Stats ──
    total_users     = User.objects.count()
    verified_users  = User.objects.filter(email_verified=True, phone_verified=True).count()
    new_today       = User.objects.filter(created_at__date=today).count()
    total_shares    = Share.objects.count()
    active_shares   = Share.objects.filter(status=Share.STATUS_ACTIVE, expires_at__gt=now).count()
    shares_today    = Share.objects.filter(created_at__date=today).count()
    total_bytes     = Share.objects.aggregate(t=Sum("ciphertext_size"))["t"] or 0
    active_subs     = Subscription.objects.filter(status="active").count()
    revenue_approx  = Subscription.objects.filter(status="active").select_related("plan")\
                        .aggregate(t=Sum("plan__price_amount"))["t"] or 0

    # ── Recent activity ──
    recent_users   = User.objects.order_by("-created_at")[:8]
    recent_shares  = Share.objects.select_related("owner_user").order_by("-created_at")[:10]

    # ── Share-type breakdown ──
    type_counts = dict(
        Share.objects.values_list("share_type")
        .annotate(c=Count("id"))
        .values_list("share_type", "c")
    )

    # ── 7-day shares chart data ──
    chart_labels = []
    chart_data   = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        cnt = Share.objects.filter(created_at__date=d).count()
        chart_labels.append(d.strftime("%b %d"))
        chart_data.append(cnt)

    ctx = {
        "total_users": total_users,
        "verified_users": verified_users,
        "new_today": new_today,
        "total_shares": total_shares,
        "active_shares": active_shares,
        "shares_today": shares_today,
        "total_bytes_mb": round(total_bytes / (1024 * 1024), 1),
        "active_subs": active_subs,
        "revenue_approx": revenue_approx,
        "recent_users": recent_users,
        "recent_shares": recent_shares,
        "type_counts": type_counts,
        "chart_labels": json.dumps(chart_labels),
        "chart_data":   json.dumps(chart_data),
        "pbkdf2_iterations": settings.PBKDF2_ITERATIONS,
    }
    return render(request, "admin_panel/dashboard.html", ctx)


# ─────────────────────────────────────────────────────────────────────────────
# Users
# ─────────────────────────────────────────────────────────────────────────────

@admin_required
@require_GET
def user_list(request):
    q    = request.GET.get("q", "").strip()
    filt = request.GET.get("filter", "all")

    users = User.objects.annotate(share_count=Count("shares")).order_by("-created_at")

    if q:
        users = users.filter(Q(email__icontains=q) | Q(display_name__icontains=q) | Q(phone_number__icontains=q))
    if filt == "verified":
        users = users.filter(email_verified=True, phone_verified=True)
    elif filt == "unverified":
        users = users.filter(Q(email_verified=False) | Q(phone_verified=False))
    elif filt == "staff":
        users = users.filter(is_staff=True)
    elif filt == "subscribed":
        users = users.filter(subscriptions__status="active").distinct()

    return render(request, "admin_panel/users.html", {
        "users": users[:200],
        "q": q,
        "filt": filt,
        "total_count": users.count(),
    })


@admin_required
@require_http_methods(["GET", "POST"])
def user_detail(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    shares = Share.objects.filter(owner_user=target).order_by("-created_at")[:20]
    subs   = Subscription.objects.filter(user=target).select_related("plan").order_by("-created_at")

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "toggle_active":
            target.is_active = not target.is_active
            target.save(update_fields=["is_active"])
            messages.success(request, f"User {'activated' if target.is_active else 'deactivated'}.")
        elif action == "toggle_staff":
            if target == request.user:
                messages.error(request, "Cannot change your own staff status.")
            else:
                target.is_staff = not target.is_staff
                target.save(update_fields=["is_staff"])
                messages.success(request, f"Staff status {'granted' if target.is_staff else 'removed'}.")
        elif action == "force_verify":
            target.email_verified = True
            target.phone_verified = True
            target.save(update_fields=["email_verified", "phone_verified"])
            messages.success(request, "User marked as fully verified.")
        elif action == "revoke_all_shares":
            revoked = 0
            for share in Share.objects.filter(owner_user=target, status=Share.STATUS_ACTIVE):
                share.revoke()
                revoked += 1
            messages.success(request, f"Revoked {revoked} active shares.")
        return redirect("admin_panel:user_detail", user_id=user_id)

    return render(request, "admin_panel/user_detail.html", {
        "target": target,
        "shares": shares,
        "subs": subs,
    })


# ─────────────────────────────────────────────────────────────────────────────
# User CRUD  (create / edit / delete)
# ─────────────────────────────────────────────────────────────────────────────

@admin_required
@require_http_methods(["GET", "POST"])
def user_create(request):
    """Admin creates a new user account (pre-verified, no email/OTP flow)."""
    import re
    error = None

    if request.method == "POST":
        email        = request.POST.get("email", "").strip().lower()
        display_name = request.POST.get("display_name", "").strip()
        phone_number = request.POST.get("phone_number", "").strip()
        password     = request.POST.get("password", "")
        is_staff     = request.POST.get("is_staff") == "1"

        # Basic validation
        if not email:
            error = "Email is required."
        elif User.objects.filter(email=email).exists():
            error = f"A user with email '{email}' already exists."
        elif not password or len(password) < 8:
            error = "Password must be at least 8 characters."
        elif phone_number and not re.match(r"^\+?[1-9]\d{6,14}$", phone_number):
            error = "Enter a valid phone number (e.g. +91XXXXXXXXXX)."

        if not error:
            new_user = User(
                email=email,
                display_name=display_name,
                phone_number=phone_number,
                email_verified=True,   # admin-created accounts are pre-verified
                phone_verified=True,
                is_active=True,
                is_staff=is_staff,
            )
            new_user.set_password(password)
            new_user.save()
            messages.success(request, f"User '{email}' created successfully.")
            return redirect("admin_panel:user_detail", user_id=new_user.pk)

    return render(request, "admin_panel/user_create.html", {"error": error})


@admin_required
@require_http_methods(["GET", "POST"])
def user_edit(request, user_id):
    """Admin edits an existing user's profile and permissions."""
    import re
    target = get_object_or_404(User, pk=user_id)
    error  = None

    if request.method == "POST":
        display_name = request.POST.get("display_name", "").strip()
        phone_number = request.POST.get("phone_number", "").strip()
        new_password = request.POST.get("new_password", "").strip()
        is_staff     = request.POST.get("is_staff") == "1"
        is_active    = request.POST.get("is_active") == "1"
        email_verified = request.POST.get("email_verified") == "1"
        phone_verified = request.POST.get("phone_verified") == "1"

        if phone_number and not re.match(r"^\+?[1-9]\d{6,14}$", phone_number):
            error = "Enter a valid phone number (e.g. +91XXXXXXXXXX)."
        elif new_password and len(new_password) < 8:
            error = "New password must be at least 8 characters."

        if not error:
            target.display_name  = display_name
            target.phone_number  = phone_number
            target.email_verified = email_verified
            target.phone_verified = phone_verified
            target.is_active     = is_active
            # Don't let admin accidentally demote themselves
            if target != request.user:
                target.is_staff = is_staff
            if new_password:
                target.set_password(new_password)
            target.save()
            messages.success(request, f"User '{target.email}' updated successfully.")
            return redirect("admin_panel:user_detail", user_id=user_id)

    return render(request, "admin_panel/user_edit.html", {
        "target": target,
        "error": error,
    })


@admin_required
@require_POST
def user_delete(request, user_id):
    """Admin permanently deletes a user and all their shares."""
    target = get_object_or_404(User, pk=user_id)

    if target == request.user:
        messages.error(request, "You cannot delete your own admin account.")
        return redirect("admin_panel:users")

    email = target.email

    # Revoke + delete all their shares (including storage objects)
    from django.core.files.storage import FileSystemStorage
    _storage = FileSystemStorage(location=str(settings.MEDIA_ROOT))
    for share in Share.objects.filter(owner_user=target):
        try:
            if _storage.exists(share.storage_key):
                _storage.delete(share.storage_key)
        except Exception:
            pass

    target.delete()  # cascades to shares, subscriptions, challenges
    messages.success(request, f"User '{email}' and all their data have been permanently deleted.")
    return redirect("admin_panel:users")


# ─────────────────────────────────────────────────────────────────────────────
# Shares
# ─────────────────────────────────────────────────────────────────────────────

@admin_required
@require_GET
def share_list(request):
    q    = request.GET.get("q", "").strip()
    filt = request.GET.get("filter", "all")

    shares = Share.objects.select_related("owner_user").order_by("-created_at")

    if q:
        shares = shares.filter(
            Q(original_filename__icontains=q) |
            Q(owner_user__email__icontains=q)
        )
    if filt == "active":
        shares = shares.filter(status=Share.STATUS_ACTIVE, expires_at__gt=timezone.now())
    elif filt == "expired":
        shares = shares.filter(status=Share.STATUS_EXPIRED)
    elif filt == "revoked":
        shares = shares.filter(status=Share.STATUS_REVOKED)
    elif filt in ("file", "folder", "document"):
        shares = shares.filter(share_type=filt)

    return render(request, "admin_panel/shares.html", {
        "shares": shares[:300],
        "q": q,
        "filt": filt,
        "total_count": shares.count(),
    })


@admin_required
@require_POST
def revoke_share(request, share_id):
    share = get_object_or_404(Share, pk=share_id)
    if share.revoked_at:
        messages.warning(request, "Share already revoked.")
    else:
        share.revoke()
        messages.success(request, f"Share #{share_id} revoked.")
    return redirect("admin_panel:shares")


@admin_required
@require_POST
def delete_share(request, share_id):
    share = get_object_or_404(Share, pk=share_id)
    # Remove stored file
    from django.core.files.storage import FileSystemStorage
    _storage = FileSystemStorage(location=str(settings.MEDIA_ROOT))
    try:
        if _storage.exists(share.storage_key):
            _storage.delete(share.storage_key)
    except Exception as exc:
        logger.warning("Could not delete storage object for share #%s: %s", share_id, exc)
    share.delete()
    messages.success(request, f"Share #{share_id} permanently deleted.")
    return redirect("admin_panel:shares")


# ─────────────────────────────────────────────────────────────────────────────
# Subscriptions
# ─────────────────────────────────────────────────────────────────────────────

@admin_required
@require_GET
def subscription_list(request):
    filt = request.GET.get("filter", "all")
    subs = Subscription.objects.select_related("user", "plan").order_by("-created_at")
    if filt != "all":
        subs = subs.filter(status=filt)
    plans = Plan.objects.all()
    return render(request, "admin_panel/subscriptions.html", {
        "subs": subs[:200],
        "filt": filt,
        "plans": plans,
        "status_choices": Subscription.STATUS_CHOICES,
    })


@admin_required
@require_POST
def update_subscription(request, sub_id):
    sub    = get_object_or_404(Subscription, pk=sub_id)
    action = request.POST.get("action", "")
    if action == "activate":
        sub.status = Subscription.STATUS_ACTIVE
        sub.period_start = timezone.now()
        sub.period_end   = timezone.now() + timedelta(days=30)
        sub.save()
        messages.success(request, "Subscription activated.")
    elif action == "cancel":
        sub.status = Subscription.STATUS_CANCELLED
        sub.save()
        messages.success(request, "Subscription cancelled.")
    elif action == "delete":
        sub.delete()
        messages.success(request, "Subscription deleted.")
        return redirect("admin_panel:subscriptions")
    return redirect("admin_panel:subscriptions")


# ─────────────────────────────────────────────────────────────────────────────
# Admin share creation (unlimited, any size)
# ─────────────────────────────────────────────────────────────────────────────

@admin_required
@require_GET
def admin_share_page(request):
    """Page where admin can share files with no restrictions."""
    return render(request, "admin_panel/admin_share.html", {
        "pbkdf2_iterations": settings.PBKDF2_ITERATIONS,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Plans management
# ─────────────────────────────────────────────────────────────────────────────

@admin_required
@require_http_methods(["GET", "POST"])
def plans_manage(request):
    plans = Plan.objects.order_by("sort_order")

    if request.method == "POST":
        action   = request.POST.get("action", "")
        plan_id  = request.POST.get("plan_id")

        if action == "toggle_active" and plan_id:
            plan = get_object_or_404(Plan, pk=plan_id)
            plan.active = not plan.active
            plan.save(update_fields=["active"])
            messages.success(request, f"Plan '{plan.name}' {'activated' if plan.active else 'deactivated'}.")

        elif action == "update_price" and plan_id:
            plan = get_object_or_404(Plan, pk=plan_id)
            try:
                plan.price_amount = float(request.POST.get("price_amount", plan.price_amount))
                plan.daily_share_limit = int(request.POST.get("daily_share_limit", plan.daily_share_limit))
                plan.save(update_fields=["price_amount", "daily_share_limit"])
                messages.success(request, f"Plan '{plan.name}' updated.")
            except (ValueError, TypeError) as exc:
                messages.error(request, f"Invalid value: {exc}")

        return redirect("admin_panel:plans")

    return render(request, "admin_panel/plans.html", {"plans": plans})


# ─────────────────────────────────────────────────────────────────────────────
# Quota management
# ─────────────────────────────────────────────────────────────────────────────

@admin_required
@require_http_methods(["GET", "POST"])
def quota_manage(request):
    today = timezone.localdate()
    usages = (
        QuotaUsage.objects
        .filter(date_key=today)
        .select_related()
        .order_by("-successful_shares")[:100]
    )

    if request.method == "POST":
        action      = request.POST.get("action", "")
        identity_id = request.POST.get("identity_id")
        id_type     = request.POST.get("identity_type")
        if action == "reset" and identity_id and id_type:
            QuotaUsage.objects.filter(
                identity_type=id_type,
                identity_id=identity_id,
                date_key=today,
            ).delete()
            messages.success(request, "Quota reset for that identity.")
        return redirect("admin_panel:quota")

    # Enrich with user info for display
    enriched = []
    for u in usages:
        label = u.identity_type + ":" + str(u.identity_id)
        if u.identity_type == QuotaUsage.IDENTITY_USER:
            try:
                usr = User.objects.get(pk=u.identity_id)
                label = usr.email
            except User.DoesNotExist:
                pass
        enriched.append({"usage": u, "label": label})

    return render(request, "admin_panel/quota.html", {
        "enriched": enriched,
        "today": today,
    })


# ─────────────────────────────────────────────────────────────────────────────
# JSON stats API for charts
# ─────────────────────────────────────────────────────────────────────────────

@admin_required
@require_GET
def stats_api(request):
    today = timezone.localdate()
    data  = {}
    for i in range(29, -1, -1):
        d   = today - timedelta(days=i)
        cnt = Share.objects.filter(created_at__date=d).count()
        data[d.isoformat()] = cnt
    return JsonResponse({"shares_30d": data})
