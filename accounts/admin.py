from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User, VerificationChallenge


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "display_name", "phone_number", "email_verified", "phone_verified", "is_active", "created_at")
    list_filter = ("email_verified", "phone_verified", "is_active", "is_staff")
    search_fields = ("email", "display_name", "phone_number")
    ordering = ("-created_at",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("display_name", "phone_number")}),
        ("Verification", {"fields": ("email_verified", "phone_verified")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "created_at")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "display_name", "phone_number", "password1", "password2"),
        }),
    )
    readonly_fields = ("created_at",)


@admin.register(VerificationChallenge)
class VerificationChallengeAdmin(admin.ModelAdmin):
    list_display = ("user", "channel", "expires_at", "attempts", "consumed_at")
    list_filter = ("channel",)
    search_fields = ("user__email",)
    ordering = ("-sent_at",)
    readonly_fields = ("token_hash", "sent_at")
