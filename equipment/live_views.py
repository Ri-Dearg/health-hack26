"""피칭 시연용 화면과 수신 경로: 블루투스 브리지 수신, 실시간 대시보드."""

import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import services
from .location import active_tag, record_location
from .models import Equipment, Loan, LocationLog, Tag, WaitlistEntry

EVENT_TEXT = {
    LocationLog.Event.OUT: "issued",
    LocationLog.Event.IN: "returned",
    LocationLog.Event.SEEN: "seen at {location}",
    LocationLog.Event.LOCATION: "moved to {location}",
}
SOURCE_TEXT = {LocationLog.Source.MICROBIT: "Bluetooth bridge", LocationLog.Source.PHONE: "NFC phone"}


# 나중에 실제 Wiliot 클라우드 웹훅이 이 자리를 대신할 수 있다 (같은 record_location을 부르면 된다).
@csrf_exempt
@require_POST
def bridge_read(request):
    """Bluetooth bridges POST {"tag", "location", "seen_at"?, "rssi"?}. Location only; never changes loan status."""
    token = getattr(settings, "DEVICE_TOKEN", "")
    if token and request.headers.get("X-Device-Token") != token:
        return JsonResponse({"error": "bad device token"}, status=403)
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "body must be JSON"}, status=400)
    if not isinstance(data, dict) or not data.get("tag") or not data.get("location"):
        return JsonResponse({"error": "tag and location are required"}, status=400)
    try:
        seen_at = parse_datetime(str(data["seen_at"])) if data.get("seen_at") else None
    except ValueError:
        seen_at = None
    if seen_at and timezone.is_naive(seen_at):
        seen_at = timezone.make_aware(seen_at)
    tag = active_tag(str(data["tag"]))
    if not tag:
        return JsonResponse({"outcome": "unknown", "message": "Tag not registered, disabled or not on an item."})
    rssi = data.get("rssi") if isinstance(data.get("rssi"), int) else None
    record_location(tag.equipment, location=str(data["location"])[:100], tag=tag, seen_at=seen_at, rssi=rssi,
                    payload=data)
    item = tag.equipment
    item.refresh_from_db()
    return JsonResponse({"outcome": "recorded", "asset_number": item.asset_number, "location": item.location,
                         "status": item.get_status_display()})


def ago(when, now):
    """큰 화면용 상대 시간: just now / 2 min ago / 3 h ago / 4 days ago."""
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} days ago"


def feed_data():
    """대시보드 한 화면에 필요한 모든 값. 표는 블루투스 태그가 붙은(자동 위치 추적) 장비만 보여준다."""
    now = timezone.now()
    items = (
        Equipment.objects.filter(tags__tag_type=Tag.TagType.BLUETOOTH, tags__is_active=True).distinct()
        .select_related("category")
        .prefetch_related(Prefetch("loans", queryset=Loan.objects.open(), to_attr="open_loans"))
        .order_by("asset_number")
    )
    rows = []
    for item in items:
        loan = item.open_loans[0] if item.open_loans else None
        rows.append({
            "asset_number": item.asset_number,
            "category": item.category.name,
            "status": item.status,
            "status_label": item.get_status_display(),
            "overdue": bool(loan and loan.is_overdue),
            "location": item.location,
            "last_seen": item.location_updated_at.isoformat(),
            "last_seen_text": ago(item.location_updated_at, now),
            "signal": None,  # 슬라이스 5(연결 끊김) 자리
        })

    next_patient = None
    last_return = Loan.objects.filter(returned_at__isnull=False).select_related("equipment__category").order_by(
        "-returned_at").first()
    if last_return and last_return.equipment.status == Equipment.Status.AVAILABLE:
        entry = services.next_on_waitlist(last_return.equipment.category)
        if entry:
            next_patient = {
                "mrn": entry.patient.mrn, "priority": entry.get_priority_display(), "ward": entry.ward,
                "category": entry.category.name, "asset_number": last_return.equipment.asset_number,
                "location": last_return.equipment.location,
            }

    events = []
    for log in LocationLog.objects.select_related("equipment").order_by("-recorded_at", "-pk")[:8]:
        events.append({
            "time": log.recorded_at.isoformat(),
            "ago": ago(log.recorded_at, now),
            "text": f"{log.equipment.asset_number} " + EVENT_TEXT.get(log.event, log.event).format(location=log.location),
            "source": SOURCE_TEXT.get(log.source, log.get_source_display()),
        })

    return {
        "now": now.isoformat(),
        "stats": {
            "waiting": WaitlistEntry.objects.filter(status=WaitlistEntry.Status.WAITING).count(),
            "overdue": Loan.objects.overdue().count(),
            "needs_locating": 0,  # 슬라이스 5에서 연결 끊김 수로 채운다
            "available": Equipment.objects.filter(status=Equipment.Status.AVAILABLE).count(),
        },
        "items": rows,
        "next_patient": next_patient,
        "events": events,
    }


@login_required
def dashboard(request):
    return render(request, "equipment/dashboard.html")


@login_required
def dashboard_feed(request):
    return JsonResponse(feed_data())
