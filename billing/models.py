"""
billing/models.py
Plan catalogue, Subscription, and PaymentEvent.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Plan(models.Model):
    """
    Configurable plan definitions. Values live in the database so they
    can be edited from the admin without a code deploy.
    """
    INTERVAL_MONTHLY = "monthly"
    INTERVAL_ANNUAL = "annual"
    INTERVAL_CHOICES = [
        (INTERVAL_MONTHLY, "Monthly"),
        (INTERVAL_ANNUAL, "Annual"),
    ]

    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(max_length=50, unique=True)
    daily_share_limit = models.PositiveIntegerField()
    price_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="INR")
    billing_interval = models.CharField(max_length=10, choices=INTERVAL_CHOICES, default=INTERVAL_MONTHLY)
    description = models.TextField(blank=True)
    is_free = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    # Movie sharing: 0 = not allowed, -1 = unlimited, positive int = bytes limit
    movie_size_limit_bytes = models.BigIntegerField(default=0)

    class Meta:
        db_table = "billing_plan"
        ordering = ["sort_order"]

    def __str__(self):
        return f"{self.name} ({self.currency} {self.price_amount}/{self.billing_interval})"

    @property
    def price_display(self):
        if self.is_free or self.price_amount == 0:
            return "Free"
        symbol = "₹" if self.currency == "INR" else self.currency
        return f"{symbol}{self.price_amount}/{self.billing_interval}"


class Subscription(models.Model):
    """
    Active subscription record for a user.
    Status is ONLY updated after server-side webhook verification.
    """
    STATUS_PENDING = "pending"
    STATUS_ACTIVE = "active"
    STATUS_PAST_DUE = "past_due"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"
    STATUS_REFUNDED = "refunded"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_PAST_DUE, "Past Due"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_REFUNDED, "Refunded"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    provider_customer_ref      = models.CharField(max_length=255, blank=True)
    provider_subscription_ref  = models.CharField(max_length=255, blank=True, db_index=True)
    # Razorpay-specific fields
    razorpay_order_id          = models.CharField(max_length=255, blank=True, db_index=True)
    razorpay_payment_id        = models.CharField(max_length=255, blank=True)
    razorpay_signature         = models.CharField(max_length=512, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "billing_subscription"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.email} – {self.plan.name} ({self.status})"

    @property
    def is_currently_active(self):
        if self.status != self.STATUS_ACTIVE:
            return False
        now = timezone.now()
        if self.period_end and self.period_end < now:
            return False
        return True


class PaymentEvent(models.Model):
    """
    Immutable log of provider webhook events.
    The unique provider_event_id constraint ensures idempotent processing.
    Never store card numbers or CVV here.
    """
    STATUS_RECEIVED = "received"
    STATUS_PROCESSED = "processed"
    STATUS_FAILED = "failed"
    STATUS_DUPLICATE = "duplicate"
    STATUS_CHOICES = [
        (STATUS_RECEIVED, "Received"),
        (STATUS_PROCESSED, "Processed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_DUPLICATE, "Duplicate"),
    ]

    provider_event_id = models.CharField(max_length=255, unique=True, db_index=True)
    event_type = models.CharField(max_length=100)
    subscription = models.ForeignKey(
        Subscription,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="payment_events",
    )
    raw_payload = models.TextField(blank=True)   # sanitised JSON for reconciliation
    processing_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_RECEIVED)
    error_detail = models.TextField(blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "billing_paymentevent"
        ordering = ["-received_at"]

    def __str__(self):
        return f"PaymentEvent {self.provider_event_id} [{self.event_type}] – {self.processing_status}"
