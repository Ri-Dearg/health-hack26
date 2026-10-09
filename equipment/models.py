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


class Equipment(models.Model):
    class Status(models.TextChoices):
        # The brief's own five statuses. "Overdue" and "needs an owner" are loan flags, not statuses.
        AVAILABLE = "available", "Available"
        ON_LOAN = "on_loan", "On loan"
        AWAITING_REPAIR = "awaiting_repair", "Awaiting repair"
        MISSING = "missing", "Missing"
        ALLOCATED = "allocated", "Allocated to another patient"

    class Pool(models.TextChoices):
        OT = "ot", "OT Department"
        WINTER = "winter", "Winter Initiative"

    asset_number = models.CharField(max_length=50, unique=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="items")
    description = models.CharField(max_length=200, blank=True)
    barcode = models.CharField(max_length=100, blank=True)
    serial_number = models.CharField(max_length=100, blank=True, help_text="Not every item has one.")
    pool = models.CharField(max_length=10, choices=Pool.choices, default=Pool.OT)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    location = models.CharField(max_length=100, default="OT store", help_text="Ward, bed or 'OT store'.")
    location_updated_at = models.DateTimeField(default=timezone.now)
    purchase_date = models.DateField(null=True, blank=True)
    last_service_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

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
