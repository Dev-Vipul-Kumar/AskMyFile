"""
shares/services.py
OTP-based share creation.  The 6-digit OTP is the encryption key derivation
input — no separate passphrase is needed.
"""
import logging

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import GuestQuotaIdentity, QuotaUsage, Share, ShareOTPDelivery

logger = logging.getLogger(__name__)

_private_storage = FileSystemStorage(location=str(settings.MEDIA_ROOT))


# ---------------------------------------------------------------------------
# Quota helpers
# ---------------------------------------------------------------------------

def get_daily_limit(user=None) -> int:
    """Admin has no daily limit (returns a very large number)."""
    if user is None or not user.is_authenticated:
        return settings.GUEST_SHARES_PER_DAY
    if getattr(user, "is_staff", False):
        return 999999   # effectively unlimited
    try:
        sub = user.subscriptions.filter(status="active").select_related("plan").latest("period_start")
        return sub.plan.daily_share_limit
    except Exception:
        pass
    return settings.FREE_SHARES_PER_DAY


def get_quota_remaining(user=None, guest_identity=None) -> int:
    if user and user.is_authenticated:
        used  = QuotaUsage.get_used(QuotaUsage.IDENTITY_USER, user.pk)
        limit = get_daily_limit(user)
    elif guest_identity:
        used  = QuotaUsage.get_used(QuotaUsage.IDENTITY_GUEST, guest_identity.pk)
        limit = settings.GUEST_SHARES_PER_DAY
    else:
        return settings.GUEST_SHARES_PER_DAY
    return max(0, limit - used)


# ---------------------------------------------------------------------------
# OTP delivery
# ---------------------------------------------------------------------------

def deliver_otp(share: Share, raw_otp: str, channel: str, user=None) -> None:
    """Send/show the OTP via the chosen channel."""
    if channel == ShareOTPDelivery.CHANNEL_EMAIL and user and user.email:
        try:
            send_mail(
                subject="Your AskMyFile access code",
                message=(
                    f"Hello,\n\n"
                    f"Your 6-digit code to access the shared file is:\n\n"
                    f"    {raw_otp}\n\n"
                    f"This code expires when the share link is opened and used.\n"
                    f"Do not share this code with anyone other than the intended recipient.\n\n"
                    f"— AskMyFile"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )
            logger.info("Share OTP emailed to %s for share #%s", user.email, share.pk)
        except Exception as exc:
            logger.error("OTP email FAILED for share #%s to %s: %s", share.pk, user.email, exc)

    elif channel == ShareOTPDelivery.CHANNEL_SMS and user and getattr(user, "phone_number", ""):
        # TODO: wire to SMS provider using settings.SMS_PROVIDER_KEY
        logger.info(
            "[DEV] SMS OTP for share #%s → %s: %s",
            share.pk, user.phone_number, raw_otp,
        )

    # CHANNEL_SCREEN: OTP is returned in the API response — no delivery needed here


# ---------------------------------------------------------------------------
# Atomic share creation
# ---------------------------------------------------------------------------

def create_share(
    *,
    file_bytes: bytes,
    salt_b64: str,
    iv_b64: str,
    original_filename: str = "",
    share_type: str = "file",
    algorithm_version: str = "AES-GCM-256-v1",
    otp_channel: str = ShareOTPDelivery.CHANNEL_SCREEN,
    user=None,
    guest_identity: GuestQuotaIdentity | None = None,
) -> dict:
    """
    Atomically: check quota → store encrypted bytes → create Share → deliver OTP.

    Returns:
        ok=True  → { share, raw_otp, channel, quota_remaining }
        ok=False → { error }
    """
    # Determine identity
    if user and user.is_authenticated:
        identity_type = QuotaUsage.IDENTITY_USER
        identity_id   = user.pk
    elif guest_identity:
        identity_type = QuotaUsage.IDENTITY_GUEST
        identity_id   = guest_identity.pk
    else:
        return {"ok": False, "error": "no_identity"}

    # Registered users can choose email/SMS; guests are screen-only
    if not (user and user.is_authenticated):
        otp_channel = ShareOTPDelivery.CHANNEL_SCREEN

    # Expiry
    if user and user.is_authenticated:
        expiry_delta = timezone.timedelta(hours=settings.ACCOUNT_LINK_EXPIRY_HOURS)
    else:
        guest_minutes = getattr(settings, "GUEST_LINK_EXPIRY_MINUTES", 1)
        expiry_delta  = timezone.timedelta(minutes=guest_minutes)

    # Generate OTP and storage key
    raw_otp     = Share.generate_otp()
    otp_hash    = Share.hash_otp(raw_otp)
    storage_key = Share.generate_storage_key()

    # Admin users: skip quota entirely
    is_admin = user and user.is_authenticated and getattr(user, "is_staff", False)

    with transaction.atomic():
        if not is_admin:
            usage, _ = QuotaUsage.objects.select_for_update().get_or_create(
                identity_type=identity_type,
                identity_id=identity_id,
                date_key=timezone.localdate(),
                defaults={"successful_shares": 0},
            )
            limit = get_daily_limit(user)
            if usage.successful_shares >= limit:
                return {"ok": False, "error": "quota_exceeded"}

        # Persist encrypted bytes
        _private_storage.save(storage_key, ContentFile(file_bytes))

        share = Share.objects.create(
            owner_user        = user if (user and user.is_authenticated) else None,
            guest_identity    = guest_identity if not (user and user.is_authenticated) else None,
            access_otp_hash   = otp_hash,
            storage_key       = storage_key,
            ciphertext_size   = len(file_bytes),
            algorithm_version = algorithm_version,
            salt              = salt_b64,
            iv                = iv_b64,
            expires_at        = timezone.now() + expiry_delta,
            original_filename = original_filename[:512],
            share_type        = share_type,
        )

        ShareOTPDelivery.objects.create(
            share   = share,
            channel = otp_channel,
            sent_at = timezone.now() if otp_channel != ShareOTPDelivery.CHANNEL_SCREEN else None,
        )

        if not is_admin:
            usage.successful_shares += 1
            usage.save(update_fields=["successful_shares", "updated_at"])

    # Deliver OTP outside the transaction (email/SMS I/O)
    deliver_otp(share, raw_otp, otp_channel, user=user)

    remaining = get_quota_remaining(user=user, guest_identity=guest_identity)
    return {
        "ok":              True,
        "share":           share,
        "raw_otp":         raw_otp,
        "channel":         otp_channel,
        "quota_remaining": remaining,
    }


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def get_file_bytes(share: Share) -> bytes | None:
    try:
        with _private_storage.open(share.storage_key, "rb") as f:
            return f.read()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup_expired_shares(dry_run: bool = False) -> dict:
    now = timezone.now()
    candidates = Share.objects.filter(
        status__in=[Share.STATUS_ACTIVE, Share.STATUS_EXHAUSTED, Share.STATUS_REVOKED],
        expires_at__lt=now,
    )
    deleted = errors = 0
    for share in candidates:
        if dry_run:
            deleted += 1
            continue
        try:
            if _private_storage.exists(share.storage_key):
                _private_storage.delete(share.storage_key)
            share.status = Share.STATUS_EXPIRED
            share.save(update_fields=["status"])
            deleted += 1
        except Exception:
            errors += 1
    return {"deleted": deleted, "errors": errors}
