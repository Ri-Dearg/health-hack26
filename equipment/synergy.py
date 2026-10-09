"""Integration seam for Synergy, TUH's EPR (Kainos Evolve). Nothing here talks to Synergy tonight.

Three touch points, all through TUH's integration engine and agreed with TUH ICT and Kainos:

1. Patient lookup (we ask):      FHIR  GET [base]/Patient?identifier=<MRN system>|<mrn>
2. Patient moves (Synergy tells): HL7 v2 ADT A02 (transfer) and A03 (discharge), or FHIR Encounter updates
3. Loan record (we tell):         FHIR DeviceUseStatement (R4), or an HL7 MDM T02 document, which Evolve
                                   already accepts from third-party systems

The system URIs below are placeholders until TUH tells us its real identifier systems.
"""

from django.db import transaction
from django.utils import timezone

from .models import Loan, LocationLog, Patient

MRN_SYSTEM = "urn:tuh:placeholder:mrn"
ASSET_SYSTEM = "urn:tuh:placeholder:ot-asset-number"
STAFF_SYSTEM = "urn:tuh:placeholder:staff-number"


def loan_to_fhir(loan: Loan) -> dict:
    """The loan as a FHIR R4 DeviceUseStatement: what Synergy would receive. Identifiers only, no names."""
    resource = {
        "resourceType": "DeviceUseStatement",
        "status": "active" if loan.is_open else "completed",
        "subject": {"identifier": {"system": MRN_SYSTEM, "value": loan.patient.mrn if loan.patient else "unknown"}},
        "device": {
            "identifier": {"system": ASSET_SYSTEM, "value": loan.equipment.asset_number},
            "display": loan.equipment.category.name,
        },
        "timingPeriod": {"start": loan.issued_at.isoformat()},
        "recordedOn": loan.issued_at.isoformat(),
        "note": [{"text": f"OT loan. Ward {loan.ward or '?'}, bed {loan.bed or '?'}. Due back {loan.due_at:%d/%m/%Y}."}],
    }
    if loan.returned_at:
        resource["timingPeriod"]["end"] = loan.returned_at.isoformat()
    if loan.issued_by and loan.issued_by.staff_number:
        resource["source"] = {"identifier": {"system": STAFF_SYSTEM, "value": loan.issued_by.staff_number}}
    if loan.short_term_loan:
        resource["note"].append({"text": "Short-term loan: went home with the patient."})
    return resource


@transaction.atomic
def handle_adt(event: str, mrn: str, ward: str = "", bed: str = "") -> list[Loan]:
    """React to a patient-movement message from Synergy. Returns the open loans it touched.

    A02 transfer:  the patient's equipment follows them, so update each open loan's ward and bed.
    A03 discharge: equipment still out is now a short-term loan (gone home). Flag it so the
                   return label and the 30-day reminder kick in, instead of it silently going missing.
    """
    patient = Patient.objects.filter(mrn=mrn.strip().upper()).first()
    if not patient:
        return []
    loans = list(patient.loans.open().select_related("equipment"))
    for loan in loans:
        item = loan.equipment
        if event == "A02":
            loan.ward, loan.bed = ward or loan.ward, bed or loan.bed
            item.location = loan.ward
            label = f"Moved with patient to {loan.ward}"
        elif event == "A03":
            loan.short_term_loan = True
            item.location = "Home with patient"
            label = item.location
        else:
            raise ValueError(f"Unsupported ADT event {event!r}; expected A02 or A03")
        loan.save()
        item.location_updated_at = timezone.now()
        item.save(update_fields=["location", "location_updated_at"])
        LocationLog.objects.create(
            equipment=item, event=LocationLog.Event.LOCATION, location=label, source=LocationLog.Source.SYNERGY,
            raw_payload={"adt": event, "mrn": patient.mrn, "ward": ward, "bed": bed},
        )
    return loans
