import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):

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
        extra_fields.setdefault("is_active", True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """
    Custom user model — email-only verification, no phone required.
    """

    email        = models.EmailField(unique=True, db_index=True)
    display_name = models.CharField(max_length=150, blank=True)

    email_verified = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    is_staff  = models.BooleanField(default=False)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD  = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table    = "accounts_user"
        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self):
        return self.email

    @property
    def is_fully_verified(self):
        """Account is ready to use once email is verified."""
        return self.email_verified

    @property
    def full_name(self):
        return self.display_name or self.email.split("@")[0]


class VerificationChallenge(models.Model):
    """
    Single-use email verification tokens and password-reset tokens.
    Raw token values are stored as SHA-256 hashes only.
    """

    CHANNEL_EMAIL          = "email"
    CHANNEL_PASSWORD_RESET = "password_reset"
    CHANNEL_CHOICES = [
        (CHANNEL_EMAIL,          "Email"),
        (CHANNEL_PASSWORD_RESET, "Password Reset"),
    ]

    user       = models.ForeignKey(User, on_delete=models.CASCADE,
                                   related_name="verification_challenges")
    channel    = models.CharField(max_length=20, choices=CHANNEL_CHOICES)
    token_hash = models.CharField(max_length=64, db_index=True)
    expires_at = models.DateTimeField()
    attempts   = models.PositiveSmallIntegerField(default=0)
    sent_at    = models.DateTimeField(default=timezone.now)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_verificationchallenge"
        indexes  = [models.Index(fields=["user", "channel", "consumed_at"])]

    def __str__(self):
        return f"{self.channel} challenge for {self.user.email}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @classmethod
    def _hash_token(cls, raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode()).hexdigest()

    @classmethod
    def create_for_user(cls, user, channel: str, ttl_minutes: int) -> str:
        """
        Create a new challenge, delete any previous unconsumed ones for the
        same channel, and return the raw (unhashed) URL-safe token.
        """
        cls.objects.filter(user=user, channel=channel, consumed_at__isnull=True).delete()

        raw = secrets.token_urlsafe(32)
        cls.objects.create(
            user=user,
            channel=channel,
            token_hash=cls._hash_token(raw),
            expires_at=timezone.now() + timedelta(minutes=ttl_minutes),
        )
        return raw

    @classmethod
    def verify(cls, user, channel: str, raw_token: str):
        """
        Validate a token. Returns (True, challenge) on success or
        (False, error_code) on failure.
        error codes: 'not_found', 'expired', 'invalid'
        """
        token_hash = cls._hash_token(raw_token)

        challenge = (
            cls.objects
            .filter(user=user, channel=channel, consumed_at__isnull=True)
            .order_by("-sent_at")
            .first()
        )

        if challenge is None:
            return False, "not_found"

        if challenge.expires_at < timezone.now():
            return False, "expired"

        if challenge.token_hash != token_hash:
            return False, "invalid"

        challenge.consumed_at = timezone.now()
        challenge.save(update_fields=["consumed_at"])
        return True, challenge
