"""Loan rules. Admin actions call these now; the events endpoint will call the same functions later."""

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import Equipment, Loan, LocationLog, Patient, WaitlistEntry


def get_patient(mrn: str) -> Patient:
    """Stub for a Synergy (EPR) lookup. Later: a FHIR Patient search by MRN via TUH ICT and Kainos."""
    patient, _ = Patient.objects.get_or_create(mrn=mrn.strip().upper())
    return patient


@transaction.atomic
def issue_item(equipment, *, staff=None, mrn="", ward="", bed="", source=LocationLog.Source.MANUAL, due_at=None):
    """Open a loan. Never refuses because a field is missing: no staff means the loan needs an owner."""
    loan = Loan.objects.create(
        equipment=equipment,
        issued_by=staff,
        patient=get_patient(mrn) if mrn else None,
        ward=ward,
        bed=bed,
        due_at=due_at,
    )
    equipment.status = Equipment.Status.ON_LOAN
    equipment.location = ward or "Left the store"
    equipment.location_updated_at = timezone.now()
    equipment.save(update_fields=["status", "location", "location_updated_at"])
    LocationLog.objects.create(
        equipment=equipment, event=LocationLog.Event.OUT, location=equipment.location, source=source, recorded_by=staff,
    )
    return loan


@transaction.atomic
def return_item(equipment, *, staff=None, source=LocationLog.Source.MANUAL):
    """Close the open loan (no tap needed), mark the item available and return the top waitlist entry, if any."""
    now = timezone.now()
    equipment.loans.open().update(returned_at=now)
    equipment.status = Equipment.Status.AVAILABLE
    equipment.location = "OT store"
    equipment.location_updated_at = now
    equipment.save(update_fields=["status", "location", "location_updated_at"])
    LocationLog.objects.create(
        equipment=equipment, event=LocationLog.Event.IN, location="OT store", source=source, recorded_by=staff,
    )
    return next_on_waitlist(equipment.category)


def next_on_waitlist(category):
    """Highest clinical priority first, then longest waiting."""
    return WaitlistEntry.objects.filter(category=category, status=WaitlistEntry.Status.WAITING).first()


def send_overdue_notices():
    """Email the issuing OT once for each overdue loan. Returns the number of notices sent."""
    sent = 0
    for loan in Loan.objects.overdue().filter(overdue_notice_sent_at__isnull=True).select_related(
        "equipment", "issued_by", "patient"
    ):
        recipient = loan.issued_by.email if loan.issued_by else None
        if not recipient:
            continue  # needs an owner: shows on the dashboard instead
        send_mail(
            subject=f"Overdue: {loan.equipment.asset_number}",
            message=(
                f"{loan.equipment} was due back on {loan.due_at:%d %B %Y}.\n"
                f"Patient MRN: {loan.patient or 'not recorded'}. Ward: {loan.ward or 'not recorded'}.\n"
                "If it is still in use, extend the loan; otherwise please arrange its return."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
        )
        loan.overdue_notice_sent_at = timezone.now()
        loan.save(update_fields=["overdue_notice_sent_at"])
        sent += 1
    return sent
