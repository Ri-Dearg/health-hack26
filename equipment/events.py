"""Tag reads and QR actions, following the team's flowchart. Every path ends in a TagEvent row.

    tag read -> known, enabled tag on an item?   no  -> unknown_tag (flag for review)
             -> changed in the last N seconds?   yes -> repeat (blocked, reason recorded)
             -> available -> loan; on loan -> return; any other status -> needs_review
    QR page  -> staff choose loan, return or location check, then confirm
    both     -> apply_action: lock the row, check it against the latest record
                (already_done / conflict / valid), then update and log in one transaction
"""

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import services
from .models import Equipment, LocationLog, Tag, TagEvent

S, O = Equipment.Status, TagEvent.Outcome
DONE_STATUS = {"loan": S.ON_LOAN, "return": S.AVAILABLE}


def repeat_seconds():
    return getattr(settings, "TAG_REPEAT_SECONDS", 60)


@dataclass
class Result:
    outcome: str
    message: str
    equipment: Equipment | None = None
    event: TagEvent | None = None


def _record(outcome, message, *, equipment=None, needs_review=False, **fields):
    event = TagEvent.objects.create(
        outcome=outcome, reason=message[:200], equipment=equipment, needs_review=needs_review, **fields,
    )
    return Result(outcome, message, equipment, event)


def handle_tag_read(tag_uid, *, reader_id="", read_at=None, source=LocationLog.Source.MICROBIT, payload=None):
    """A reader saw a tag. Never blocks anything; odd cases are recorded for a person to look at."""
    read_at = read_at or timezone.now()
    common = {"tag_uid": tag_uid, "reader_id": reader_id, "read_at": read_at, "source": source, "raw_payload": payload}
    tag = Tag.objects.filter(uid=tag_uid, is_active=True, equipment__isnull=False).select_related("equipment").first()
    if not tag:
        return _record(O.UNKNOWN_TAG, "Tag not registered, disabled or not on an item. Status unchanged.",
                       needs_review=True, **common)
    item = tag.equipment
    since = read_at - timedelta(seconds=repeat_seconds())
    if item.events.filter(outcome__in=[O.LOAN, O.RETURN], read_at__gte=since).exists():
        return _record(O.REPEAT, f"Changed less than {repeat_seconds()} seconds ago. Repeat read ignored.",
                       equipment=item, **common)
    action = {S.AVAILABLE: "loan", S.ON_LOAN: "return"}.get(item.status)
    if not action:
        return _record(O.NEEDS_REVIEW, f"Read while '{item.get_status_display()}'. Not changed automatically.",
                       equipment=item, needs_review=True, **common)
    return apply_action(item, action, expected_status=item.status, location=reader_id, **common)


@transaction.atomic
def apply_action(equipment, action, *, expected_status, staff=None, mrn="", ward="", bed="", location="",
                 source=LocationLog.Source.PHONE, tag_uid="", reader_id="", read_at=None, raw_payload=None):
    """The shared rules. action is 'loan', 'return' or 'location'."""
    common = {"tag_uid": tag_uid, "reader_id": reader_id, "read_at": read_at or timezone.now(), "source": source,
              "raw_payload": raw_payload, "recorded_by": staff}
    item = Equipment.objects.select_for_update().get(pk=equipment.pk)  # validate against the latest record

    if action in DONE_STATUS:
        if item.status == DONE_STATUS[action]:
            return _record(O.ALREADY_DONE, f"{item.asset_number} is already {item.get_status_display().lower()}.",
                           equipment=item, **common)
        if item.status != expected_status:
            return _record(O.CONFLICT, f"{item.asset_number} changed to '{item.get_status_display()}' meanwhile. "
                           "Nothing changed; check the latest details.", equipment=item, needs_review=True, **common)

    if action == "loan":
        loan = services.issue_item(item, staff=staff, mrn=mrn, ward=ward, bed=bed, source=source)
        owner = "" if staff else " No card tap, so it needs an owner."
        return _record(O.LOAN, f"{item.asset_number} on loan, due back {loan.due_at:%d %b}.{owner}",
                       equipment=item, **common)
    if action == "return":
        offer = services.return_item(item, staff=staff, source=source)
        extra = f" Next on the waitlist: {offer}." if offer else ""
        return _record(O.RETURN, f"{item.asset_number} returned.{extra}", equipment=item, **common)
    if action == "location":
        item.location = location or ward or item.location
        item.location_updated_at = timezone.now()
        item.save(update_fields=["location", "location_updated_at"])
        LocationLog.objects.create(equipment=item, event=LocationLog.Event.SEEN, location=item.location,
                                   source=source, recorded_by=staff)
        return _record(O.LOCATION, f"{item.asset_number} seen at {item.location}.", equipment=item, **common)
    raise ValueError(f"Unknown action {action!r}")
