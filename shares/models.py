import hashlib
import secrets
from datetime import date

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone


class GuestQuotaIdentity(models.Model):
    identifier_hash = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "shares_guestquotaidentity"

    def __str__(self):
        return f"Guest #{self.pk} ({self.identifier_hash[:8]}…)"

    @classmethod
    def hash_token(cls, raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode()).hexdigest()

    @classmethod
    def get_or_create_from_token(cls, raw_token: str):
        hashed = cls.hash_token(raw_token)
        obj, created = cls.objects.get_or_create(
            identifier_hash=hashed,
            defaults={"created_at": timezone.now(), "last_seen_at": timezone.now()},
        )
        if not created:
            obj.last_seen_at = timezone.now()
            obj.save(update_fields=["last_seen_at"])
        return obj


class QuotaUsage(models.Model):
    IDENTITY_USER  = "user"
    IDENTITY_GUEST = "guest"

    identity_type     = models.CharField(max_length=10)
    identity_id       = models.BigIntegerField()
    date_key          = models.DateField(db_index=True)
    successful_shares = models.PositiveIntegerField(default=0)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "shares_quotausage"
        unique_together = [("identity_type", "identity_id", "date_key")]

    def __str__(self):
        return f"{self.identity_type}:{self.identity_id} on {self.date_key} – {self.successful_shares}"

    @classmethod
    def _today(cls) -> date:
        return timezone.localdate()

    @classmethod
    def get_used(cls, identity_type: str, identity_id: int) -> int:
        try:
            row = cls.objects.get(
                identity_type=identity_type,
                identity_id=identity_id,
                date_key=cls._today(),
            )
            return row.successful_shares
        except cls.DoesNotExist:
            return 0

    @classmethod
    def increment(cls, identity_type: str, identity_id: int) -> int:
        today = cls._today()
        row, _ = cls.objects.select_for_update().get_or_create(
            identity_type=identity_type,
            identity_id=identity_id,
            date_key=today,
            defaults={"successful_shares": 0},
        )
        row.successful_shares += 1
        row.save(update_fields=["successful_shares", "updated_at"])
        return row.successful_shares


class Share(models.Model):
    STATUS_ACTIVE    = "active"
    STATUS_EXPIRED   = "expired"
    STATUS_REVOKED   = "revoked"
    STATUS_EXHAUSTED = "exhausted"
    STATUS_CHOICES = [
        (STATUS_ACTIVE,    "Active"),
        (STATUS_EXPIRED,   "Expired"),
        (STATUS_REVOKED,   "Revoked"),
        (STATUS_EXHAUSTED, "Exhausted"),
    ]

    TYPE_FILE       = "file"
    TYPE_FOLDER     = "folder"
    TYPE_MULTIMEDIA = "multimedia"
    TYPE_MOVIE      = "movie"
    TYPE_TEXT       = "text"
    TYPE_CHOICES    = [
        (TYPE_FILE,       "Document / File"),
        (TYPE_FOLDER,     "Folder / Archive"),
        (TYPE_MULTIMEDIA, "Multimedia (Image / Video)"),
        (TYPE_MOVIE,      "Movie"),
        (TYPE_TEXT,       "Text"),
    ]

    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="shares",
    )
    guest_identity = models.ForeignKey(
        GuestQuotaIdentity,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="shares",
    )

    access_otp_hash   = models.CharField(max_length=64, db_index=True)
    storage_key       = models.CharField(max_length=255, unique=True)
    ciphertext_size   = models.PositiveBigIntegerField(default=0)
    algorithm_version = models.CharField(max_length=20, default="AES-GCM-256-v1")
    salt              = models.CharField(max_length=64)
    iv                = models.CharField(max_length=32)
    expires_at        = models.DateTimeField()
    max_downloads     = models.PositiveIntegerField(default=0)
    download_count    = models.PositiveIntegerField(default=0)
    revoked_at        = models.DateTimeField(null=True, blank=True)
    status            = models.CharField(max_length=12, choices=STATUS_CHOICES,
                                         default=STATUS_ACTIVE, db_index=True)
    created_at        = models.DateTimeField(default=timezone.now)
    original_filename = models.CharField(max_length=512, blank=True)
    share_type        = models.CharField(max_length=12, choices=TYPE_CHOICES, default=TYPE_FILE)

    class Meta:
        db_table = "shares_share"
        indexes = [
            models.Index(fields=["access_otp_hash", "status"]),
            models.Index(fields=["owner_user", "created_at"]),
        ]

    def __str__(self):
        owner = self.owner_user or self.guest_identity
        return f"Share #{self.pk} by {owner} ({self.status})"

    @staticmethod
    def hash_otp(raw_otp: str) -> str:
        return hashlib.sha256(raw_otp.encode()).hexdigest()

    @staticmethod
    def generate_otp() -> str:
        return "".join([str(secrets.randbelow(10)) for _ in range(6)])

    @staticmethod
    def generate_storage_key() -> str:
        return secrets.token_hex(24)

    @property
    def is_available(self) -> bool:
        now = timezone.now()
        return (
            self.status == self.STATUS_ACTIVE
            and self.revoked_at is None
            and self.expires_at > now
            and (self.max_downloads == 0 or self.download_count < self.max_downloads)
        )

    def check_otp(self, raw_otp: str) -> bool:
        import hmac as _hmac
        return _hmac.compare_digest(self.access_otp_hash, self.hash_otp(raw_otp))

    def increment_download(self) -> bool:
        with transaction.atomic():
            share = Share.objects.select_for_update().get(pk=self.pk)
            if not share.is_available:
                return False
            share.download_count += 1
            if share.max_downloads > 0 and share.download_count >= share.max_downloads:
                share.status = Share.STATUS_EXHAUSTED
            share.save(update_fields=["download_count", "status"])
            self.download_count = share.download_count
            self.status = share.status
        return True

    def revoke(self):
        self.revoked_at = timezone.now()
        self.status = self.STATUS_REVOKED
        self.save(update_fields=["revoked_at", "status"])


class ShareOTPDelivery(models.Model):
    CHANNEL_EMAIL  = "email"
    CHANNEL_SMS    = "sms"
    CHANNEL_SCREEN = "screen"
    CHANNEL_CHOICES = [
        (CHANNEL_EMAIL,  "Email"),
        (CHANNEL_SMS,    "SMS"),
        (CHANNEL_SCREEN, "Screen"),
    ]

    share   = models.OneToOneField(Share, on_delete=models.CASCADE, related_name="otp_delivery")
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES, default=CHANNEL_SCREEN)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "shares_sharotpdelivery"

    def __str__(self):
        return f"OTP delivery for share #{self.share_id} via {self.channel}"
