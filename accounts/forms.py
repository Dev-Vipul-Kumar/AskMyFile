from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import User


class RegistrationForm(forms.ModelForm):
    """Registration: email, display name, password (×2). No phone."""

    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "class": "form-input"}),
    )
    password_confirm = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "class": "form-input"}),
    )

    class Meta:
        model  = User
        fields = ["email", "display_name"]
        widgets = {
            "email":        forms.EmailInput(attrs={"autocomplete": "email", "class": "form-input"}),
            "display_name": forms.TextInput(attrs={"autocomplete": "name",  "class": "form-input"}),
        }

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def clean_password(self):
        password = self.cleaned_data.get("password", "")
        validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password")
        p2 = cleaned.get("password_confirm")
        if p1 and p2 and p1 != p2:
            self.add_error("password_confirm", "Passwords do not match.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        user.email_verified = False
        if commit:
            user.save()
        return user


class LoginForm(forms.Form):
    """Email + password login."""

    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"autocomplete": "email", "class": "form-input"})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "class": "form-input"})
    )

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self._user   = None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned  = super().clean()
        email    = cleaned.get("email", "").strip().lower()
        password = cleaned.get("password", "")
        if email and password:
            user = authenticate(self.request, username=email, password=password)
            if user is None:
                raise ValidationError("Invalid email or password.")
            if not user.is_active:
                raise ValidationError("This account is disabled.")
            self._user = user
        return cleaned

    def get_user(self):
        return self._user


class PasswordResetRequestForm(forms.Form):
    """Start a password reset by email."""

    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"autocomplete": "email", "class": "form-input"})
    )


class PasswordResetConfirmForm(forms.Form):
    """Set a new password after clicking the reset link."""

    password = forms.CharField(
        label="New Password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "class": "form-input"}),
    )
    password_confirm = forms.CharField(
        label="Confirm New Password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "class": "form-input"}),
    )

    def clean_password(self):
        password = self.cleaned_data.get("password", "")
        validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password")
        p2 = cleaned.get("password_confirm")
        if p1 and p2 and p1 != p2:
            self.add_error("password_confirm", "Passwords do not match.")
        return cleaned
