"""피칭 시연의 시작 상태. reset_pitch_demo 명령과 시뮬레이터의 Reset 버튼이 같은 reset()을 부른다.

기존 seed_demo(--reset)를 그대로 돌린 뒤, 그 위에 시연용 장비 D27·D12와 휠체어 대기 명단을 얹는다.
모든 데이터는 지어낸 것이다. 환자는 MRN만.
"""

from io import StringIO

from django.core.management import call_command
from django.db import transaction

from accounts.models import Staff

from . import services
from .location import RETURN_POINT, record_location
from .models import Category, Equipment, Tag, TagEvent, WaitlistEntry

STORE, WARD_7, WARD_3 = "OT Store Room", "Ward 7", "Ward 3"
ZONES = [STORE, WARD_7, WARD_3, RETURN_POINT]

DEMO_MRN = "4100273"  # 시연에서 아이폰으로 입력할 MRN (대기 명단에 없는 환자)
D12_MRN = "4100145"
WAITLIST = [  # 휠체어 대기 명단: 우선순위가 서로 다르다. 1순위 = 4100512
    ("4100512", WaitlistEntry.Priority.DISCHARGE_CRITICAL, WARD_7),
    ("4100388", WaitlistEntry.Priority.URGENT, WARD_3),
    ("4100601", WaitlistEntry.Priority.ROUTINE, WARD_7),
]

# 시뮬레이터 버튼 (자산번호, 구역)
MOVES = [("D27", WARD_7), ("D27", RETURN_POINT)]


@transaction.atomic
def reset():
    TagEvent.objects.all().delete()
    call_command("seed_demo", reset=True, stdout=StringIO())

    chairs = Category.objects.get(name="Manual wheelchair")
    staff = Staff.objects.get(username="niamh")

    d27 = Equipment.objects.create(asset_number="D27", category=chairs, description="Demo wheelchair", location=STORE)
    Tag.objects.create(uid="NFC-D27", tag_type=Tag.TagType.NFC, equipment=d27)
    bt27 = Tag.objects.create(uid="BLUETOOTH-D27", tag_type=Tag.TagType.BLUETOOTH, equipment=d27)
    record_location(d27, location=STORE, tag=bt27)

    d12 = Equipment.objects.create(asset_number="D12", category=chairs, description="Demo wheelchair", location=STORE)
    bt12 = Tag.objects.create(uid="BLUETOOTH-D12", tag_type=Tag.TagType.BLUETOOTH, equipment=d12)
    services.issue_item(d12, staff=staff, mrn=D12_MRN, ward=WARD_3)
    record_location(d12, location=WARD_3, tag=bt12)

    for mrn, priority, ward in WAITLIST:
        WaitlistEntry.objects.create(category=chairs, patient=services.get_patient(mrn), ward=ward, priority=priority,
                                     requested_by=staff)
