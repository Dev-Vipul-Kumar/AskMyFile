import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .models import GuestQuotaIdentity, QuotaUsage, Share, ShareOTPDelivery
from .services import (
    create_share,
    deliver_otp,
    get_daily_limit,
    get_file_bytes,
    get_quota_remaining,
    _private_storage,
)

logger = logging.getLogger(__name__)

GUEST_COOKIE_NAME    = "askmyfile_gid"
GUEST_COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def _resolve_guest(request):
    import secrets as _s
    raw = request.COOKIES.get(GUEST_COOKIE_NAME) or _s.token_urlsafe(32)
    return GuestQuotaIdentity.get_or_create_from_token(raw), raw


@require_POST
def create_share_view(request):
    user = request.user if request.user.is_authenticated else None
    guest_identity = guest_raw = None

    if not user:
        guest_identity, guest_raw = _resolve_guest(request)

    if user and not user.is_fully_verified:
        return JsonResponse({"ok": False, "error": "unverified_account"}, status=403)

    file_obj = request.FILES.get("file_data")
    if not file_obj:
        return JsonResponse({"ok": False, "error": "missing_file"}, status=400)

    otp_hash_from_browser = request.POST.get("otp_hash", "").strip()
    if not otp_hash_from_browser or len(otp_hash_from_browser) != 64:
        return JsonResponse({"ok": False, "error": "missing_otp_hash"}, status=400)

    share_type = request.POST.get("share_type", "file").strip()
    if share_type not in ("file", "folder", "multimedia", "movie", "text"):
        share_type = "file"

    is_admin = getattr(user, "is_staff", False) if user else False

    if not is_admin and share_type != "movie":
        size_limit = settings.FREE_MAX_FILE_SIZE_BYTES if (user and user.is_authenticated) else settings.MAX_FILE_SIZE_BYTES
        if file_obj.size > size_limit:
            return JsonResponse({"ok": False, "error": "file_too_large"}, status=413)

    salt_b64  = request.POST.get("salt_b64", "").strip()
    iv_b64    = request.POST.get("iv_b64",   "").strip()
    if not salt_b64 or not iv_b64:
        return JsonResponse({"ok": False, "error": "missing_crypto_params"}, status=400)

    original_filename = request.POST.get("original_filename", "")[:512]
    algorithm_version = request.POST.get("algorithm_version", "AES-GCM-256-v1").strip()

    if share_type == "movie":
        if not (user and user.is_authenticated):
            return JsonResponse({"ok": False, "error": "movie_requires_account"}, status=403)
        if not is_admin:
            movie_limit = 0
            try:
                active_sub = user.subscriptions.filter(status="active").select_related("plan").latest("period_start")
                if active_sub.is_currently_active:
                    movie_limit = active_sub.plan.movie_size_limit_bytes
            except Exception:
                movie_limit = settings.MOVIE_SIZE_FREE
            if movie_limit == 0:
                return JsonResponse({"ok": False, "error": "movie_requires_subscription"}, status=403)
            if movie_limit != -1 and file_obj.size > movie_limit:
                return JsonResponse({"ok": False, "error": "movie_too_large",
                                     "limit_gb": movie_limit // (1024 ** 3)}, status=413)

    is_registered = user and user.is_authenticated and not getattr(user, "is_staff", False)

    if not (user and user.is_authenticated):
        otp_channel = ShareOTPDelivery.CHANNEL_SCREEN
    elif getattr(user, "is_staff", False):
        otp_channel = ShareOTPDelivery.CHANNEL_SCREEN
    else:
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
        otp_hash          = otp_hash_from_browser,
        user              = user,
        guest_identity    = guest_identity,
    )

    if not result["ok"]:
        if result["error"] == "quota_exceeded":
            from billing.models import Plan
            plans = list(Plan.objects.filter(active=True).order_by("sort_order").values(
                "id", "name", "daily_share_limit", "price_amount", "currency"
            ))
            return JsonResponse({"ok": False, "error": "quota_exceeded",
                                 "plans": plans, "daily_limit": get_daily_limit(user)}, status=429)
        return JsonResponse({"ok": False, "error": result["error"]}, status=400)

    share     = result["share"]
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
        "is_registered":   bool(is_registered),
    }

    response = JsonResponse(payload)
    if guest_raw:
        response.set_cookie(GUEST_COOKIE_NAME, guest_raw,
            max_age=GUEST_COOKIE_MAX_AGE, httponly=True,
            samesite="Lax", secure=not settings.DEBUG)
    return response


@require_POST
@login_required
def send_otp_email(request):
    import json as _json
    try:
        body = _json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    raw_otp  = str(body.get("otp", "")).strip()
    share_id = body.get("share_id")

    if not raw_otp or not raw_otp.isdigit() or len(raw_otp) != 6:
        return JsonResponse({"ok": False, "error": "invalid_otp"}, status=400)
    if not share_id:
        return JsonResponse({"ok": False, "error": "missing_share_id"}, status=400)

    try:
        share = Share.objects.get(pk=share_id, owner_user=request.user)
    except Share.DoesNotExist:
        return JsonResponse({"ok": False, "error": "share_not_found"}, status=404)

    if not share.check_otp(raw_otp):
        return JsonResponse({"ok": False, "error": "otp_mismatch"}, status=400)

    deliver_otp(share, raw_otp, ShareOTPDelivery.CHANNEL_EMAIL, user=request.user)
    return JsonResponse({"ok": True})


@require_http_methods(["GET", "POST"])
def share_access(request):
    if request.method == "GET":
        return render(request, "shares/share_access.html", {
            "pbkdf2_iterations": settings.PBKDF2_ITERATIONS,
        })

    import json
    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    raw_otp = str(body.get("otp", "")).strip()
    if not raw_otp or not raw_otp.isdigit() or len(raw_otp) != 6:
        return JsonResponse({"ok": False, "error": "invalid_otp_format"}, status=400)

    otp_hash = Share.hash_otp(raw_otp)

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

    request.session[f"otp_verified_{share.pk}"] = True
    request.session.modified = True

    return JsonResponse({
        "ok":                 True,
        "share_id":           share.pk,
        "salt_b64":           share.salt,
        "iv_b64":             share.iv,
        "algorithm_version":  share.algorithm_version,
        "ciphertext_size":    share.ciphertext_size,
        "pbkdf2_iterations":  settings.PBKDF2_ITERATIONS,
        "share_type":         share.share_type,
    })


@require_GET
def share_download(request, share_id: int):
    if not request.session.get(f"otp_verified_{share_id}"):
        return HttpResponse("OTP verification required.", status=403)

    share = Share.objects.filter(pk=share_id).first()
    if share is None or not share.is_available:
        return HttpResponse("Share unavailable.", status=410)

    ok = share.increment_download()
    if not ok:
        return HttpResponse("Share unavailable.", status=410)

    del request.session[f"otp_verified_{share_id}"]
    request.session.modified = True

    def file_iterator(storage_key, chunk_size=4 * 1024 * 1024):
        try:
            with _private_storage.open(storage_key, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        except Exception as exc:
            logger.error("Streaming error for share #%s: %s", share_id, exc)

    response = StreamingHttpResponse(
        file_iterator(share.storage_key),
        content_type="application/octet-stream",
    )
    response["Content-Disposition"] = 'attachment; filename="encrypted_payload.bin"'
    response["Content-Length"]      = share.ciphertext_size
    response["Referrer-Policy"]     = "no-referrer"
    response["X-Content-Type-Options"] = "nosniff"
    return response


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


@login_required
@require_GET
def share_list(request):
    shares = Share.objects.filter(owner_user=request.user).order_by("-created_at")[:50]
    return render(request, "shares/share_list.html", {"shares": shares})


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
