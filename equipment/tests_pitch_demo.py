import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import Staff
from equipment import pitch_demo
from equipment.models import Equipment, Loan, LocationLog, Tag, TagEvent, WaitlistEntry

STATIC = {"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}


def reset():
    call_command("reset_pitch_demo", stdout=StringIO())


class ResetTests(TestCase):
    def test_start_state(self):
        reset()
        d27 = Equipment.objects.get(asset_number="D27")
        self.assertEqual((d27.status, d27.location), (Equipment.Status.AVAILABLE, "OT Store Room"))
        self.assertEqual(set(d27.tags.values_list("tag_type", flat=True)), {Tag.TagType.NFC, Tag.TagType.BLUETOOTH})
        self.assertTrue(d27.location_logs.filter(source=LocationLog.Source.MICROBIT).exists())

        d12 = Equipment.objects.get(asset_number="D12")
        self.assertEqual((d12.status, d12.location), (Equipment.Status.ON_LOAN, "Ward 3"))
        self.assertTrue(d12.tags.filter(tag_type=Tag.TagType.BLUETOOTH).exists())
        self.assertEqual(d27.category, d12.category)

        self.assertGreater(Loan.objects.overdue().count(), 0)
        waiting = WaitlistEntry.objects.filter(category=d27.category, status=WaitlistEntry.Status.WAITING)
        self.assertGreaterEqual(waiting.count(), 2)
        self.assertEqual(len(set(waiting.values_list("priority", flat=True))), waiting.count())
        self.assertNotIn(pitch_demo.DEMO_MRN, waiting.values_list("patient__mrn", flat=True))

    def test_reset_twice_gives_the_same_state(self):
        reset()
        Equipment.objects.filter(asset_number="D27").update(location="Somewhere")
        reset()
        self.assertEqual(Equipment.objects.get(asset_number="D27").location, "OT Store Room")
        self.assertEqual(Equipment.objects.filter(asset_number="D27").count(), 1)
        self.assertFalse(TagEvent.objects.exists())


@override_settings(STORAGES=STATIC)
class SimulatorTests(TestCase):
    def setUp(self):
        reset()
        self.staff = Staff.objects.get(username="aoife")

    def act(self, **body):
        return self.client.post("/demo/simulator/", json.dumps(body), content_type="application/json")

    def test_staff_only(self):
        self.assertEqual(self.client.get("/demo/simulator/").status_code, 302)
        self.client.force_login(Staff.objects.create_user("plain", password="x"))
        self.assertEqual(self.client.get("/demo/simulator/").status_code, 302)
        self.client.force_login(self.staff)
        r = self.client.get("/demo/simulator/")
        for label in ["D27 → Ward 7", "D27 → Return Point", "Reset demo"]:
            self.assertContains(r, label)

    def test_move_button_changes_location_not_status(self):
        self.client.force_login(self.staff)
        r = self.act(action="move", asset="D27", location="Ward 7")
        self.assertEqual(r.json()["ok"], True)
        d27 = Equipment.objects.get(asset_number="D27")
        self.assertEqual((d27.location, d27.status), ("Ward 7", Equipment.Status.AVAILABLE))
        log = d27.location_logs.first()
        self.assertEqual((log.source, log.tag.tag_type), (LocationLog.Source.MICROBIT, Tag.TagType.BLUETOOTH))
        feed = self.client.get("/dashboard/feed/").json()
        row = next(i for i in feed["items"] if i["asset_number"] == "D27")
        self.assertEqual((row["location"], row["last_seen_text"]), ("Ward 7", "just now"))

    def test_bad_actions(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.act(action="move", asset="NOPE", location="Ward 7").status_code, 400)
        self.assertEqual(self.act(action="dance").status_code, 400)

    def test_reset_button(self):
        self.client.force_login(self.staff)
        self.act(action="move", asset="D27", location="Ward 7")
        self.assertEqual(self.act(action="reset").json()["ok"], True)
        self.assertEqual(Equipment.objects.get(asset_number="D27").location, "OT Store Room")


@override_settings(STORAGES=STATIC)
class RehearsalTests(TestCase):
    """리셋 → ① 대여 → ② 자동 위치 → ③ 반납 을 3회 반복해도 결과가 같아야 한다."""

    def test_three_rehearsals(self):
        results = []
        for _ in range(3):
            reset()
            self.client.force_login(Staff.objects.get(username="aoife"))
            r = self.client.post("/item/D27/", {"action": "loan", "expected_status": "available",
                                                "mrn": pitch_demo.DEMO_MRN})
            self.assertEqual(r.status_code, 200)
            self.client.post("/item/D27/", {"action": "loan", "expected_status": "available",
                                            "mrn": pitch_demo.DEMO_MRN})  # 두 번 눌러도
            self.assertEqual(Loan.objects.filter(equipment__asset_number="D27").count(), 1)
            self.client.post("/demo/simulator/", json.dumps({"action": "move", "asset": "D27", "location": "Ward 7"}),
                             content_type="application/json")
            self.client.post("/item/D27/", {"action": "return", "expected_status": "on_loan"})
            feed = self.client.get("/dashboard/feed/").json()
            d27 = next(i for i in feed["items"] if i["asset_number"] == "D27")
            results.append((d27["status"], feed["next_patient"]["mrn"], feed["next_patient"]["priority"],
                            Loan.objects.filter(equipment__asset_number="D27").count()))
        self.assertEqual(len(set(results)), 1, results)
        self.assertEqual(results[0][0], "available")
