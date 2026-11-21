"""
Management command: python manage.py cleanup_expired_shares
Run this periodically (e.g., via cron or a task queue) to delete expired
ciphertext, stale upload staging files, and update share status.
"""
from django.core.management.base import BaseCommand
from shares.services import cleanup_expired_shares


class Command(BaseCommand):
    help = "Delete expired shares and stale upload staging files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted without making changes.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        result = cleanup_expired_shares(dry_run=dry_run)
        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Cleanup complete: {result['deleted']} deleted, {result['errors']} errors."
            )
        )
