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


@override_settings(BT_DISCONNECT_SECONDS=10, STORAGES={
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class DisconnectTests(TestCase):
    """마지막 브리지 신호가 BT_DISCONNECT_SECONDS보다 오래되면 Disconnected. 한 번도 신호가 없으면 표시하지 않는다."""

    def setUp(self):
        from accounts.models import Staff
        from django.core.management import call_command
        from io import StringIO
        call_command("reset_pitch_demo", stdout=StringIO())
        self.client.force_login(Staff.objects.get(username="aoife"))
        self.d12 = Equipment.objects.get(asset_number="D12")
        self.d27 = Equipment.objects.get(asset_number="D27")

    def feed_row(self, asset):
        feed = self.client.get("/dashboard/feed/").json()
        return feed, next(i for i in feed["items"] if i["asset_number"] == asset)

    def age_bridge_logs(self, item, seconds):
        LocationLog.objects.filter(equipment=item).update(recorded_at=timezone.now() - timedelta(seconds=seconds))

    def heartbeat(self, exclude=()):
        return self.client.post("/demo/simulator/", json.dumps({"action": "heartbeat", "exclude": list(exclude)}),
                                content_type="application/json")

    def test_signal_states(self):
        from equipment.location import signal_states
        never = Equipment.objects.get(asset_number="WCS-001")
        self.age_bridge_logs(self.d12, 30)
        states = signal_states([self.d12.pk, self.d27.pk, never.pk])
        self.assertEqual(states, {self.d12.pk: "disconnected", self.d27.pk: "ok"})

    def test_dashboard_shows_disconnected_and_needs_locating(self):
        self.age_bridge_logs(self.d12, 30)
        feed, d12 = self.feed_row("D12")
        self.assertEqual((d12["signal"], feed["stats"]["needs_locating"]), ("disconnected", 1))
        Equipment.objects.filter(pk=self.d12.pk).update(location_updated_at=timezone.now() - timedelta(seconds=30))
        self.assertRegex(self.feed_row("D12")[1]["last_seen_text"], r"^3\d sec ago$")
        self.assertIsNone(self.feed_row("WCS-001")[1]["signal"])  # 브리지가 본 적 없는 장비
        self.assertEqual(self.feed_row("D27")[1]["signal"], "ok")

    def test_cut_then_restore(self):
        self.age_bridge_logs(self.d12, 30)
        self.age_bridge_logs(self.d27, 30)
        r = self.heartbeat(exclude=["D12"])  # Cut signal: D12 → D12만 하트비트에서 빠진다
        self.assertEqual(r.json()["assets"], ["D27"])
        self.assertEqual(self.feed_row("D27")[1]["signal"], "ok")
        self.assertEqual(self.feed_row("D12")[1]["signal"], "disconnected")
        self.heartbeat()  # Restore
        feed, d12 = self.feed_row("D12")
        self.assertEqual((d12["signal"], feed["stats"]["needs_locating"]), ("ok", 0))

    def test_heartbeat_keeps_current_location_status_and_stays_out_of_events(self):
        services.issue_item(self.d27, mrn="4100273")
        record_location(self.d27, location="Return Point", source=LocationLog.Source.PHONE)
        events_before = self.client.get("/dashboard/feed/").json()["events"]
        self.heartbeat()
        self.d27.refresh_from_db()
        self.assertEqual((self.d27.location, self.d27.status), ("Return Point", Equipment.Status.ON_LOAN))
        self.assertTrue(self.d27.location_logs.filter(raw_payload__heartbeat=True).exists())
        self.assertEqual([e["text"] for e in self.client.get("/dashboard/feed/").json()["events"]],
                         [e["text"] for e in events_before])

    def test_simulator_has_cut_and_restore_buttons(self):
        r = self.client.get("/demo/simulator/")
        self.assertContains(r, "Cut signal: D12")
        self.assertContains(r, "Restore signal: D12")
