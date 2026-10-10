"""피칭 시연용 화면과 수신 경로: 블루투스 브리지 수신."""

import json

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .location import active_tag, record_location


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
