from django.core.management.base import BaseCommand

from equipment.spreadsheet import import_workbook


class Command(BaseCommand):
    help = "Import an .xlsx tracker. Previews only, unless --apply is given."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--apply", action="store_true", help="Actually make the changes")
        parser.add_argument("--pool", choices=["ot", "winter"], default="ot", help="Pool for new items")

    def handle(self, *args, path, apply=False, pool="ot", **options):
        r = import_workbook(path, dry_run=not apply, default_pool=pool)
        self.stdout.write("APPLIED" if apply else "PREVIEW (nothing changed; add --apply)")
        self.stdout.write(f"  new items: {len(r.created)}  updated: {len(r.updated)}  unchanged: {r.unchanged}  "
                          f"conflicts: {len(r.conflicts)}  waitlist new/updated: {r.waitlist_created}/{r.waitlist_updated}")
        for asset, fields in r.updated:
            self.stdout.write(f"  updated {asset}: {', '.join(fields)}")
        for asset, reason in r.conflicts:
            self.stdout.write(self.style.WARNING(f"  CONFLICT {asset}: {reason}"))
        for line in r.offers + r.warnings:
            self.stdout.write(self.style.WARNING(f"  {line}"))
