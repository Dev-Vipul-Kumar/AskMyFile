"""
billing/views.py
Razorpay payment gateway integration.

Flow:
  1. User clicks "Subscribe" on /billing/plans/
  2. POST /billing/create-order/ → server creates Razorpay order, returns order_id + key
  3. Browser opens Razorpay checkout modal (their hosted JS)
  4. On payment success, browser POSTs razorpay_payment_id + razorpay_signature to /billing/verify/
  5. Server verifies HMAC signature → activates subscription
  6. Razorpay also fires webhooks to /billing/webhook/ for server-side confirmation

Never trust the browser's success callback alone — always verify server-side.
"""
import hashlib
import hmac
import json
import logging

import razorpay

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .models import Plan, Subscription, PaymentEvent

logger = logging.getLogger(__name__)


def _razorpay_client():
    """Return an authenticated Razorpay client."""
    return razorpay.Client(
        auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
    )


# ---------------------------------------------------------------------------
# Plans page
# ---------------------------------------------------------------------------

@require_GET
def plans_view(request):
    plans = Plan.objects.filter(active=True).order_by("sort_order")
    user_subscription = None
    if request.user.is_authenticated:
        user_subscription = (
            Subscription.objects
            .filter(user=request.user, status=Subscription.STATUS_ACTIVE)
            .select_related("plan")
            .first()
        )
        if user_subscription and not user_subscription.is_currently_active:
            user_subscription = None
    return render(request, "billing/plans.html", {
        "plans": plans,
        "user_subscription": user_subscription,
        "razorpay_key_id": settings.RAZORPAY_KEY_ID,
    })


# ---------------------------------------------------------------------------
# Step 1: Create Razorpay order (AJAX POST from plans page)
# ---------------------------------------------------------------------------

@login_required
@require_POST
def create_order(request):
    """
    Creates a Razorpay order for the selected plan.
    Returns { ok, order_id, amount, currency, plan_name, subscription_id }.
    """
    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Invalid request."}, status=400)

    plan_id = body.get("plan_id")
    if not plan_id:
        return JsonResponse({"ok": False, "error": "plan_id is required."}, status=400)

    try:
        plan = Plan.objects.get(pk=plan_id, active=True, is_free=False)
    except Plan.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Plan not found."}, status=404)

    # Block double-subscription
    active_sub = Subscription.objects.filter(
        user=request.user,
        plan=plan,
        status=Subscription.STATUS_ACTIVE,
    ).first()
    if active_sub and active_sub.is_currently_active:
        return JsonResponse({"ok": False, "error": "You already have this plan active."}, status=400)

    # Razorpay amount is in paise (INR × 100)
    amount_paise = int(float(plan.price_amount) * 100)

    if not settings.RAZORPAY_KEY_ID or settings.RAZORPAY_KEY_ID.startswith("rzp_test_XXX"):
        # Keys not configured — return a helpful error
        return JsonResponse({
            "ok": False,
            "error": "Razorpay keys not configured. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to your .env file.",
        }, status=503)

    try:
        client = _razorpay_client()
        rz_order = client.order.create({
            "amount":   amount_paise,
            "currency": plan.currency,
            "receipt":  f"sub_{request.user.pk}_{plan.slug}",
            "notes": {
                "user_email": request.user.email,
                "plan_slug":  plan.slug,
                "plan_name":  plan.name,
            },
        })
    except Exception as exc:
        logger.error("Razorpay order creation failed: %s", exc)
        return JsonResponse({"ok": False, "error": "Payment gateway error. Please try again."}, status=502)

    # Create a pending subscription linked to this Razorpay order
    sub = Subscription.objects.create(
        user=request.user,
        plan=plan,
        status=Subscription.STATUS_PENDING,
        razorpay_order_id=rz_order["id"],
        provider_customer_ref=request.user.email,
    )

    return JsonResponse({
        "ok":              True,
        "order_id":        rz_order["id"],
        "amount":          amount_paise,
        "currency":        plan.currency,
        "plan_name":       plan.name,
        "subscription_id": sub.pk,
    })


# ---------------------------------------------------------------------------
# Step 2: Verify payment after Razorpay checkout success callback
# ---------------------------------------------------------------------------

@login_required
@require_POST
def verify_payment(request):
    """
    Called by the browser after Razorpay checkout succeeds.
    Verifies the HMAC signature, then activates the subscription.
    """
    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Invalid request."}, status=400)

    razorpay_order_id   = body.get("razorpay_order_id",   "").strip()
    razorpay_payment_id = body.get("razorpay_payment_id", "").strip()
    razorpay_signature  = body.get("razorpay_signature",  "").strip()
    subscription_id     = body.get("subscription_id")

    if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature]):
        return JsonResponse({"ok": False, "error": "Incomplete payment data."}, status=400)

    # ── Verify HMAC-SHA256 signature ──
    # Razorpay signs: order_id + "|" + payment_id  with KEY_SECRET
    message  = f"{razorpay_order_id}|{razorpay_payment_id}"
    expected = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, razorpay_signature):
        logger.warning(
            "Razorpay signature mismatch for order %s payment %s",
            razorpay_order_id, razorpay_payment_id,
        )
        return JsonResponse({"ok": False, "error": "Payment verification failed."}, status=400)

    # ── Find and activate the subscription ──
    try:
        sub = Subscription.objects.get(
            razorpay_order_id=razorpay_order_id,
            user=request.user,
        )
    except Subscription.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Subscription not found."}, status=404)

    sub.razorpay_payment_id = razorpay_payment_id
    sub.razorpay_signature  = razorpay_signature
    sub.status              = Subscription.STATUS_ACTIVE
    sub.period_start        = timezone.now()
    sub.period_end          = timezone.now() + timezone.timedelta(days=30)
    sub.provider_subscription_ref = razorpay_payment_id
    sub.save()

    logger.info(
        "Subscription #%s activated for %s (plan: %s, payment: %s)",
        sub.pk, request.user.email, sub.plan.name, razorpay_payment_id,
    )

    return JsonResponse({
        "ok":       True,
        "plan":     sub.plan.name,
        "redirect": "/billing/payment/success/",
    })


# ---------------------------------------------------------------------------
# Payment success / failure pages
# ---------------------------------------------------------------------------

@login_required
@require_GET
def payment_success(request):
    sub = (
        Subscription.objects
        .filter(user=request.user, status=Subscription.STATUS_ACTIVE)
        .select_related("plan")
        .order_by("-period_start")
        .first()
    )
    return render(request, "billing/payment_success.html", {"subscription": sub})


@login_required
@require_GET
def payment_failure(request):
    return render(request, "billing/payment_failure.html")


# ---------------------------------------------------------------------------
# Razorpay Webhook  (server-side confirmation — CSRF exempt, signature verified)
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
def webhook_view(request):
    """
    Razorpay fires this for every payment event.
    We verify the X-Razorpay-Signature header before processing.
    """
    payload   = request.body
    signature = request.headers.get("X-Razorpay-Signature", "")
    secret    = settings.RAZORPAY_WEBHOOK_SECRET

    if secret:
        expected = hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            logger.warning("Razorpay webhook signature invalid.")
            return HttpResponse("Invalid signature.", status=400)

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return HttpResponse("Bad JSON.", status=400)

    event = data.get("event", "")
    entity = data.get("payload", {}).get("payment", {}).get("entity", {})
    order_id   = entity.get("order_id",  "")
    payment_id = entity.get("id",        "")

    logger.info("Razorpay webhook: event=%s order=%s payment=%s", event, order_id, payment_id)

    # Idempotency — skip already-processed events
    if PaymentEvent.objects.filter(provider_event_id=payment_id).exists():
        return HttpResponse("OK", status=200)

    PaymentEvent.objects.create(
        provider_event_id  = payment_id or f"evt_{order_id}",
        event_type         = event,
        raw_payload        = payload.decode("utf-8", errors="replace")[:10000],
        processing_status  = PaymentEvent.STATUS_RECEIVED,
    )

    if event == "payment.captured":
        try:
            sub = Subscription.objects.get(razorpay_order_id=order_id)
            if sub.status != Subscription.STATUS_ACTIVE:
                sub.status       = Subscription.STATUS_ACTIVE
                sub.period_start = timezone.now()
                sub.period_end   = timezone.now() + timezone.timedelta(days=30)
                sub.razorpay_payment_id = payment_id
                sub.save()
                logger.info("Webhook activated subscription #%s for order %s", sub.pk, order_id)
        except Subscription.DoesNotExist:
            logger.warning("Webhook: no subscription found for order %s", order_id)

    elif event == "payment.failed":
        try:
            sub = Subscription.objects.get(razorpay_order_id=order_id)
            if sub.status == Subscription.STATUS_PENDING:
                sub.status = Subscription.STATUS_PAST_DUE
                sub.save(update_fields=["status", "updated_at"])
        except Subscription.DoesNotExist:
            pass

    return HttpResponse("OK", status=200)


# ---------------------------------------------------------------------------
# Subscription status API
# ---------------------------------------------------------------------------

@login_required
@require_GET
def subscription_status(request):
    sub = (
        Subscription.objects
        .filter(user=request.user, status=Subscription.STATUS_ACTIVE)
        .select_related("plan")
        .first()
    )
    if sub and sub.is_currently_active:
        return JsonResponse({
            "has_active_plan": True,
            "plan_name":   sub.plan.name,
            "daily_limit": sub.plan.daily_share_limit,
            "period_end":  sub.period_end.isoformat() if sub.period_end else None,
        })
    return JsonResponse({
        "has_active_plan": False,
        "plan_name":   "Free",
        "daily_limit": settings.FREE_SHARES_PER_DAY,
    })
