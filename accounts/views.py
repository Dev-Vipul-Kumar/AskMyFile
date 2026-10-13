"""
accounts/views.py
Handles: register, login, logout, email verification, phone OTP, password reset.
"""
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .forms import (
    LoginForm,
    OTPForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    RegistrationForm,
)
from .models import User, VerificationChallenge
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _send_email_verification(request, user: User) -> None:
    """Send an email with a verification link."""
    ttl = settings.EMAIL_TOKEN_TTL_HOURS * 60  # minutes
    raw_token = VerificationChallenge.create_for_user(
        user, VerificationChallenge.CHANNEL_EMAIL, ttl_minutes=ttl
    )
    # Use SITE_URL so the link always points to the live domain, not the
    # internal proxy address (127.0.0.1) that request.build_absolute_uri()
    # can return when running behind Render's load balancer.
    site_url  = getattr(settings, "SITE_URL", "").rstrip("/")
    verify_url = f"{site_url}/accounts/verify-email/{raw_token}/"
    try:
        send_mail(
            subject="Verify your AskMyFile email address",
            message=(
                f"Hello {user.full_name},\n\n"
                f"Click the link below to verify your email address "
                f"(expires in {settings.EMAIL_TOKEN_TTL_HOURS} hours):\n\n"
                f"{verify_url}\n\n"
                f"If you did not register, please ignore this email.\n\n"
                f"— The AskMyFile Team"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=True,   # never crash the request if email fails
        )
        logger.info("Verification email sent to %s", user.email)
    except Exception as exc:
        logger.error("Verification email FAILED for %s: %s", user.email, exc)


def _send_sms_otp(user: User) -> str:
    """
    Generate and 'send' an SMS OTP.
    In development the OTP is returned (and displayed in console).
    In production wire this to your SMS provider.
    """
    ttl = settings.SMS_OTP_TTL_MINUTES
    raw_otp = VerificationChallenge.create_for_user(
        user, VerificationChallenge.CHANNEL_SMS, ttl_minutes=ttl, length=6
    )
    # TODO: integrate SMS provider using settings.SMS_PROVIDER_KEY
    logger.info("SMS OTP for %s: %s  (dev mode – not sent via provider)", user.phone_number, raw_otp)
    return raw_otp


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)

    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        _send_email_verification(request, user)
        otp = _send_sms_otp(user)
        # Store user id in session for the pending verification flow
        request.session["pending_verify_user_id"] = user.pk
        if settings.DEBUG:
            # Surface OTP in dev via messages so testers can see it
            messages.info(request, f"[DEV] Your SMS OTP is: {otp}")
        messages.success(
            request,
            "Account created! Please check your email to verify your address, "
            "then enter the SMS code sent to your phone.",
        )
        return redirect("accounts:verify_phone")

    return render(request, "accounts/register.html", {"form": form})


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------

@require_http_methods(["GET"])
def verify_email(request, token: str):
    """
    The user clicks the link from their email.
    We look up any user with a matching pending email challenge.
    """
    token_hash = VerificationChallenge._hash_token(token)
    try:
        challenge = VerificationChallenge.objects.select_related("user").get(
            channel=VerificationChallenge.CHANNEL_EMAIL,
            token_hash=token_hash,
            consumed_at__isnull=True,
        )
    except VerificationChallenge.DoesNotExist:
        messages.error(request, "This verification link is invalid or has already been used.")
        return render(request, "accounts/verify_email_result.html", {"success": False})

    if challenge.expires_at < timezone.now():
        messages.error(request, "This verification link has expired. Please request a new one.")
        return render(request, "accounts/verify_email_result.html", {"success": False})

    # Mark consumed & verify
    challenge.consumed_at = timezone.now()
    challenge.save(update_fields=["consumed_at"])
    user = challenge.user
    user.email_verified = True
    user.save(update_fields=["email_verified"])

    messages.success(request, "Email verified successfully!")
    # If phone already verified, can proceed to login
    if user.is_fully_verified:
        return redirect("accounts:login")
    return render(request, "accounts/verify_email_result.html", {"success": True, "user": user})


# ---------------------------------------------------------------------------
# Phone / OTP verification
# ---------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
def verify_phone(request):
    """
    The user enters the 6-digit OTP received by SMS.
    Works both during registration (session-based user id) and when logged in.
    """
    user = None
    if request.user.is_authenticated:
        user = request.user
    else:
        uid = request.session.get("pending_verify_user_id")
        if uid:
            try:
                user = User.objects.get(pk=uid)
            except User.DoesNotExist:
                pass

    if user is None:
        messages.error(request, "No pending verification found. Please register or sign in.")
        return redirect("accounts:register")

    form = OTPForm(request.POST or None)

    if request.method == "POST":
        if "resend" in request.POST:
            otp = _send_sms_otp(user)
            if settings.DEBUG:
                messages.info(request, f"[DEV] New SMS OTP: {otp}")
            messages.success(request, "A new code has been sent to your phone.")
            return redirect("accounts:verify_phone")

        if form.is_valid():
            success, result = VerificationChallenge.verify(
                user,
                VerificationChallenge.CHANNEL_SMS,
                form.cleaned_data["otp"],
                max_attempts=settings.SMS_OTP_MAX_ATTEMPTS,
            )
            if success:
                user.phone_verified = True
                user.save(update_fields=["phone_verified"])
                request.session.pop("pending_verify_user_id", None)
                messages.success(request, "Phone number verified! You can now sign in.")
                return redirect("accounts:login")
            else:
                error_map = {
                    "not_found": "No active OTP found. Please request a new code.",
                    "expired": "Your code has expired. Please request a new one.",
                    "too_many_attempts": "Too many attempts. Please request a new code.",
                    "invalid": "Incorrect code. Please try again.",
                }
                messages.error(request, error_map.get(result, "Invalid code."))

    return render(request, "accounts/verify_phone.html", {"form": form, "user_obj": user})


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
def user_login(request):
    if request.user.is_authenticated and request.user.is_fully_verified:
        return redirect(settings.LOGIN_REDIRECT_URL)

    form = LoginForm(request, request.POST or None)

    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        if not user.is_fully_verified:
            request.session["pending_verify_user_id"] = user.pk
            messages.warning(
                request,
                "Please complete verification before accessing your account.",
            )
            if not user.email_verified:
                return redirect("accounts:resend_email_verification")
            return redirect("accounts:verify_phone")

        login(request, user)
        # Staff/admin users always go to the admin panel
        if user.is_staff:
            return redirect("/admin-panel/")
        next_url = request.GET.get("next", settings.LOGIN_REDIRECT_URL)
        return redirect(next_url)

    return render(request, "accounts/login.html", {"form": form})


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@require_http_methods(["POST"])
@login_required
def user_logout(request):
    logout(request)
    messages.success(request, "You have been signed out.")
    return redirect("/")


# ---------------------------------------------------------------------------
# Resend email verification
# ---------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
def resend_email_verification(request):
    user = None

    # Try to resolve user from session (set during registration)
    if request.user.is_authenticated:
        user = request.user
    else:
        uid = request.session.get("pending_verify_user_id")
        if uid:
            try:
                user = User.objects.get(pk=uid)
            except User.DoesNotExist:
                pass

    # If session expired / user not found — let them enter email manually
    email_not_found = False
    if request.method == "POST":
        if user is None:
            # User typed their email manually (session lost case)
            email = request.POST.get("email", "").strip().lower()
            if email:
                try:
                    user = User.objects.get(email=email)
                    request.session["pending_verify_user_id"] = user.pk
                except User.DoesNotExist:
                    email_not_found = True
            if email_not_found or not user:
                return render(request, "accounts/resend_email_verification.html", {
                    "user_obj": None,
                    "show_email_field": True,
                    "email_not_found": email_not_found,
                })

        _send_email_verification(request, user)
        messages.success(request, "A new verification email has been sent. Please check your inbox.")
        return render(request, "accounts/resend_email_verification.html", {
            "user_obj": user,
            "sent": True,
        })

    return render(request, "accounts/resend_email_verification.html", {
        "user_obj": user,
        "show_email_field": user is None,  # show email input if session was lost
    })


# ---------------------------------------------------------------------------
# Password reset – request
# ---------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
def password_reset_request(request):
    form = PasswordResetRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        # Always show the same message to prevent account enumeration
        try:
            user = User.objects.get(email=email)
            ttl = settings.EMAIL_TOKEN_TTL_HOURS * 60
            raw_token = VerificationChallenge.create_for_user(
                user, VerificationChallenge.CHANNEL_PASSWORD_RESET, ttl_minutes=ttl
            )
            reset_url = f"{getattr(settings, 'SITE_URL', '').rstrip('/')}/accounts/password-reset/{raw_token}/"
            try:
                send_mail(
                    subject="Reset your AskMyFile password",
                    message=(
                        f"Hello {user.full_name},\n\n"
                        f"Click below to reset your password "
                        f"(expires in {settings.EMAIL_TOKEN_TTL_HOURS} hours):\n\n"
                        f"{reset_url}\n\n"
                        f"If you did not request this, please ignore this email.\n\n"
                        f"— The AskMyFile Team"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=True,   # never crash the request if email fails
                )
            except Exception as exc:
                logger.error("Password reset email FAILED for %s: %s", user.email, exc)
        except User.DoesNotExist:
            pass  # silent – do not reveal whether email exists

        messages.success(
            request,
            "If that email is registered, you will receive a reset link shortly.",
        )
        return redirect("accounts:password_reset_request")

    return render(request, "accounts/password_reset_request.html", {"form": form})


# ---------------------------------------------------------------------------
# Password reset – confirm
# ---------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
def password_reset_confirm(request, token: str):
    token_hash = VerificationChallenge._hash_token(token)
    try:
        challenge = VerificationChallenge.objects.select_related("user").get(
            channel=VerificationChallenge.CHANNEL_PASSWORD_RESET,
            token_hash=token_hash,
            consumed_at__isnull=True,
        )
    except VerificationChallenge.DoesNotExist:
        messages.error(request, "This reset link is invalid or has already been used.")
        return redirect("accounts:password_reset_request")

    if challenge.expires_at < timezone.now():
        messages.error(request, "This reset link has expired. Please request a new one.")
        return redirect("accounts:password_reset_request")

    form = PasswordResetConfirmForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        challenge.consumed_at = timezone.now()
        challenge.save(update_fields=["consumed_at"])
        user = challenge.user
        user.set_password(form.cleaned_data["password"])
        user.save(update_fields=["password"])
        messages.success(request, "Password reset successfully. Please sign in with your new password.")
        return redirect("accounts:login")

    return render(request, "accounts/password_reset_confirm.html", {"form": form})


# ---------------------------------------------------------------------------
# Phone-based password reset  (SMS OTP flow)
# ---------------------------------------------------------------------------

from django.http import JsonResponse as _JsonResponse
from django.views.decorators.http import require_http_methods as _require_http


@_require_http(["GET"])
def phone_reset_check(request):
    """
    AJAX: GET /accounts/phone-reset/check/?phone=+91XXXXXXXXXX
    Returns JSON { registered: true/false } for real-time feedback.
    Does NOT confirm the user's identity — only whether the number exists.
    """
    phone = request.GET.get("phone", "").strip()
    registered = User.objects.filter(phone_number=phone, is_active=True).exists()
    return _JsonResponse({"registered": registered})


@require_http_methods(["GET", "POST"])
def phone_reset_request(request):
    """
    Step 1 — User enters their registered mobile number.
    Step 2 — User enters the 6-digit SMS OTP.
    Step 3 — User sets a new password.
    All three steps are handled on one page via JS sections.
    """
    # ── Handle OTP send request (AJAX POST with action=send_otp) ──
    if request.method == "POST":
        import json as _json
        try:
            body = _json.loads(request.body)
        except (ValueError, TypeError):
            body = {}

        action = body.get("action", request.POST.get("action", ""))

        # ── Action: send OTP to phone ──
        if action == "send_otp":
            phone = body.get("phone", "").strip()
            try:
                user = User.objects.get(phone_number=phone, is_active=True)
            except User.DoesNotExist:
                return _JsonResponse({"ok": False, "error": "Phone number not registered."})

            ttl     = settings.SMS_OTP_TTL_MINUTES
            raw_otp = VerificationChallenge.create_for_user(
                user,
                VerificationChallenge.CHANNEL_PASSWORD_RESET,
                ttl_minutes=ttl,
                length=6,
            )
            # In dev: log to console; in prod: use SMS provider
            logger.info("[DEV] Phone-reset OTP for %s: %s", phone, raw_otp)
            if settings.DEBUG:
                # Surface the OTP in the response for local testing only
                return _JsonResponse({"ok": True, "dev_otp": raw_otp,
                                      "message": f"[DEV] OTP sent: {raw_otp}"})
            return _JsonResponse({"ok": True, "message": "OTP sent to your phone."})

        # ── Action: verify OTP ──
        if action == "verify_otp":
            phone   = body.get("phone", "").strip()
            raw_otp = str(body.get("otp", "")).strip()
            try:
                user = User.objects.get(phone_number=phone, is_active=True)
            except User.DoesNotExist:
                return _JsonResponse({"ok": False, "error": "Phone number not found."})

            success, result = VerificationChallenge.verify(
                user,
                VerificationChallenge.CHANNEL_PASSWORD_RESET,
                raw_otp,
                max_attempts=settings.SMS_OTP_MAX_ATTEMPTS,
            )
            if not success:
                err_map = {
                    "not_found":        "No active OTP. Please request a new code.",
                    "expired":          "OTP has expired. Please request a new code.",
                    "too_many_attempts":"Too many attempts. Please request a new code.",
                    "invalid":          "Incorrect OTP. Try again.",
                }
                return _JsonResponse({"ok": False, "error": err_map.get(result, "Invalid OTP.")})

            # Issue a short-lived session token so the password-set step is authorised
            request.session["phone_reset_user_id"] = user.pk
            request.session.set_expiry(600)   # 10 minutes
            return _JsonResponse({"ok": True})

        # ── Action: set new password ──
        if action == "set_password":
            uid = request.session.get("phone_reset_user_id")
            if not uid:
                return _JsonResponse({"ok": False, "error": "Session expired. Please start over."})
            try:
                user = User.objects.get(pk=uid, is_active=True)
            except User.DoesNotExist:
                return _JsonResponse({"ok": False, "error": "User not found."})

            password = body.get("password", "")
            confirm  = body.get("confirm",  "")

            if not password or len(password) < 8:
                return _JsonResponse({"ok": False, "error": "Password must be at least 8 characters."})
            if password != confirm:
                return _JsonResponse({"ok": False, "error": "Passwords do not match."})

            user.set_password(password)
            user.save(update_fields=["password"])
            # Clear the reset session key
            request.session.pop("phone_reset_user_id", None)
            return _JsonResponse({"ok": True, "redirect": "/accounts/login/"})

        return _JsonResponse({"ok": False, "error": "Unknown action."}, status=400)

    # GET — render the page
    return render(request, "accounts/phone_reset.html")
