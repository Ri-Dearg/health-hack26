import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from equipment import services
from equipment.location import active_tag, record_location
from equipment.models import Category, Equipment, LocationLog, Tag


class RecordLocationTests(TestCase):
    """블루투스 브리지 신호는 위치와 마지막 확인 시각만 바꾸고, 대여 상태는 절대 바꾸지 않는다."""

    def setUp(self):
        self.item = Equipment.objects.create(asset_number="D27", category=Category.objects.create(name="Wheelchair"))
        self.tag = Tag.objects.create(uid="BLUETOOTH-D27", tag_type=Tag.TagType.BLUETOOTH, equipment=self.item)

    def test_available_item_keeps_status_and_gets_one_log(self):
        record_location(self.item, location="Ward 7", tag=self.tag)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Equipment.Status.AVAILABLE)
        self.assertEqual(self.item.location, "Ward 7")
        log = LocationLog.objects.get(equipment=self.item)
        self.assertEqual((log.event, log.location, log.source, log.tag), (
            LocationLog.Event.SEEN, "Ward 7", LocationLog.Source.MICROBIT, self.tag))

    def test_on_loan_item_keeps_its_open_loan(self):
        loan = services.issue_item(self.item, mrn="4100273", ward="Ward 3")
        before = LocationLog.objects.count()
        record_location(self.item, location="Return Point", tag=self.tag)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Equipment.Status.ON_LOAN)
        self.assertEqual(self.item.open_loan, loan)
        self.assertEqual(self.item.location, "Return Point")
        self.assertEqual(LocationLog.objects.count(), before + 1)

    def test_unknown_or_detached_tag_finds_nothing(self):
        self.assertIsNone(active_tag("NOPE"))
        Tag.objects.create(uid="BLUETOOTH-SPARE", tag_type=Tag.TagType.BLUETOOTH)
        self.assertIsNone(active_tag("BLUETOOTH-SPARE"))
        Tag.objects.filter(pk=self.tag.pk).update(is_active=False)
        self.assertIsNone(active_tag("BLUETOOTH-D27"))

    def test_late_signal_is_logged_but_does_not_overwrite_newer_location(self):
        record_location(self.item, location="Ward 7", tag=self.tag)
        record_location(self.item, location="Ward 3", tag=self.tag, seen_at=timezone.now() - timedelta(minutes=5))
        self.item.refresh_from_db()
        self.assertEqual(self.item.location, "Ward 7")
        self.assertEqual(LocationLog.objects.filter(equipment=self.item).count(), 2)


class BridgeEndpointTests(TestCase):
    def setUp(self):
        self.item = Equipment.objects.create(asset_number="D12", category=Category.objects.create(name="Wheelchair"))
        Tag.objects.create(uid="BLUETOOTH-D12", tag_type=Tag.TagType.BLUETOOTH, equipment=self.item)

    def post(self, body, **headers):
        return self.client.post("/api/bridge-read/", json.dumps(body), content_type="application/json", **headers)

    def test_known_tag_updates_location_only(self):
        services.issue_item(self.item, mrn="4100999", ward="Ward 3")
        r = self.post({"tag": "BLUETOOTH-D12", "location": "Ward 7"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["outcome"], r.json()["asset_number"], r.json()["location"]),
                         ("recorded", "D12", "Ward 7"))
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Equipment.Status.ON_LOAN)
        self.assertEqual(self.item.loans.open().count(), 1)

    def test_unknown_tag_changes_nothing(self):
        before = LocationLog.objects.count()
        r = self.post({"tag": "NOPE", "location": "Ward 7"})
        self.assertEqual((r.status_code, r.json()["outcome"]), (200, "unknown"))
        self.assertEqual(LocationLog.objects.count(), before)

    def test_bad_requests(self):
        self.assertEqual(self.post({"tag": "BLUETOOTH-D12"}).status_code, 400)
        self.assertEqual(self.client.post("/api/bridge-read/", "nope", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.get("/api/bridge-read/").status_code, 405)

    def test_seen_at_is_used(self):
        seen = timezone.now() - timedelta(seconds=30)
        self.post({"tag": "BLUETOOTH-D12", "location": "Ward 7", "seen_at": seen.isoformat()})
        self.assertEqual(LocationLog.objects.get(equipment=self.item).recorded_at, seen)

    @override_settings(DEVICE_TOKEN="secret")
    def test_checks_device_token(self):
        self.assertEqual(self.post({"tag": "BLUETOOTH-D12", "location": "Ward 7"}).status_code, 403)
        r = self.post({"tag": "BLUETOOTH-D12", "location": "Ward 7"}, HTTP_X_DEVICE_TOKEN="secret")
        self.assertEqual(r.status_code, 200)
