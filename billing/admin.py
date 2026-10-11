from django.contrib import admin
from .models import PaymentEvent, Plan, Subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "daily_share_limit", "price_amount", "currency", "billing_interval", "is_free", "active", "sort_order")
    list_editable = ("active", "sort_order", "daily_share_limit", "price_amount")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("sort_order",)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "status", "period_start", "period_end", "cancel_at_period_end", "created_at")
    list_filter = ("status", "plan")
    search_fields = ("user__email", "provider_subscription_ref", "provider_customer_ref")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    list_display = ("provider_event_id", "event_type", "processing_status", "received_at", "processed_at")
    list_filter = ("processing_status", "event_type")
    search_fields = ("provider_event_id",)
    ordering = ("-received_at",)
    readonly_fields = ("provider_event_id", "raw_payload", "received_at")
