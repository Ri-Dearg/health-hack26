"""피칭 시연용 화면과 수신 경로: 블루투스 브리지 수신, 실시간 대시보드."""

import json

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import pitch_demo, services
from .location import active_tag, record_location, signal_states
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
    """큰 화면용 상대 시간: just now / 25 sec ago / 2 min ago / 3 h ago / 4 days ago."""
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 10:
        return "just now"
    if seconds < 60:
        return f"{seconds} sec ago"  # 연결이 끊긴 장비는 초 단위로 늘어나는 게 보여야 한다
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
    items = list(items)
    signals = signal_states([item.pk for item in items], now)
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
            "signal": signals.get(item.pk),  # "ok" / "disconnected" / None(브리지가 본 적 없음)
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
    # 시뮬레이터 하트비트는 로그에는 남기지만 최근 이벤트 목록은 덮지 않게 뺀다.
    logs = LocationLog.objects.exclude(raw_payload__heartbeat=True).select_related("equipment")
    for log in logs.order_by("-recorded_at", "-pk")[:8]:
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
            "needs_locating": sum(1 for state in signals.values() if state == "disconnected"),
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


@staff_member_required
def simulator(request):
    """시연용 블루투스 신호 시뮬레이터. 버튼은 fetch로 POST하고, 브리지와 같은 record_location을 부른다."""
    if request.method != "POST":
        moves = [{"asset": a, "location": loc, "label": f"{a} \u2192 {loc}"} for a, loc in pitch_demo.MOVES]
        return render(request, "equipment/simulator.html", {
            "moves": moves, "cuts": pitch_demo.CUTS, "heartbeat_ms": settings.BT_HEARTBEAT_SECONDS * 1000})
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "body must be JSON"}, status=400)
    action = data.get("action") if isinstance(data, dict) else None
    if action == "reset":
        pitch_demo.reset()
        return JsonResponse({"ok": True, "message": "Demo reset."})
    if action == "move":
        tag = Tag.objects.filter(equipment__asset_number=str(data.get("asset", "")), tag_type=Tag.TagType.BLUETOOTH,
                                 is_active=True).select_related("equipment").first()
        location = str(data.get("location", ""))[:100]
        if not tag or not location:
            return JsonResponse({"ok": False, "error": "No Bluetooth tag on that item, or no location."}, status=400)
        record_location(tag.equipment, location=location, tag=tag, payload={"simulated": True, **data})
        return JsonResponse({"ok": True, "message": f"{tag.equipment.asset_number} seen at {location}."})
    if action == "heartbeat":
        # 브리지가 계속 보고 있다는 신호: 이미 브리지가 본 적 있는 장비를 현재 위치 그대로 다시 기록한다(끊은 장비는 제외).
        exclude = {str(a) for a in data.get("exclude", []) or []}
        tags = (Tag.objects.filter(tag_type=Tag.TagType.BLUETOOTH, is_active=True, equipment__isnull=False,
                                   equipment__location_logs__source=LocationLog.Source.MICROBIT)
                .exclude(equipment__asset_number__in=exclude).select_related("equipment").distinct()
                .order_by("equipment__asset_number"))
        beat = []
        for tag in tags:
            item = Equipment.objects.get(pk=tag.equipment_id)  # 방금 바뀐 위치(예: 반납 지점)를 그대로 유지
            record_location(item, location=item.location, tag=tag, payload={"simulated": True, "heartbeat": True})
            beat.append(item.asset_number)
        return JsonResponse({"ok": True, "assets": beat, "message": "Heartbeat: " + (", ".join(beat) or "none")})
    return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)
