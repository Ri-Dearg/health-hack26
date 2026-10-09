from django.core.management.base import BaseCommand

from equipment.synergy import handle_adt


class Command(BaseCommand):
    help = "Pretend Synergy sent an HL7 ADT message: A02 (transfer) or A03 (discharge). For the demo."

    def add_arguments(self, parser):
        parser.add_argument("event", choices=["A02", "A03"])
        parser.add_argument("mrn")
        parser.add_argument("--ward", default="")
        parser.add_argument("--bed", default="")

    def handle(self, *args, event, mrn, ward, bed, **options):
        loans = handle_adt(event, mrn, ward=ward, bed=bed)
        if not loans:
            self.stdout.write(f"No open loans for {mrn}.")
        for loan in loans:
            self.stdout.write(self.style.SUCCESS(f"{event}: updated loan of {loan.equipment.asset_number} ({loan.equipment.location})"))
