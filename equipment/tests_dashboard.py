from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import Staff
from equipment import services
from equipment.location import record_location
from equipment.models import Category, Equipment, Tag, WaitlistEntry


class DashboardTests(TestCase):
    def setUp(self):
        self.chairs = Category.objects.create(name="Manual wheelchair")
        self.d27 = Equipment.objects.create(asset_number="D27", category=self.chairs, location="OT Store Room")
        self.d12 = Equipment.objects.create(asset_number="D12", category=self.chairs)
        self.cushion = Equipment.objects.create(asset_number="CSA-001", category=Category.objects.create(name="Cushion"))
        Tag.objects.create(uid="BLUETOOTH-D27", tag_type=Tag.TagType.BLUETOOTH, equipment=self.d27)
        Tag.objects.create(uid="BLUETOOTH-D12", tag_type=Tag.TagType.BLUETOOTH, equipment=self.d12)
        Tag.objects.create(uid="NFC-CSA-001", tag_type=Tag.TagType.NFC, equipment=self.cushion)
        self.staff = Staff.objects.create_user("ot", password="x", is_staff=True)

    def feed(self):
        self.client.force_login(self.staff)
        r = self.client.get("/dashboard/feed/")
        self.assertEqual(r.status_code, 200)
        return r.json()

    def row(self, data, asset):
        return next(i for i in data["items"] if i["asset_number"] == asset)

    def test_pages_need_login(self):
        self.assertEqual(self.client.get("/dashboard/").status_code, 302)
        self.assertEqual(self.client.get("/dashboard/feed/").status_code, 302)
        self.client.force_login(self.staff)
        r = self.client.get("/dashboard/")
        for text in ["Patients waiting", "Overdue loans", "Needs locating", "Available now", "Next patient"]:
            self.assertContains(r, text)

    def test_feed_lists_bluetooth_items_with_status_location_and_last_seen(self):
        data = self.feed()
        self.assertEqual([i["asset_number"] for i in data["items"]], ["D12", "D27"])
        d27 = self.row(data, "D27")
        self.assertEqual((d27["status"], d27["status_label"], d27["location"]), ("available", "Available", "OT Store Room"))
        self.assertIn("last_seen", d27)
        self.assertEqual(d27["last_seen_text"], "just now")

    def test_location_signal_shows_in_feed(self):
        Equipment.objects.filter(pk=self.d27.pk).update(location_updated_at=timezone.now() - timedelta(minutes=2))
        self.assertEqual(self.row(self.feed(), "D27")["last_seen_text"], "2 min ago")
        record_location(self.d27, location="Ward 7")
        d27 = self.row(self.feed(), "D27")
        self.assertEqual((d27["location"], d27["last_seen_text"], d27["status"]), ("Ward 7", "just now", "available"))
        self.assertIn("Ward 7", self.feed()["events"][0]["text"])

    def test_stats(self):
        services.issue_item(self.d12, mrn="4100999", ward="Ward 3", due_at=timezone.now() - timedelta(days=1))
        WaitlistEntry.objects.create(category=self.chairs, patient=services.get_patient("4100512"))
        stats = self.feed()["stats"]
        self.assertEqual(stats, {"waiting": 1, "overdue": 1, "needs_locating": 0, "available": 2})
        self.assertTrue(self.row(self.feed(), "D12")["overdue"])

    def test_next_patient_appears_after_a_return(self):
        WaitlistEntry.objects.create(category=self.chairs, patient=services.get_patient("4100388"),
                                     priority=WaitlistEntry.Priority.URGENT)
        WaitlistEntry.objects.create(category=self.chairs, patient=services.get_patient("4100512"),
                                     priority=WaitlistEntry.Priority.DISCHARGE_CRITICAL)
        self.assertIsNone(self.feed()["next_patient"])
        services.issue_item(self.d27, mrn="4100273")
        services.return_item(self.d27)
        nxt = self.feed()["next_patient"]
        self.assertEqual((nxt["mrn"], nxt["priority"], nxt["asset_number"]), ("4100512", "Discharge-critical", "D27"))
        services.issue_item(self.d27, mrn="4100512")
        self.assertIsNone(self.feed()["next_patient"])  # 다시 나가면 강조할 것이 없다
