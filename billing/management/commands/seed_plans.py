"""
Management command: python manage.py seed_plans
Creates the default Free / Starter / Plus / Pro plans from settings.
Safe to run multiple times (uses update_or_create).
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from billing.models import Plan


PLAN_DEFAULTS = [
    {
        "name": "Free",
        "slug": "free",
        "daily_share_limit":    settings.FREE_SHARES_PER_DAY,
        "price_amount":         "0.00",
        "currency":             "INR",
        "billing_interval":     "monthly",
        "description":          "Up to 10 secure file shares per day. No movie sharing.",
        "is_free":              True,
        "active":               True,
        "sort_order":           0,
        "movie_size_limit_bytes": 0,   # not allowed
    },
    {
        "name": "Starter",
        "slug": "starter",
        "daily_share_limit":    settings.STARTER_SHARES_PER_DAY,
        "price_amount":         "99.00",
        "currency":             "INR",
        "billing_interval":     "monthly",
        "description":          "Up to 50 shares/day. Movie sharing up to 1 GB.",
        "is_free":              False,
        "active":               True,
        "sort_order":           1,
        "movie_size_limit_bytes": settings.MOVIE_SIZE_STARTER,  # 1 GB
    },
    {
        "name": "Plus",
        "slug": "plus",
        "daily_share_limit":    settings.PLUS_SHARES_PER_DAY,
        "price_amount":         "199.00",
        "currency":             "INR",
        "billing_interval":     "monthly",
        "description":          "Up to 200 shares/day. Movie sharing up to 2 GB.",
        "is_free":              False,
        "active":               True,
        "sort_order":           2,
        "movie_size_limit_bytes": settings.MOVIE_SIZE_PLUS,     # 2 GB
    },
    {
        "name": "Pro",
        "slug": "pro",
        "daily_share_limit":    settings.PRO_SHARES_PER_DAY,
        "price_amount":         "399.00",
        "currency":             "INR",
        "billing_interval":     "monthly",
        "description":          "Up to 500 shares/day. Unlimited movie sharing.",
        "is_free":              False,
        "active":               True,
        "sort_order":           3,
        "movie_size_limit_bytes": -1,                           # unlimited
    },
]


class Command(BaseCommand):
    help = "Seed the database with default subscription plans."

    def handle(self, *args, **options):
        for plan_data in PLAN_DEFAULTS:
            slug = plan_data.pop("slug")
            plan, created = Plan.objects.update_or_create(
                slug=slug,
                defaults=plan_data,
            )
            plan_data["slug"] = slug
            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{action}: {plan.name} plan"))
        self.stdout.write(self.style.SUCCESS("Plan seeding complete."))
