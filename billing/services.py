"""
billing/services.py
Payment webhook processing and subscription state management.
Entitlements are ONLY updated after verified provider events.
"""
import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.utils import timezone

from .models import PaymentEvent, Plan, Subscription

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Webhook signature verification (provider-agnostic stub)
# ---------------------------------------------------------------------------

def verify_webhook_signature(payload: bytes, signature_header: str) -> bool:
    """
    Verify that the webhook payload was signed by the payment provider.
    This stub uses HMAC-SHA256 with the PAYMENT_WEBHOOK_SECRET setting.
    Replace with the actual provider SDK verification in production.
    """
    secret = settings.PAYMENT_WEBHOOK_SECRET
    if not secret:
        logger.warning("PAYMENT_WEBHOOK_SECRET is not set; skipping signature check in dev mode.")
        return settings.DEBUG  # Only allow unsigned in DEBUG

    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header or "")


# ---------------------------------------------------------------------------
# Idempotent webhook event processing
# ---------------------------------------------------------------------------

def process_webhook_event(provider_event_id: str, event_type: str, payload_json: str) -> dict:
    """
    Process a payment provider webhook event idempotently.
    Returns dict: { ok, action, event_id }
    """
    # --- Idempotency: reject already-processed events ---
    if PaymentEvent.objects.filter(provider_event_id=provider_event_id).exists():
        logger.info("Duplicate webhook event ignored: %s", provider_event_id)
        PaymentEvent.objects.filter(provider_event_id=provider_event_id).update(
            processing_status=PaymentEvent.STATUS_DUPLICATE
        )
        return {"ok": True, "action": "duplicate_ignored", "event_id": provider_event_id}

    event = PaymentEvent.objects.create(
        provider_event_id=provider_event_id,
        event_type=event_type,
        raw_payload=payload_json[:10000],  # cap size; never store card data
        processing_status=PaymentEvent.STATUS_RECEIVED,
    )

    try:
        data = json.loads(payload_json)
        action = _dispatch_event(event, event_type, data)
        event.processing_status = PaymentEvent.STATUS_PROCESSED
        event.processed_at = timezone.now()
        event.save(update_fields=["processing_status", "processed_at", "subscription"])
        return {"ok": True, "action": action, "event_id": provider_event_id}
    except Exception as exc:
        event.processing_status = PaymentEvent.STATUS_FAILED
        event.error_detail = str(exc)[:500]
        event.save(update_fields=["processing_status", "error_detail"])
        logger.exception("Webhook processing failed for event %s", provider_event_id)
        return {"ok": False, "action": "error", "event_id": provider_event_id}


def _dispatch_event(event: PaymentEvent, event_type: str, data: dict) -> str:
    """Route webhook event type to the appropriate handler."""
    handlers = {
        "payment.success": _handle_payment_success,
        "subscription.activated": _handle_subscription_activated,
        "subscription.renewed": _handle_subscription_renewed,
        "subscription.cancelled": _handle_subscription_cancelled,
        "subscription.expired": _handle_subscription_expired,
        "payment.failed": _handle_payment_failed,
        "payment.refunded": _handle_payment_refunded,
    }
    handler = handlers.get(event_type)
    if handler:
        return handler(event, data)
    logger.info("Unhandled webhook event type: %s", event_type)
    return "unhandled"


def _get_or_create_subscription(data: dict) -> Subscription | None:
    provider_ref = data.get("subscription_id", "")
    user_email = data.get("customer_email", "")
    plan_slug = data.get("plan_slug", "")

    if not provider_ref:
        return None

    sub = Subscription.objects.filter(provider_subscription_ref=provider_ref).first()
    if sub:
        return sub

    # Try to find user and plan to create a new subscription
    try:
        from accounts.models import User
        user = User.objects.get(email=user_email)
        plan = Plan.objects.get(slug=plan_slug)
        sub = Subscription.objects.create(
            user=user,
            plan=plan,
            provider_subscription_ref=provider_ref,
            provider_customer_ref=data.get("customer_id", ""),
        )
        return sub
    except Exception:
        return None


def _handle_payment_success(event: PaymentEvent, data: dict) -> str:
    sub = _get_or_create_subscription(data)
    if sub:
        sub.status = Subscription.STATUS_ACTIVE
        period_start = data.get("period_start")
        period_end = data.get("period_end")
        if period_start:
            sub.period_start = period_start
        if period_end:
            sub.period_end = period_end
        sub.save(update_fields=["status", "period_start", "period_end", "updated_at"])
        event.subscription = sub
    return "subscription_activated"


def _handle_subscription_activated(event: PaymentEvent, data: dict) -> str:
    return _handle_payment_success(event, data)


def _handle_subscription_renewed(event: PaymentEvent, data: dict) -> str:
    return _handle_payment_success(event, data)


def _handle_subscription_cancelled(event: PaymentEvent, data: dict) -> str:
    provider_ref = data.get("subscription_id", "")
    sub = Subscription.objects.filter(provider_subscription_ref=provider_ref).first()
    if sub:
        sub.status = Subscription.STATUS_CANCELLED
        sub.cancel_at_period_end = True
        sub.save(update_fields=["status", "cancel_at_period_end", "updated_at"])
        event.subscription = sub
    return "subscription_cancelled"


def _handle_subscription_expired(event: PaymentEvent, data: dict) -> str:
    provider_ref = data.get("subscription_id", "")
    sub = Subscription.objects.filter(provider_subscription_ref=provider_ref).first()
    if sub:
        sub.status = Subscription.STATUS_EXPIRED
        sub.save(update_fields=["status", "updated_at"])
        event.subscription = sub
    return "subscription_expired"


def _handle_payment_failed(event: PaymentEvent, data: dict) -> str:
    provider_ref = data.get("subscription_id", "")
    sub = Subscription.objects.filter(provider_subscription_ref=provider_ref).first()
    if sub:
        sub.status = Subscription.STATUS_PAST_DUE
        sub.save(update_fields=["status", "updated_at"])
        event.subscription = sub
    return "payment_failed"


def _handle_payment_refunded(event: PaymentEvent, data: dict) -> str:
    provider_ref = data.get("subscription_id", "")
    sub = Subscription.objects.filter(provider_subscription_ref=provider_ref).first()
    if sub:
        sub.status = Subscription.STATUS_REFUNDED
        sub.save(update_fields=["status", "updated_at"])
        event.subscription = sub
    return "payment_refunded"
