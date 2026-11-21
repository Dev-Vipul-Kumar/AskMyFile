import logging
import math
import secrets
import shutil
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.files import File
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from billing.models import Subscription
from .models import GuestQuotaIdentity, QuotaUsage, Share, ShareOTPDelivery
from .services import (
    cleanup_pending_uploads,
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
LARGE_UPLOAD_CHUNK_SIZE = 32 * 1024 * 1024
LARGE_UPLOAD_TICKET_SALT = "shares.large_upload"


def _resolve_guest(request):
    import secrets as _s
    raw = request.COOKIES.get(GUEST_COOKIE_NAME) or _s.token_urlsafe(32)
    return GuestQuotaIdentity.get_or_create_from_token(raw), raw


def _validate_share_size(user, share_type, size):
    is_admin = bool(user and getattr(user, "is_staff", False))
    if not is_admin and share_type != "movie":
        size_limit = settings.FREE_MAX_FILE_SIZE_BYTES if user else settings.MAX_FILE_SIZE_BYTES
        if size > size_limit:
            return {"ok": False, "error": "file_too_large"}, 413
    elif share_type == "movie":
        if not user:
            return {"ok": False, "error": "movie_requires_account"}, 403
        if not is_admin:
            movie_limit = settings.MOVIE_SIZE_FREE
            try:
                active_sub = user.subscriptions.filter(status="active").select_related("plan").latest("period_start")
                if active_sub.is_currently_active:
                    movie_limit = active_sub.plan.movie_size_limit_bytes
            except Subscription.DoesNotExist:
                pass
            if movie_limit == 0:
                return {"ok": False, "error": "movie_requires_subscription"}, 403
            if movie_limit != -1 and size > movie_limit:
                return {
                    "ok": False,
                    "error": "movie_too_large",
                    "limit_gb": movie_limit // (1024 ** 3),
                }, 413
    return None


def _pending_upload_root():
    root = Path(settings.MEDIA_ROOT) / ".pending_uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _load_upload_ticket(request):
    token = request.POST.get("ticket", "")
    try:
        ticket = signing.loads(token, salt=LARGE_UPLOAD_TICKET_SALT, max_age=24 * 60 * 60)
    except signing.BadSignature:
        return None, JsonResponse({"ok": False, "error": "upload_expired"}, status=400)

    upload_id = ticket.get("upload_id", "")
    if len(upload_id) != 32 or any(char not in "0123456789abcdef" for char in upload_id):
        return None, JsonResponse({"ok": False, "error": "invalid_upload"}, status=400)

    owner = ticket.get("owner", "")
    if owner.startswith("user:"):
        if not request.user.is_authenticated or str(request.user.pk) != owner[5:]:
            return None, JsonResponse({"ok": False, "error": "upload_owner_mismatch"}, status=403)
    else:
        raw_guest = request.COOKIES.get(GUEST_COOKIE_NAME, "")
        if not raw_guest or GuestQuotaIdentity.hash_token(raw_guest) != owner[6:]:
            return None, JsonResponse({"ok": False, "error": "upload_owner_mismatch"}, status=403)
    return ticket, None


def _share_result_response(result, user, guest_identity, guest_raw=None):
    if not result["ok"]:
        if result["error"] == "quota_exceeded":
            from billing.models import Plan
            plans = list(Plan.objects.filter(active=True).order_by("sort_order").values(
                "id", "name", "daily_share_limit", "price_amount", "currency"
            ))
            return JsonResponse({"ok": False, "error": "quota_exceeded",
                                 "plans": plans, "daily_limit": get_daily_limit(user)}, status=429)
        return JsonResponse({"ok": False, "error": result["error"]}, status=400)

    share = result["share"]
    remaining = result["quota_remaining"]
    payload = {
        "ok": True,
        "share_id": share.pk,
        "channel": result["channel"],
        "expires_at": share.expires_at.isoformat(),
        "quota_remaining": remaining,
        "quota_limit": get_daily_limit(user),
        "guest_quota_hit": (not user and remaining == 0),
        "is_registered": bool(user and user.is_authenticated and not getattr(user, "is_staff", False)),
    }
    response = JsonResponse(payload)
    if guest_raw:
        response.set_cookie(GUEST_COOKIE_NAME, guest_raw,
            max_age=GUEST_COOKIE_MAX_AGE, httponly=True,
            samesite="Lax", secure=not settings.DEBUG)
    return response


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

    size_error = _validate_share_size(user, share_type, file_obj.size)
    if size_error:
        error, status = size_error
        return JsonResponse(error, status=status)

    salt_b64  = request.POST.get("salt_b64", "").strip()
    iv_b64    = request.POST.get("iv_b64",   "").strip()
    if not salt_b64 or not iv_b64:
        return JsonResponse({"ok": False, "error": "missing_crypto_params"}, status=400)

    original_filename = request.POST.get("original_filename", "")[:512]
    algorithm_version = request.POST.get("algorithm_version", "AES-GCM-256-v1").strip()

    if not (user and user.is_authenticated):
        otp_channel = ShareOTPDelivery.CHANNEL_SCREEN
    elif getattr(user, "is_staff", False):
        otp_channel = ShareOTPDelivery.CHANNEL_SCREEN
    else:
        otp_channel = ShareOTPDelivery.CHANNEL_EMAIL

    result = create_share(
        file_obj         = file_obj,
        ciphertext_size  = file_obj.size,
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

    return _share_result_response(result, user, guest_identity, guest_raw)


@require_POST
def large_upload_start(request):
    user = request.user if request.user.is_authenticated else None
    if user and not user.is_fully_verified:
        return JsonResponse({"ok": False, "error": "unverified_account"}, status=403)

    guest_identity = guest_raw = None
    if not user:
        guest_identity, guest_raw = _resolve_guest(request)

    try:
        ciphertext_size = int(request.POST.get("ciphertext_size", ""))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "invalid_upload_size"}, status=400)
    if ciphertext_size <= LARGE_UPLOAD_CHUNK_SIZE or ciphertext_size > (2 ** 63 - 1):
        return JsonResponse({"ok": False, "error": "upload_not_large"}, status=400)

    share_type = request.POST.get("share_type", "file").strip()
    if share_type not in ("file", "folder", "multimedia", "movie", "text"):
        share_type = "file"
    size_error = _validate_share_size(user, share_type, ciphertext_size)
    if size_error:
        error, status = size_error
        return JsonResponse(error, status=status)

    salt_b64 = request.POST.get("salt_b64", "").strip()
    iv_b64 = request.POST.get("iv_b64", "").strip()
    otp_hash = request.POST.get("otp_hash", "").strip()
    if not salt_b64 or not iv_b64:
        return JsonResponse({"ok": False, "error": "missing_crypto_params"}, status=400)
    if len(otp_hash) != 64:
        return JsonResponse({"ok": False, "error": "missing_otp_hash"}, status=400)

    root = _pending_upload_root()
    cleanup_result = cleanup_pending_uploads()
    if cleanup_result["errors"]:
        logger.warning(
            "Could not remove %s stale upload staging directories",
            cleanup_result["errors"],
        )
    upload_id = secrets.token_hex(16)
    (root / upload_id).mkdir()
    owner = (
        f"user:{user.pk}" if user
        else f"guest:{GuestQuotaIdentity.hash_token(guest_raw)}"
    )
    ticket_data = {
        "upload_id": upload_id,
        "owner": owner,
        "ciphertext_size": ciphertext_size,
        "chunk_count": math.ceil(ciphertext_size / LARGE_UPLOAD_CHUNK_SIZE),
        "salt_b64": salt_b64,
        "iv_b64": iv_b64,
        "otp_hash": otp_hash,
        "share_type": share_type,
        "original_filename": request.POST.get("original_filename", "")[:512],
        "algorithm_version": request.POST.get("algorithm_version", "AES-GCM-256-v1").strip(),
    }
    response = JsonResponse({
        "ok": True,
        "ticket": signing.dumps(ticket_data, salt=LARGE_UPLOAD_TICKET_SALT),
        "chunk_size": LARGE_UPLOAD_CHUNK_SIZE,
    })
    if guest_raw:
        response.set_cookie(GUEST_COOKIE_NAME, guest_raw,
            max_age=GUEST_COOKIE_MAX_AGE, httponly=True,
            samesite="Lax", secure=not settings.DEBUG)
    return response


@require_POST
def large_upload_chunk(request):
    ticket, error = _load_upload_ticket(request)
    if error:
        return error
    try:
        index = int(request.POST.get("chunk_index", ""))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "invalid_chunk_index"}, status=400)
    if index < 0 or index >= ticket["chunk_count"]:
        return JsonResponse({"ok": False, "error": "invalid_chunk_index"}, status=400)
    chunk = request.FILES.get("chunk_data")
    if not chunk:
        return JsonResponse({"ok": False, "error": "missing_chunk"}, status=400)
    expected_size = min(
        LARGE_UPLOAD_CHUNK_SIZE,
        ticket["ciphertext_size"] - index * LARGE_UPLOAD_CHUNK_SIZE,
    )
    if chunk.size != expected_size:
        return JsonResponse({"ok": False, "error": "invalid_chunk_size"}, status=400)

    chunk_path = _pending_upload_root() / ticket["upload_id"] / f"{index:08d}.part"
    if not chunk_path.parent.is_dir():
        return JsonResponse({"ok": False, "error": "upload_expired"}, status=400)
    with chunk_path.open("wb") as target:
        for data in chunk.chunks():
            target.write(data)
    return JsonResponse({"ok": True, "chunk_index": index})


@require_POST
def large_upload_complete(request):
    ticket, error = _load_upload_ticket(request)
    if error:
        return error
    user = request.user if request.user.is_authenticated else None
    if not user:
        raw_guest = request.COOKIES.get(GUEST_COOKIE_NAME, "")
        guest_identity = GuestQuotaIdentity.objects.get(
            identifier_hash=GuestQuotaIdentity.hash_token(raw_guest)
        )
    else:
        guest_identity = None

    upload_root = _pending_upload_root()
    upload_dir = upload_root / ticket["upload_id"]
    if not upload_dir.is_dir():
        return JsonResponse({"ok": False, "error": "upload_expired"}, status=400)
    processing_dir = upload_root / f"{ticket['upload_id']}.processing"
    try:
        upload_dir.rename(processing_dir)
    except (FileNotFoundError, FileExistsError):
        return JsonResponse({"ok": False, "error": "upload_expired"}, status=400)
    payload_path = processing_dir / "ciphertext.bin"
    try:
        with payload_path.open("wb") as payload:
            for index in range(ticket["chunk_count"]):
                chunk_path = processing_dir / f"{index:08d}.part"
                if not chunk_path.is_file():
                    return JsonResponse({"ok": False, "error": "missing_chunk"}, status=400)
                with chunk_path.open("rb") as chunk:
                    shutil.copyfileobj(chunk, payload, length=4 * 1024 * 1024)
                chunk_path.unlink()
        if payload_path.stat().st_size != ticket["ciphertext_size"]:
            return JsonResponse({"ok": False, "error": "invalid_upload_size"}, status=400)

        if user and getattr(user, "is_staff", False):
            otp_channel = ShareOTPDelivery.CHANNEL_SCREEN
        elif user:
            otp_channel = ShareOTPDelivery.CHANNEL_EMAIL
        else:
            otp_channel = ShareOTPDelivery.CHANNEL_SCREEN

        with payload_path.open("rb") as payload:
            result = create_share(
                file_obj=File(payload),
                ciphertext_size=ticket["ciphertext_size"],
                salt_b64=ticket["salt_b64"],
                iv_b64=ticket["iv_b64"],
                original_filename=ticket["original_filename"],
                algorithm_version=ticket["algorithm_version"],
                share_type=ticket["share_type"],
                otp_channel=otp_channel,
                otp_hash=ticket["otp_hash"],
                user=user,
                guest_identity=guest_identity,
            )
        response = _share_result_response(result, user, guest_identity)
        return response
    finally:
        if processing_dir.exists():
            shutil.rmtree(processing_dir)


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
