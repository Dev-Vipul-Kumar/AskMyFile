from django.contrib import admin
from .models import GuestQuotaIdentity, QuotaUsage, Share, ShareOTPDelivery


@admin.register(Share)
class ShareAdmin(admin.ModelAdmin):
    list_display = ("id", "owner_user", "guest_identity", "original_filename",
                    "status", "ciphertext_size", "download_count", "expires_at", "created_at")
    list_filter  = ("status",)
    search_fields = ("owner_user__email", "original_filename")
    ordering     = ("-created_at",)
    readonly_fields = ("access_otp_hash", "storage_key", "salt", "iv", "created_at")


@admin.register(GuestQuotaIdentity)
class GuestQuotaIdentityAdmin(admin.ModelAdmin):
    list_display  = ("id", "identifier_hash", "created_at", "last_seen_at")
    readonly_fields = ("identifier_hash",)


@admin.register(QuotaUsage)
class QuotaUsageAdmin(admin.ModelAdmin):
    list_display = ("identity_type", "identity_id", "date_key", "successful_shares", "updated_at")
    list_filter  = ("identity_type", "date_key")


@admin.register(ShareOTPDelivery)
class ShareOTPDeliveryAdmin(admin.ModelAdmin):
    list_display = ("share", "channel", "sent_at")
    list_filter  = ("channel",)
    readonly_fields = ("share",)
