"""
accounts/views.py
Email-only authentication: register → verify email → sign in.
Password reset via email link. No phone / SMS anywhere.
"""
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .forms import (
    LoginForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    RegistrationForm,
)
from .models import User, VerificationChallenge

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Email helper
# ---------------------------------------------------------------------------

def _send_verification_email(request, user: User) -> None:
    site_url  = getattr(settings, "SITE_URL", "").rstrip("/")
    ttl       = settings.EMAIL_TOKEN_TTL_HOURS * 60   # minutes
    raw_token = VerificationChallenge.create_for_user(
        user, VerificationChallenge.CHANNEL_EMAIL, ttl_minutes=ttl
    )
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
            fail_silently=True,
        )
        logger.info("Verification email sent to %s", user.email)
    except Exception as exc:
        logger.error("Verification email FAILED for %s: %s", user.email, exc)


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)

    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        _send_verification_email(request, user)
        request.session["pending_verify_user_id"] = user.pk
        messages.success(
            request,
            "Account created! Please check your email to verify your address.",
        )
        return redirect("accounts:resend_email_verification")

    return render(request, "accounts/register.html", {"form": form})


# ---------------------------------------------------------------------------
# Email verification (link click)
# ---------------------------------------------------------------------------

@require_http_methods(["GET"])
def verify_email(request, token: str):
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

    challenge.consumed_at = timezone.now()
    challenge.save(update_fields=["consumed_at"])

    user = challenge.user
    user.email_verified = True
    user.save(update_fields=["email_verified"])

    # Clear pending session
    request.session.pop("pending_verify_user_id", None)
    messages.success(request, "Email verified! You can now sign in.")
    return render(request, "accounts/verify_email_result.html", {"success": True, "user": user})


# ---------------------------------------------------------------------------
# Resend verification email
# ---------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
def resend_email_verification(request):
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

    email_not_found = False

    if request.method == "POST":
        if user is None:
            # Session lost — let them type their email
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

        _send_verification_email(request, user)
        messages.success(request, "A new verification email has been sent. Please check your inbox.")
        return render(request, "accounts/resend_email_verification.html", {
            "user_obj": user,
            "sent": True,
        })

    return render(request, "accounts/resend_email_verification.html", {
        "user_obj": user,
        "show_email_field": user is None,
    })


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
        if not user.email_verified:
            request.session["pending_verify_user_id"] = user.pk
            messages.warning(request, "Please verify your email before signing in.")
            return redirect("accounts:resend_email_verification")

        login(request, user)
        if user.is_staff:
            return redirect("/admin-panel/")
        return redirect(request.GET.get("next", settings.LOGIN_REDIRECT_URL))

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
# Password reset — request (email link)
# ---------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
def password_reset_request(request):
    form = PasswordResetRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        try:
            user = User.objects.get(email=email)
            site_url  = getattr(settings, "SITE_URL", "").rstrip("/")
            ttl       = settings.EMAIL_TOKEN_TTL_HOURS * 60
            raw_token = VerificationChallenge.create_for_user(
                user, VerificationChallenge.CHANNEL_PASSWORD_RESET, ttl_minutes=ttl
            )
            reset_url = f"{site_url}/accounts/password-reset/{raw_token}/"
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
                    fail_silently=True,
                )
                logger.info("Password reset email sent to %s", user.email)
            except Exception as exc:
                logger.error("Password reset email FAILED for %s: %s", user.email, exc)
        except User.DoesNotExist:
            pass   # silent — do not reveal whether email exists

        messages.success(request,
            "If that email is registered, you will receive a reset link shortly.")
        return redirect("accounts:password_reset_request")

    return render(request, "accounts/password_reset_request.html", {"form": form})


# ---------------------------------------------------------------------------
# Password reset — confirm (follow link from email)
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
        messages.success(request, "Password reset successfully. Please sign in.")
        return redirect("accounts:login")

    return render(request, "accounts/password_reset_confirm.html", {"form": form})
