from datetime import timedelta

from django.core import mail
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Staff
from equipment import services
from equipment.models import Category, Equipment, Loan, WaitlistEntry


class LoanRulesTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="Sliding board")
        self.item = Equipment.objects.create(asset_number="SLB-001", category=self.category)
        self.ot = Staff.objects.create_user("ot", email="ot@example.com")

    def test_issue_defaults_to_30_days_and_marks_on_loan(self):
        loan = services.issue_item(self.item, staff=self.ot, mrn="mrn1", ward="Ward 1A")
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Equipment.Status.ON_LOAN)
        self.assertEqual((loan.due_at - loan.issued_at).days, 30)
        self.assertEqual(loan.patient.mrn, "MRN1")

    def test_issue_without_tap_needs_owner(self):
        self.assertTrue(services.issue_item(self.item).needs_owner)

    def test_only_one_open_loan_per_item(self):
        services.issue_item(self.item)
        with self.assertRaises(IntegrityError):
            Loan.objects.create(equipment=self.item)

    def test_return_closes_loan_and_offers_top_of_waitlist(self):
        services.issue_item(self.item, staff=self.ot)
        routine = WaitlistEntry.objects.create(
            category=self.category, patient=services.get_patient("A"), priority=WaitlistEntry.Priority.ROUTINE,
            requested_at=timezone.now() - timedelta(days=5),
        )
        critical = WaitlistEntry.objects.create(
            category=self.category, patient=services.get_patient("B"), priority=WaitlistEntry.Priority.DISCHARGE_CRITICAL,
        )
        self.assertEqual(services.return_item(self.item), critical)
        self.assertFalse(Loan.objects.open().exists())
        self.assertNotEqual(routine, critical)

    def test_overdue_notice_sent_once(self):
        loan = services.issue_item(self.item, staff=self.ot)
        Loan.objects.filter(pk=loan.pk).update(due_at=timezone.now() - timedelta(days=1))
        self.assertEqual(services.send_overdue_notices(), 1)
        self.assertEqual(services.send_overdue_notices(), 0)
        self.assertEqual(mail.outbox[0].to, ["ot@example.com"])


class CardTapTests(TestCase):
    def test_card_lookup_uses_hash_only(self):
        staff = Staff.objects.create_user("ot")
        staff.set_card_id("04:a1:b2:c3")
        staff.save()
        self.assertNotIn("A1", staff.card_id_hash)
        self.assertEqual(Staff.from_card_tap("04A1B2C3"), staff)


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class AdminSmokeTests(TestCase):
    def test_every_admin_page_loads_with_demo_data(self):
        from django.core.management import call_command
        call_command("seed_demo", stdout=open("/dev/null", "w"))
        self.client.force_login(Staff.objects.create_superuser("admin", password="x"))
        for model in ["equipment", "tag", "patient", "loan", "waitlistentry", "locationlog", "category"]:
            for url in [f"/admin/equipment/{model}/", f"/admin/equipment/{model}/add/"]:
                self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get("/admin/equipment/loan/?state=overdue").status_code, 200)
        self.assertEqual(self.client.get("/admin/accounts/staff/").status_code, 200)
        item = Equipment.objects.first()
        self.assertEqual(self.client.get(f"/admin/equipment/equipment/{item.pk}/change/").status_code, 200)
        loan = Loan.objects.first()
        self.assertContains(self.client.get(f"/admin/equipment/loan/{loan.pk}/change/"), "DeviceUseStatement")

        export = self.client.get("/admin/equipment/equipment/export/")
        self.assertEqual(export.status_code, 200)
        import io
        upload = io.BytesIO(export.content)
        upload.name = "tracker.xlsx"
        preview = self.client.post("/admin/equipment/equipment/import/", {"file": upload, "default_pool": "", "preview": "1"})
        self.assertContains(preview, "Preview: nothing has changed yet")
        self.assertContains(preview, "Unchanged: 20")


class SynergyTests(TestCase):
    def setUp(self):
        category = Category.objects.create(name="Headrest")
        self.item = Equipment.objects.create(asset_number="HDR-001", category=category)
        self.loan = services.issue_item(self.item, mrn="MRN5", ward="Ward 1A")

    def test_fhir_resource_has_identifiers_only(self):
        from equipment.synergy import loan_to_fhir
        resource = loan_to_fhir(self.loan)
        self.assertEqual(resource["resourceType"], "DeviceUseStatement")
        self.assertEqual(resource["subject"]["identifier"]["value"], "MRN5")
        self.assertEqual(resource["status"], "active")

    def test_discharge_turns_open_loans_into_short_term_loans(self):
        from equipment.synergy import handle_adt
        handle_adt("A03", "mrn5")
        self.loan.refresh_from_db()
        self.item.refresh_from_db()
        self.assertTrue(self.loan.short_term_loan)
        self.assertEqual(self.item.location, "Home with patient")

    def test_transfer_moves_loan_ward(self):
        from equipment.synergy import handle_adt
        handle_adt("A02", "MRN5", ward="Ward 3C", bed="7")
        self.loan.refresh_from_db()
        self.assertEqual((self.loan.ward, self.loan.bed), ("Ward 3C", "7"))


class EquipmentFormTests(TestCase):
    def setUp(self):
        from equipment.models import Pool
        self.data = {
            "asset_number": "N1", "category": Category.objects.create(name="Chair").pk,
            "pool": Pool.objects.get(name="OT Department").pk, "status": "available", "location": "OT store",
            "location_updated_at": "2026-10-09 12:00", "barcode": "",
        }

    def test_new_item_needs_a_barcode(self):
        from equipment.admin import EquipmentForm
        self.assertIn("barcode", EquipmentForm(data=self.data).errors)
        self.assertTrue(EquipmentForm(data=self.data | {"barcode": "OT1"}).is_valid())

    def test_existing_item_without_barcode_can_still_be_edited_but_not_duplicated(self):
        from equipment.admin import EquipmentForm
        item = Equipment.objects.create(asset_number="N1", category_id=self.data["category"])
        self.assertTrue(EquipmentForm(data=self.data, instance=item).is_valid())
        Equipment.objects.create(asset_number="N2", category_id=self.data["category"], barcode="OT1")
        self.assertIn("barcode", EquipmentForm(data=self.data | {"barcode": "OT1"}, instance=item).errors)


class SeedDemoTests(TestCase):
    def test_if_empty_is_safe_to_repeat_and_demo_staff_cannot_log_in_off_debug(self):
        from django.core.management import call_command
        call_command("seed_demo", stdout=open("/dev/null", "w"))
        call_command("seed_demo", if_empty=True, stdout=open("/dev/null", "w"))  # no error, nothing duplicated
        self.assertEqual(Equipment.objects.count(), 20)
        self.assertFalse(Staff.objects.get(username="aoife").has_usable_password())  # tests run with DEBUG off
