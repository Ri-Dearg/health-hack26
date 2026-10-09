"""Invented demo data only. No real patients or staff."""

from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Staff
from equipment import services
from django.contrib.auth.models import Group

from equipment.models import Category, Equipment, Loan, LocationLog, Patient, Tag, WaitlistEntry

# The brief's seven kinds of equipment, with wheelchairs and cushions split to make nine categories.
CATEGORIES = {
    "Specialised wheelchair": ("WCS", 2, Tag.TagType.BLUETOOTH),
    "Manual wheelchair": ("WCM", 3, Tag.TagType.BLUETOOTH),
    "Bariatric wheelchair": ("WCB", 1, Tag.TagType.BLUETOOTH),
    "Pressure-relieving cushion (air)": ("CSA", 3, Tag.TagType.NFC),
    "Pressure-relieving cushion (gel/foam)": ("CSG", 3, Tag.TagType.NFC),
    "Elevating leg rest": ("ELR", 2, Tag.TagType.NFC),
    "Headrest": ("HDR", 2, Tag.TagType.QR),
    "Residual limb board": ("RLB", 2, Tag.TagType.QR),
    "Sliding board": ("SLB", 2, Tag.TagType.QR),
}
WARDS = ["Ward 1A", "Ward 2B", "Ward 3C", "Ward 4D", "AMU"]
# Invented register details per category: Type (make and model), Size (WxDxH), Max Wgt.
DETAILS = {
    "WCS": ("Tilt-in-space chair", '17"', "135kg"),
    "WCM": ("Self-propelled chair", '18" x 17"', "125kg"),
    "WCB": ("Bariatric chair", '24-25"', "180kg"),
    "CSA": ("Air cell cushion", '18" x 18" x 4"', "N/A"),
    "CSG": ("Gel and foam cushion", '18" x 18"', "130kg"),
    "ELR": ("Elevating leg rest (pair)", "Adjustable", "135kg"),
    "HDR": ("Contoured headrest", "Medium", ""),
    "RLB": ("Residual limb board", '16"', "135kg"),
    "SLB": ("Sliding board", "70cm", "150kg"),
}


class Command(BaseCommand):
    help = "Load invented demo data: 9 categories, 20 items with tags, staff, loans and a waitlist."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing equipment data first.")

    @transaction.atomic
    def handle(self, *args, reset=False, **options):
        if Equipment.objects.exists() and not reset:
            raise CommandError("Equipment already exists. Use --reset to wipe and reload the demo data.")
        if reset:
            for model in (LocationLog, WaitlistEntry, Tag, Loan, Equipment, Patient, Category):
                model.objects.all().delete()

        call_command("setup_roles")
        ot_staff = Group.objects.get(name="OT staff")

        staff = []
        for username, first, last, number in [
            ("aoife", "Aoife", "Byrne", "OT1001"),
            ("ciaran", "Ciarán", "Doyle", "OT1002"),
            ("niamh", "Niamh", "Kelly", "OT1003"),
        ]:
            person, created = Staff.objects.get_or_create(
                username=username,
                defaults={"first_name": first, "last_name": last, "staff_number": number,
                          "email": f"{username}@example.com", "is_staff": True},
            )
            if created:
                person.set_password("demo-pass-123")
                person.set_card_id(f"CARD-{number}")
                person.save()
            person.groups.add(ot_staff)
            staff.append(person)

        items, today = [], timezone.localdate()
        for name, (prefix, count, tag_type) in CATEGORIES.items():
            category = Category.objects.create(name=name)
            kind, size, max_weight = DETAILS[prefix]
            cushion = prefix.startswith("CS")
            for i in range(1, count + 1):
                n = len(items) + 1
                item = Equipment.objects.create(
                    asset_number=f"{prefix}-{i:03d}", category=category,
                    on_assettrak=i % 2 == 1, description=kind, size=size, max_weight=max_weight,
                    serial_number=f"SN{prefix}{i:04d}" if i % 2 else "",  # not every item has one
                    hc21_number=f"{9000000 + n}", dml_number=f"{500000 + n}", barcode=f"OT{n:05d}",
                    replacement_cover=(i != 2) if cushion else None,
                    comments="New cushion cover" if cushion and i == 1 else "",
                    last_service_date=today - timedelta(days=45 * i),
                    purchase_date=today - timedelta(days=400 * i),
                )
                Tag.objects.create(uid=f"{tag_type.upper()}-{prefix}-{i:03d}", tag_type=tag_type, equipment=item)
                items.append(item)

        now = timezone.now()
        # A normal loan, an overdue loan, and one that left with no card tap.
        services.issue_item(items[0], staff=staff[0], mrn="MRN100001", ward=WARDS[0], bed="4")
        overdue = services.issue_item(items[3], staff=staff[1], mrn="MRN100002", ward=WARDS[1], bed="12")
        overdue.issued_at = now - timedelta(days=34)
        overdue.due_at = now - timedelta(days=4)
        overdue.save()
        services.issue_item(items[8], mrn="MRN100003", ward=WARDS[2])

        Equipment.objects.filter(pk=items[5].pk).update(status=Equipment.Status.AWAITING_REPAIR)
        Equipment.objects.filter(pk=items[12].pk).update(status=Equipment.Status.MISSING, location="Last seen Ward 4D")

        air_cushions = Category.objects.get(name="Pressure-relieving cushion (air)")
        for mrn, priority, days_waiting in [
            ("MRN100010", WaitlistEntry.Priority.ROUTINE, 6),
            ("MRN100011", WaitlistEntry.Priority.DISCHARGE_CRITICAL, 1),
            ("MRN100012", WaitlistEntry.Priority.URGENT, 3),
        ]:
            WaitlistEntry.objects.create(
                category=air_cushions, patient=services.get_patient(mrn), ward=WARDS[3], priority=priority,
                requested_by=staff[2], requested_at=now - timedelta(days=days_waiting),
            )

        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(items)} items in {len(CATEGORIES)} categories, 3 loans, 3 waitlist entries. "
            "Demo staff: aoife / ciaran / niamh (password demo-pass-123)."
        ))
