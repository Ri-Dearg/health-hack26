from django.core.management.base import BaseCommand
from django.utils import timezone

from equipment.spreadsheet import export_bytes


class Command(BaseCommand):
    help = "Write the tracker to an .xlsx file (one tab per category, plus Waitlist). Schedule it during changeover."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", help="Output file (default: ot-equipment-tracker_<date>.xlsx)")
        parser.add_argument("--pool", choices=["ot", "winter"], help="Only one pool")

    def handle(self, *args, path=None, pool=None, **options):
        path = path or f"ot-equipment-tracker_{timezone.localtime():%Y-%m-%d_%H%M}.xlsx"
        with open(path, "wb") as f:
            f.write(export_bytes(pool))
        self.stdout.write(self.style.SUCCESS(f"Wrote {path}"))
