"""
shares/views.py  —  OTP-based file sharing
"""
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .models import GuestQuotaIdentity, QuotaUsage, Share, ShareOTPDelivery
from .services import (
    create_share,
    get_daily_limit,
    get_file_bytes,
    get_quota_remaining,
)

logger = logging.getLogger(__name__)

GUEST_COOKIE_NAME    = "askmyfile_gid"
GUEST_COOKIE_MAX_AGE = 60 * 60 * 24 * 30   # 30 days


# ---------------------------------------------------------------------------
# Guest identity
# ---------------------------------------------------------------------------

def _resolve_guest(request):
    """Return (GuestQuotaIdentity, raw_token)."""
    import secrets as _s
    raw = request.COOKIES.get(GUEST_COOKIE_NAME) or _s.token_urlsafe(32)
    return GuestQuotaIdentity.get_or_create_from_token(raw), raw


# ---------------------------------------------------------------------------
# POST /shares/create/
# ---------------------------------------------------------------------------

@require_POST
def create_share_view(request):
    """
    Multipart POST:
      file_data       – encrypted file bytes (Blob)
      salt_b64        – PBKDF2 salt used during client encryption
      iv_b64          – AES-GCM IV used during client encryption
      original_filename
      algorithm_version
      otp_channel     – 'screen' | 'email' | 'sms'  (registered users only)

    Returns JSON:
      ok, otp (if channel='screen'), channel, share_id,
      expires_at, quota_remaining, quota_limit, guest_quota_hit
    """
    user = request.user if request.user.is_authenticated else None
    guest_identity = guest_raw = None

    if not user:
        guest_identity, guest_raw = _resolve_guest(request)

    if user and not user.is_fully_verified:
        return JsonResponse({"ok": False, "error": "unverified_account"}, status=403)

    # --- file size limit ---
    file_obj = request.FILES.get("file_data")
    if not file_obj:
        return JsonResponse({"ok": False, "error": "missing_file"}, status=400)

    is_admin = getattr(user, "is_staff", False) if user else False

    if not is_admin:
        # Guests: MAX_FILE_SIZE_BYTES (100MB); verified free users: FREE_MAX_FILE_SIZE_BYTES (500MB)
        # Movie type has its own size check above; skip the general limit for movies
        if share_type != "movie":
            if user and user.is_authenticated:
                size_limit = settings.FREE_MAX_FILE_SIZE_BYTES
            else:
                size_limit = settings.MAX_FILE_SIZE_BYTES
            if file_obj.size > size_limit:
                return JsonResponse({"ok": False, "error": "file_too_large"}, status=413)

    salt_b64  = request.POST.get("salt_b64", "").strip()
    iv_b64    = request.POST.get("iv_b64",   "").strip()
    if not salt_b64 or not iv_b64:
        return JsonResponse({"ok": False, "error": "missing_crypto_params"}, status=400)

    original_filename = request.POST.get("original_filename", "")[:512]
    algorithm_version = request.POST.get("algorithm_version", "AES-GCM-256-v1").strip()
    share_type        = request.POST.get("share_type", "file").strip()

    # Validate share_type — document renamed to multimedia, movie added
    valid_types = ("file", "folder", "multimedia", "movie")
    if share_type not in valid_types:
        share_type = "file"

    # ── Movie: subscription-only with per-plan size limit ──────────────────
    if share_type == "movie":
        if not (user and user.is_authenticated):
            return JsonResponse({"ok": False, "error": "movie_requires_account"}, status=403)
        if is_admin:
            pass  # admin has no restriction
        else:
            # Look up the user's active plan movie limit
            movie_limit = 0   # default: not allowed (Free plan)
            try:
                active_sub = user.subscriptions.filter(
                    status="active"
                ).select_related("plan").latest("period_start")
                if active_sub.is_currently_active:
                    movie_limit = active_sub.plan.movie_size_limit_bytes
            except Exception:
                movie_limit = settings.MOVIE_SIZE_FREE

            if movie_limit == 0:
                return JsonResponse({"ok": False, "error": "movie_requires_subscription"}, status=403)
            if movie_limit != -1 and file_obj.size > movie_limit:
                limit_gb = movie_limit // (1024 ** 3)
                return JsonResponse({
                    "ok": False,
                    "error": "movie_too_large",
                    "limit_gb": limit_gb,
                }, status=413)

    # Channel logic:
    # - Guests/unauthenticated → screen only (OTP shown on screen)
    # - Registered verified users → email always, no user choice needed
    # - Admin (staff) → screen (for admin share page)
    if not (user and user.is_authenticated):
        otp_channel = ShareOTPDelivery.CHANNEL_SCREEN
    elif getattr(user, "is_staff", False):
        otp_channel = ShareOTPDelivery.CHANNEL_SCREEN  # admin sees it on screen
    else:
        # All registered verified users get the OTP via email automatically
        otp_channel = ShareOTPDelivery.CHANNEL_EMAIL

    file_bytes = file_obj.read()

    result = create_share(
        file_bytes        = file_bytes,
        salt_b64          = salt_b64,
        iv_b64            = iv_b64,
        original_filename = original_filename,
        algorithm_version = algorithm_version,
        share_type        = share_type,
        otp_channel       = otp_channel,
        user              = user,
        guest_identity    = guest_identity,
    )

    if not result["ok"]:
        if result["error"] == "quota_exceeded":
            from billing.models import Plan
            plans = list(Plan.objects.filter(active=True).order_by("sort_order").values(
                "id", "name", "daily_share_limit", "price_amount", "currency"
            ))
            return JsonResponse({
                "ok": False, "error": "quota_exceeded",
                "plans": plans,
                "daily_limit": get_daily_limit(user),
            }, status=429)
        return JsonResponse({"ok": False, "error": result["error"]}, status=400)

    share     = result["share"]
    raw_otp   = result["raw_otp"]
    channel   = result["channel"]
    remaining = result["quota_remaining"]
    limit     = get_daily_limit(user)

    payload = {
        "ok":              True,
        "share_id":        share.pk,
        "channel":         channel,
        "expires_at":      share.expires_at.isoformat(),
        "quota_remaining": remaining,
        "quota_limit":     limit,
        "guest_quota_hit": (not user and remaining == 0),
    }
    # Only reveal the OTP in the response for screen-channel
    if channel == ShareOTPDelivery.CHANNEL_SCREEN:
        payload["otp"] = raw_otp
    else:
        payload["otp"] = None   # sent via email/SMS

    response = JsonResponse(payload)
    if guest_raw:
        response.set_cookie(
            GUEST_COOKIE_NAME, guest_raw,
            max_age=GUEST_COOKIE_MAX_AGE,
            httponly=True, samesite="Lax",
            secure=not settings.DEBUG,
        )
    return response


# ---------------------------------------------------------------------------
# GET/POST /shares/access/  — OTP entry page (recipient)
# ---------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
def share_access(request):
    """
    Recipient lands here, enters the 6-digit OTP to access the file.
    GET  → show OTP input form
    POST → verify OTP and redirect to download or return JSON
    """
    if request.method == "GET":
        return render(request, "shares/share_access.html", {
            "pbkdf2_iterations": settings.PBKDF2_ITERATIONS,
        })

    # POST — JSON body from JS fetch
    import json
    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    raw_otp = str(body.get("otp", "")).strip()
    if not raw_otp or not raw_otp.isdigit() or len(raw_otp) != 6:
        return JsonResponse({"ok": False, "error": "invalid_otp_format"}, status=400)

    otp_hash = Share.hash_otp(raw_otp)

    # Find the most-recent active share matching this OTP hash
    share = (
        Share.objects
        .filter(access_otp_hash=otp_hash, status=Share.STATUS_ACTIVE)
        .order_by("-created_at")
        .first()
    )

    if share is None:
        return JsonResponse({"ok": False, "error": "otp_invalid"}, status=400)

    if not share.is_available:
        return JsonResponse({"ok": False, "error": "share_unavailable"}, status=410)

    # Store verified share id in session for the download endpoint
    request.session[f"otp_verified_{share.pk}"] = True
    request.session.modified = True

    return JsonResponse({
        "ok":       True,
        "share_id": share.pk,
        "salt_b64": share.salt,
        "iv_b64":   share.iv,
        "algorithm_version":  share.algorithm_version,
        "ciphertext_size":    share.ciphertext_size,
        "pbkdf2_iterations":  settings.PBKDF2_ITERATIONS,
    })


# ---------------------------------------------------------------------------
# GET /shares/<id>/download/  — verified download
# ---------------------------------------------------------------------------

@require_GET
def share_download(request, share_id: int):
    """
    Serve the encrypted file bytes after OTP verification.
    Session must contain otp_verified_<share_id>=True.
    """
    if not request.session.get(f"otp_verified_{share_id}"):
        return HttpResponse("OTP verification required.", status=403)

    share = Share.objects.filter(pk=share_id).first()
    if share is None or not share.is_available:
        return HttpResponse("Share unavailable.", status=410)

    ok = share.increment_download()
    if not ok:
        return HttpResponse("Share unavailable.", status=410)

    file_bytes = get_file_bytes(share)
    if file_bytes is None:
        logger.error("File missing in storage for share #%s", share.pk)
        return HttpResponse("File not found.", status=404)

    # Clear session key after one download to prevent replays
    del request.session[f"otp_verified_{share_id}"]
    request.session.modified = True

    response = HttpResponse(file_bytes, content_type="application/octet-stream")
    response["Content-Disposition"] = 'attachment; filename="encrypted_payload.bin"'
    response["Content-Length"]      = len(file_bytes)
    response["Referrer-Policy"]     = "no-referrer"
    response["X-Content-Type-Options"] = "nosniff"
    return response


# ---------------------------------------------------------------------------
# POST /shares/<id>/revoke/
# ---------------------------------------------------------------------------

@require_POST
@login_required
def revoke_share(request, share_id: int):
    share = get_object_or_404(Share, pk=share_id, owner_user=request.user)
    if share.revoked_at:
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"ok": False, "error": "already_revoked"})
        messages.warning(request, "Already revoked.")
        return redirect("core:home")
    share.revoke()
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"ok": True})
    messages.success(request, "Share revoked.")
    return redirect("core:home")


# ---------------------------------------------------------------------------
# GET /shares/list/
# ---------------------------------------------------------------------------

@login_required
@require_GET
def share_list(request):
    shares = Share.objects.filter(owner_user=request.user).order_by("-created_at")[:50]
    return render(request, "shares/share_list.html", {"shares": shares})


# ---------------------------------------------------------------------------
# GET /shares/quota/
# ---------------------------------------------------------------------------

@require_GET
def quota_status(request):
    user = request.user if request.user.is_authenticated else None
    guest_identity = None
    if not user:
        raw = request.COOKIES.get(GUEST_COOKIE_NAME)
        if raw:
            guest_identity = GuestQuotaIdentity.objects.filter(
                identifier_hash=GuestQuotaIdentity.hash_token(raw)
            ).first()
    remaining = get_quota_remaining(user=user, guest_identity=guest_identity)
    limit     = get_daily_limit(user)
    return JsonResponse({
        "quota_remaining": remaining,
        "quota_limit":     limit,
        "reset_at": timezone.localtime(timezone.now()).replace(
            hour=23, minute=59, second=59
        ).isoformat(),
    })
