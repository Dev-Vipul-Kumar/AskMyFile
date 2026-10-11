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
        "daily_share_limit": settings.FREE_SHARES_PER_DAY,
        "price_amount": "0.00",
        "currency": "INR",
        "billing_interval": "monthly",
        "description": "Up to 10 secure file shares per day.",
        "is_free": True,
        "active": True,
        "sort_order": 0,
    },
    {
        "name": "Starter",
        "slug": "starter",
        "daily_share_limit": settings.STARTER_SHARES_PER_DAY,
        "price_amount": "99.00",
        "currency": "INR",
        "billing_interval": "monthly",
        "description": "Up to 50 secure file shares per day.",
        "is_free": False,
        "active": True,
        "sort_order": 1,
    },
    {
        "name": "Plus",
        "slug": "plus",
        "daily_share_limit": settings.PLUS_SHARES_PER_DAY,
        "price_amount": "199.00",
        "currency": "INR",
        "billing_interval": "monthly",
        "description": "Up to 200 secure file shares per day.",
        "is_free": False,
        "active": True,
        "sort_order": 2,
    },
    {
        "name": "Pro",
        "slug": "pro",
        "daily_share_limit": settings.PRO_SHARES_PER_DAY,
        "price_amount": "399.00",
        "currency": "INR",
        "billing_interval": "monthly",
        "description": "Up to 500 secure file shares per day.",
        "is_free": False,
        "active": True,
        "sort_order": 3,
    },
]


class Command(BaseCommand):
    help = "Seed the database with default subscription plans."

    def handle(self, *args, **options):
        for plan_data in PLAN_DEFAULTS:
            slug = plan_data.pop("slug")
            plan_data_copy = {k: v for k, v in plan_data.items()}
            plan, created = Plan.objects.update_or_create(
                slug=slug,
                defaults=plan_data_copy,
            )
            # restore slug in case needed
            plan_data["slug"] = slug
            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{action}: {plan.name} plan"))

        self.stdout.write(self.style.SUCCESS("Plan seeding complete."))
