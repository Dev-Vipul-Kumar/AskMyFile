import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    """Custom manager for the AskMyFile User model."""

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("email_verified", True)
        extra_fields.setdefault("phone_verified", True)
        extra_fields.setdefault("is_active", True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """
    Custom user model.
    Accounts become usable only after both email_verified and phone_verified
    are True (enforced at view/middleware level).
    """

    email = models.EmailField(unique=True, db_index=True)
    display_name = models.CharField(max_length=150, blank=True)
    phone_number = models.CharField(max_length=20, blank=True, db_index=True)

    email_verified = models.BooleanField(default=False)
    phone_verified = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "accounts_user"
        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self):
        return self.email

    @property
    def is_fully_verified(self):
        """True only when both verification gates are passed."""
        return self.email_verified and self.phone_verified

    @property
    def full_name(self):
        return self.display_name or self.email.split("@")[0]


class VerificationChallenge(models.Model):
    """
    Stores single-use email or SMS verification tokens/OTPs.
    Token/OTP values are stored as HMAC-SHA256 hashes so raw secrets
    are never readable from the database.
    """

    CHANNEL_EMAIL = "email"
    CHANNEL_SMS = "sms"
    CHANNEL_PASSWORD_RESET = "password_reset"
    CHANNEL_CHOICES = [
        (CHANNEL_EMAIL, "Email"),
        (CHANNEL_SMS, "SMS"),
        (CHANNEL_PASSWORD_RESET, "Password Reset"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="verification_challenges")
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES)
    token_hash = models.CharField(max_length=64, db_index=True)  # hex SHA-256
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    sent_at = models.DateTimeField(default=timezone.now)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_verificationchallenge"
        indexes = [
            models.Index(fields=["user", "channel", "consumed_at"]),
        ]

    def __str__(self):
        return f"{self.channel} challenge for {self.user.email}"

    # ------------------------------------------------------------------
    # Class-level helpers
    # ------------------------------------------------------------------

    @classmethod
    def _hash_token(cls, raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode()).hexdigest()

    @classmethod
    def create_for_user(cls, user, channel: str, ttl_minutes: int, length: int = 0) -> str:
        """
        Create a new challenge and return the raw (unhashed) token/OTP.
        Invalidates any previous unconsumed challenge for the same channel.
        length=0 means use a 32-byte URL-safe hex string (for email links);
        length>0 generates a numeric OTP of that many digits.
        """
        # Expire existing challenges for this channel
        cls.objects.filter(
            user=user, channel=channel, consumed_at__isnull=True
        ).update(expires_at=timezone.now())

        if length > 0:
            raw = "".join([str(secrets.randbelow(10)) for _ in range(length)])
        else:
            raw = secrets.token_urlsafe(32)

        challenge = cls.objects.create(
            user=user,
            channel=channel,
            token_hash=cls._hash_token(raw),
            expires_at=timezone.now() + timedelta(minutes=ttl_minutes),
        )
        return raw

    @classmethod
    def verify(cls, user, channel: str, raw_token: str, max_attempts: int = 5):
        """
        Validate token/OTP.  Returns (True, challenge) on success or
        (False, error_code) on failure.
        error codes: 'not_found', 'expired', 'too_many_attempts', 'invalid'
        """
        token_hash = cls._hash_token(raw_token)
        try:
            challenge = cls.objects.get(
                user=user,
                channel=channel,
                consumed_at__isnull=True,
            )
        except cls.DoesNotExist:
            return False, "not_found"

        if challenge.expires_at < timezone.now():
            return False, "expired"

        if challenge.attempts >= max_attempts:
            return False, "too_many_attempts"

        challenge.attempts += 1

        if challenge.token_hash != token_hash:
            challenge.save(update_fields=["attempts"])
            return False, "invalid"

        challenge.consumed_at = timezone.now()
        challenge.save(update_fields=["attempts", "consumed_at"])
        return True, challenge
