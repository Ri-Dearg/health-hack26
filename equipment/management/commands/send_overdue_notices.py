from django.core.management.base import BaseCommand

from equipment.services import send_overdue_notices


class Command(BaseCommand):
    help = "Email the issuing OT about each overdue loan (once per loan). Run daily, e.g. as a Render cron job."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f"Sent {send_overdue_notices()} overdue notice(s)."))
