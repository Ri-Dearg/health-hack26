import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Staff
from equipment.events import apply_action, handle_tag_read
from equipment.models import Category, Equipment, Tag, TagEvent

O = TagEvent.Outcome


class TagReadTests(TestCase):
    def setUp(self):
        self.item = Equipment.objects.create(asset_number="D02", category=Category.objects.create(name="Chair"))
        Tag.objects.create(uid="MB-1", tag_type=Tag.TagType.BLUETOOTH, equipment=self.item)

    def test_unknown_tag_is_flagged_and_changes_nothing(self):
        r = handle_tag_read("NOPE")
        self.assertEqual(r.outcome, O.UNKNOWN_TAG)
        self.assertTrue(r.event.needs_review)

    def test_disabled_tag_counts_as_unknown(self):
        Tag.objects.filter(uid="MB-1").update(is_active=False)
        self.assertEqual(handle_tag_read("MB-1").outcome, O.UNKNOWN_TAG)

    def test_available_then_on_loan_toggles_with_repeat_protection(self):
        now = timezone.now()
        self.assertEqual(handle_tag_read("MB-1", read_at=now).outcome, O.LOAN)
        self.assertEqual(handle_tag_read("MB-1", read_at=now + timedelta(seconds=5)).outcome, O.REPEAT)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Equipment.Status.ON_LOAN)
        self.assertEqual(handle_tag_read("MB-1", read_at=now + timedelta(seconds=120)).outcome, O.RETURN)

    def test_other_statuses_need_review(self):
        Equipment.objects.filter(pk=self.item.pk).update(status=Equipment.Status.MISSING)
        r = handle_tag_read("MB-1")
        self.assertEqual((r.outcome, r.event.needs_review), (O.NEEDS_REVIEW, True))

    def test_stale_page_gets_already_done_or_conflict(self):
        apply_action(self.item, "loan", expected_status="available")
        self.assertEqual(apply_action(self.item, "loan", expected_status="available").outcome, O.ALREADY_DONE)
        Equipment.objects.filter(pk=self.item.pk).update(status=Equipment.Status.MISSING)
        self.assertEqual(apply_action(self.item, "return", expected_status="on_loan").outcome, O.CONFLICT)


class EndpointTests(TestCase):
    def setUp(self):
        self.item = Equipment.objects.create(asset_number="E98", category=Category.objects.create(name="Cushion"))
        Tag.objects.create(uid="NFC-1", tag_type=Tag.TagType.NFC, equipment=self.item)

    def post(self, body, **headers):
        return self.client.post("/api/tag-read/", json.dumps(body), content_type="application/json", **headers)

    def test_api_opens_loan(self):
        r = self.post({"tag_id": "NFC-1", "reader_id": "store-door"})
        self.assertEqual((r.status_code, r.json()["outcome"], r.json()["asset_number"]), (200, "loan", "E98"))
        self.assertEqual(self.post({}).status_code, 400)

    @override_settings(DEVICE_TOKEN="secret")
    def test_api_checks_device_token(self):
        self.assertEqual(self.post({"tag_id": "NFC-1"}).status_code, 403)
        self.assertEqual(self.post({"tag_id": "NFC-1"}, HTTP_X_DEVICE_TOKEN="secret").status_code, 200)

    @override_settings(STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
    def test_item_page_loans_as_logged_in_staff(self):
        self.assertEqual(self.client.get("/item/NFC-1/").status_code, 302)  # login first
        staff = Staff.objects.create_user("ot", password="x", is_staff=True)
        self.client.force_login(staff)
        self.assertContains(self.client.get("/item/NFC-1/"), "Loan to this patient")
        r = self.client.post("/item/NFC-1/", {"action": "loan", "expected_status": "available", "mrn": "m1", "ward": "3C"})
        self.assertContains(r, "on loan, due back")
        self.assertEqual(self.item.open_loan.issued_by, staff)
        self.assertEqual(self.item.open_loan.patient.mrn, "M1")
