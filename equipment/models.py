from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    default_loan_days = models.PositiveIntegerField(default=30)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Pool(models.Model):
    """Who an item is for. The register has sections such as "For the provision of clients in Burkitt / ARHC"."""

    DEFAULT_NAME = "OT Department"

    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


def default_pool():
    return Pool.objects.get_or_create(name=Pool.DEFAULT_NAME)[0].pk


class Equipment(models.Model):
    class Status(models.TextChoices):
        # The brief's own five statuses. "Overdue" and "needs an owner" are loan flags, not statuses.
        AVAILABLE = "available", "Available"
        ON_LOAN = "on_loan", "On loan"
        AWAITING_REPAIR = "awaiting_repair", "Awaiting repair"
        MISSING = "missing", "Missing"
        ALLOCATED = "allocated", "Allocated to another patient"

    # One field per column of TUH's equipment register (Asset Register and Cushion Register tabs),
    # labelled with the register's own headings. "Therapist" and "MRN" belong to the loan, not the item.
    # "Patient Name" is the one column never stored: MRN only.
    on_assettrak = models.BooleanField(
        "AssetTrak (AT+/AT-)", null=True, blank=True,
        help_text="Column A on the register. Assumed to mean the item is on AssetTrak. Blank if unknown.",
    )
    asset_number = models.CharField("Asset No.", max_length=50, unique=True)
    description = models.CharField("Type", max_length=200, blank=True, help_text="Make and model.")
    size = models.CharField("Size (WxDxH)", max_length=100, blank=True, help_text='As written, e.g. 18" x 17".')
    serial_number = models.CharField(
        "Serial No.", max_length=100, blank=True, help_text="Optional: not every item has one.",
    )
    hc21_number = models.CharField(
        "HC21 No.", max_length=50, blank=True, help_text="From the register. Meaning not yet confirmed with TUH.",
    )
    dml_number = models.CharField(
        "DML No.", max_length=50, blank=True, help_text="From the register. Meaning not yet confirmed with TUH.",
    )
    barcode = models.CharField(
        max_length=100, blank=True,
        help_text="Required for new items. Imported items without one are listed under 'Has barcode: No'.",
    )
    max_weight = models.CharField(
        "Max Wgt", max_length=50, blank=True, help_text="Maximum user weight as written, e.g. 135kg or 160kg / 25st.",
    )
    location = models.CharField("Ward / Location", max_length=100, default="OT store", help_text="Ward, bed or 'OT store'.")
    location_updated_at = models.DateTimeField(
        "Updated", default=timezone.now, help_text="When the location was last confirmed.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    register_status = models.CharField(
        "Status on register", max_length=200, blank=True,
        help_text="Exactly as written on the last imported register, e.g. 'OWWP' or 'STL - Due back in October'.",
    )
    comments = models.TextField(blank=True, help_text="Open text, e.g. replacement cover details.")
    last_service_date = models.DateField("Last Service date", null=True, blank=True)
    purchase_date = models.DateField(
        "Purchased in", null=True, blank=True, help_text="A month or year alone is stored as its first day.",
    )
    replacement_cover = models.BooleanField(
        "Replacement cover", null=True, blank=True, help_text="Cushions: Yes or No. Blank where it doesn't apply.",
    )
    # Added by the app, not columns on the register.
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="items")
    pool = models.ForeignKey(Pool, on_delete=models.PROTECT, default=default_pool, related_name="equipment")
    flagged = models.BooleanField(default=False, help_text="Needs someone to look at it (yellow on the register).")

    class Meta:
        verbose_name_plural = "equipment"
        ordering = ["category__name", "asset_number"]

    def __str__(self):
        return f"{self.asset_number} ({self.category})"

    @property
    def open_loan(self):
        return self.loans.filter(returned_at__isnull=True).first()


class Tag(models.Model):
    """A chip or label that identifies an item: micro:bit/Bluetooth beacon, NFC sticker, QR or barcode.

    Separate from Equipment so an item can carry more than one (e.g. Bluetooth plus a QR fallback)
    and a tag can be moved to another item without losing its history.
    """

    class TagType(models.TextChoices):
        BLUETOOTH = "bluetooth", "Bluetooth / micro:bit"
        NFC = "nfc", "NFC sticker"
        QR = "qr", "QR label"
        BARCODE = "barcode", "Barcode"

    uid = models.CharField("tag ID", max_length=100, unique=True, help_text="Serial number, NFC UID or QR payload.")
    tag_type = models.CharField(max_length=20, choices=TagType.choices)
    equipment = models.ForeignKey(
        Equipment, on_delete=models.SET_NULL, null=True, blank=True, related_name="tags",
        help_text="Blank means the tag is in the drawer, not on an item.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "chip / tag"
        verbose_name_plural = "chips / tags"

    def __str__(self):
        return f"{self.get_tag_type_display()} {self.uid}"


class Patient(models.Model):
    """MRN only. Never store names or other identifiers here; look them up in Synergy via get_patient()."""

    mrn = models.CharField("MRN", max_length=20, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["mrn"]

    def __str__(self):
        return self.mrn


class LoanQuerySet(models.QuerySet):
    def open(self):
        return self.filter(returned_at__isnull=True)

    def overdue(self):
        return self.open().filter(due_at__lt=timezone.now())


class Loan(models.Model):
    """One issue of one item: who issued it, to which patient, where, when, and when it came back."""

    equipment = models.ForeignKey(Equipment, on_delete=models.PROTECT, related_name="loans")
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, null=True, blank=True, related_name="loans")
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="loans_issued",
        help_text="Blank means the item left with no card tap: it needs an owner.",
    )
    issued_by_name = models.CharField(
        "therapist (as written)", max_length=100, blank=True,
        help_text="From an imported register, when the name doesn't match a staff account.",
    )
    ward = models.CharField(max_length=100, blank=True)
    bed = models.CharField(max_length=20, blank=True)
    issued_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField(blank=True, help_text="Defaults to the category's loan period (30 days).")
    returned_at = models.DateTimeField(null=True, blank=True)
    short_term_loan = models.BooleanField(default=False, help_text="Went home with the patient.")
    overdue_notice_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    notes = models.TextField(blank=True)

    objects = LoanQuerySet.as_manager()

    class Meta:
        ordering = ["-issued_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["equipment"], condition=Q(returned_at__isnull=True), name="one_open_loan_per_item",
                violation_error_message="This item already has an open loan. Return it first.",
            ),
        ]

    def __str__(self):
        return f"{self.equipment.asset_number} to {self.patient or 'unknown patient'}"

    def save(self, *args, **kwargs):
        if not self.due_at:
            self.due_at = self.issued_at + timedelta(days=self.equipment.category.default_loan_days)
        super().save(*args, **kwargs)

    @property
    def is_open(self):
        return self.returned_at is None

    @property
    def is_overdue(self):
        return self.is_open and self.due_at < timezone.now()

    @property
    def needs_owner(self):
        return self.issued_by_id is None


class WaitlistEntry(models.Model):
    class Priority(models.IntegerChoices):
        DISCHARGE_CRITICAL = 1, "Discharge-critical"
        URGENT = 2, "Urgent"
        ROUTINE = 3, "Routine"

    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting"
        ALLOCATED = "allocated", "Allocated"
        CANCELLED = "cancelled", "Cancelled"

    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="waitlist")
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="waitlist_entries")
    ward = models.CharField(max_length=100, blank=True)
    priority = models.IntegerField(choices=Priority.choices, default=Priority.ROUTINE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.WAITING)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    requested_at = models.DateTimeField(default=timezone.now)
    allocated_equipment = models.ForeignKey(Equipment, on_delete=models.SET_NULL, null=True, blank=True)
    skip_reason = models.CharField(
        max_length=200, blank=True, help_text="Required if someone higher on the list was passed over.",
    )

    class Meta:
        verbose_name_plural = "waitlist entries"
        ordering = ["priority", "requested_at"]  # clinical need, then time waiting

    def __str__(self):
        return f"{self.patient} needs {self.category} ({self.get_priority_display()})"


class LocationLog(models.Model):
    """Append-only record of every sighting or movement. Readers (micro:bit bridge, phone, admin) write here."""

    class Event(models.TextChoices):
        OUT = "item_out", "Left the store"
        IN = "item_in", "Returned to the store"
        SEEN = "seen", "Seen by a reader"
        LOCATION = "location", "Location updated"

    class Source(models.TextChoices):
        MICROBIT = "microbit", "micro:bit reader"
        PHONE = "phone", "Phone (NFC/QR)"
        MANUAL = "manual", "Manual entry"
        SPREADSHEET = "spreadsheet", "Spreadsheet import"
        SYNERGY = "synergy", "Synergy (EPR) event"

    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE, related_name="location_logs")
    tag = models.ForeignKey(Tag, on_delete=models.SET_NULL, null=True, blank=True)
    event = models.CharField(max_length=20, choices=Event.choices)
    location = models.CharField(max_length=100, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)
    rssi = models.SmallIntegerField("signal strength", null=True, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now, db_index=True)
    raw_payload = models.JSONField(null=True, blank=True)

    class Meta:
        verbose_name = "location log entry"
        verbose_name_plural = "equipment location log"
        ordering = ["-recorded_at"]

    def __str__(self):
        return f"{self.equipment.asset_number}: {self.get_event_display()} at {self.recorded_at:%d %b %H:%M}"


class TagEvent(models.Model):
    """Every tag read or QR action and what the rules decided. Unknown tags and odd cases wait here for review."""

    class Outcome(models.TextChoices):
        LOAN = "loan", "Loan opened"
        RETURN = "return", "Returned"
        LOCATION = "location", "Location checked"
        UNKNOWN_TAG = "unknown_tag", "Unknown or disabled tag"
        REPEAT = "repeat", "Repeated read, ignored"
        ALREADY_DONE = "already_done", "Already completed"
        CONFLICT = "conflict", "Conflicting update"
        NEEDS_REVIEW = "needs_review", "Needs staff review"

    tag_uid = models.CharField("tag ID", max_length=100, blank=True)
    reader_id = models.CharField("reader ID", max_length=100, blank=True)
    source = models.CharField(max_length=20, choices=LocationLog.Source.choices)
    read_at = models.DateTimeField(default=timezone.now, db_index=True)
    equipment = models.ForeignKey(Equipment, on_delete=models.SET_NULL, null=True, blank=True, related_name="events")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    reason = models.CharField(max_length=200, blank=True)
    needs_review = models.BooleanField(default=False)
    raw_payload = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-read_at"]

    def __str__(self):
        return f"{self.tag_uid or self.equipment}: {self.get_outcome_display()}"
