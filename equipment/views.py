import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import services
from .events import apply_action, handle_tag_read
from .location import RETURN_POINT, record_location
from .models import Equipment, LocationLog, TagEvent
from .notify import notify_loan

# 시연용 환자 목록. 화면의 선택지일 뿐이고, 서버는 어떤 MRN이든 기존처럼 받는다.
DEMO_MRNS = [str(n) for n in range(1001, 1007)]


@csrf_exempt
@require_POST
def tag_read(request):
    """Readers (micro:bit bridge, phone) POST {"tag_id", "reader_id", "time"?}. Send X-Device-Token if one is set."""
    token = getattr(settings, "DEVICE_TOKEN", "")
    if token and request.headers.get("X-Device-Token") != token:
        return JsonResponse({"error": "bad device token"}, status=403)
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "body must be JSON"}, status=400)
    if not data.get("tag_id"):
        return JsonResponse({"error": "tag_id is required"}, status=400)
    source = data.get("source") if data.get("source") in LocationLog.Source.values else LocationLog.Source.MICROBIT
    result = handle_tag_read(
        str(data["tag_id"]), reader_id=str(data.get("reader_id", "")), source=source, payload=data,
        read_at=parse_datetime(data["time"]) if data.get("time") else None,
    )
    item = result.equipment
    return JsonResponse({
        "outcome": result.outcome,
        "message": result.message,
        "asset_number": item.asset_number if item else None,
        "status": item.get_status_display() if item else None,
    })


@login_required
def item_page(request, code):
    """QR / NFC-link page: see the item, choose loan, return or location check, confirm."""
    item = Equipment.objects.filter(Q(tags__uid=code) | Q(asset_number__iexact=code)).select_related(
        "category", "pool").first()
    if not item:
        raise Http404("No item with that tag or asset number")
    result = next_entry = None
    if request.method == "POST":
        result = apply_action(
            item, request.POST.get("action", ""), expected_status=request.POST.get("expected_status", ""),
            staff=request.user, mrn=request.POST.get("mrn", ""), ward=request.POST.get("ward", ""),
            bed=request.POST.get("bed", ""), location=request.POST.get("location", ""),
            source=LocationLog.Source.PHONE, tag_uid=code,
        )
        if result.outcome == TagEvent.Outcome.RETURN:
            # 반납 = 반납 지점에 놓였다는 뜻. 위치 규칙은 location.record_location 하나만 쓴다.
            record_location(item, location=RETURN_POINT, source=LocationLog.Source.PHONE, staff=request.user)
            next_entry = services.next_on_waitlist(item.category)
        item.refresh_from_db()
        if result.outcome == TagEvent.Outcome.LOAN:
            notify_loan(item.open_loan)
    return render(request, "equipment/item.html", {
        "item": item, "loan": item.open_loan, "result": result, "next_entry": next_entry,
        "demo_mrns": DEMO_MRNS,
    })
