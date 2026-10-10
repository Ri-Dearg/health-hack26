"""위치만 기록하는 규칙 (블루투스 브리지, NFC 반납 지점, 시뮬레이터가 모두 이 함수를 부른다).

대여·반납 상태는 절대 바꾸지 않는다. 상태 변경은 services.issue_item / return_item 몫이다.
기존 /api/tag-read/ 의 방향 추론(events.py)과는 별개 경로다.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import Equipment, LocationLog, Tag

RETURN_POINT = "Return Point"


def active_tag(tag_uid):
    """활성 상태로 장비에 붙어 있는 태그만 인정한다. 모르거나 떼어진 태그면 None."""
    return Tag.objects.filter(uid=tag_uid, is_active=True, equipment__isnull=False).select_related("equipment").first()


@transaction.atomic
def record_location(equipment, *, location, tag=None, source=LocationLog.Source.MICROBIT, seen_at=None,
                    staff=None, rssi=None, payload=None):
    """마지막 확인 위치와 시각만 갱신하고 LocationLog에 한 줄 추가한다.

    늦게 도착한 신호(seen_at이 현재 기록보다 과거)는 로그에만 남기고 장비의 현재 위치는 덮어쓰지 않는다.
    """
    seen_at = seen_at or timezone.now()
    item = Equipment.objects.select_for_update().get(pk=equipment.pk)
    if seen_at >= item.location_updated_at:
        item.location = location
        item.location_updated_at = seen_at
        item.save(update_fields=["location", "location_updated_at"])
    return LocationLog.objects.create(
        equipment=item, tag=tag, event=LocationLog.Event.SEEN, location=location, source=source,
        rssi=rssi, recorded_by=staff, recorded_at=seen_at, raw_payload=payload,
    )


def signal_states(equipment_ids, now=None):
    """브리지(블루투스) 신호 상태: {장비 id: "ok" | "disconnected"}.

    마지막 브리지 신호가 BT_DISCONNECT_SECONDS보다 오래되면 disconnected. 브리지가 한 번도 본 적 없는 장비는 빠진다.
    마이그레이션 없이 LocationLog에서 계산한다.
    """
    cutoff = (now or timezone.now()) - timedelta(seconds=getattr(settings, "BT_DISCONNECT_SECONDS", 10))
    last = (LocationLog.objects.filter(equipment_id__in=equipment_ids, source=LocationLog.Source.MICROBIT)
            .values("equipment_id").annotate(last=Max("recorded_at")).values_list("equipment_id", "last"))
    return {pk: "ok" if seen >= cutoff else "disconnected" for pk, seen in last}
