from django.core.management.base import BaseCommand

from equipment import pitch_demo


class Command(BaseCommand):
    help = "Wipe and reload the demo data, then set up the pitch demo start state (D27, D12, wheelchair waitlist)."

    def handle(self, *args, **options):
        pitch_demo.reset()
        self.stdout.write(self.style.SUCCESS(
            f"Pitch demo ready. D27 available in {pitch_demo.STORE}, D12 on loan in {pitch_demo.WARD_3}. "
            f"Demo MRN: {pitch_demo.DEMO_MRN}. Next on the wheelchair waitlist: {pitch_demo.WAITLIST[0][0]}."
        ))
