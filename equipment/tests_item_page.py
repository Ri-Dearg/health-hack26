from django.test import TestCase, override_settings

from accounts.models import Staff
from equipment import services
from equipment.models import Category, Equipment, Loan, LocationLog, Tag, WaitlistEntry


@override_settings(STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class ItemPageTests(TestCase):
    def setUp(self):
        self.chairs = Category.objects.create(name="Manual wheelchair")
        self.item = Equipment.objects.create(asset_number="D27", category=self.chairs, location="OT Store Room")
        Tag.objects.create(uid="NFC-D27", tag_type=Tag.TagType.NFC, equipment=self.item)
        self.client.force_login(Staff.objects.create_user("ot", password="x", is_staff=True))

    def issue(self, mrn="4100273"):
        return self.client.post("/item/D27/", {"action": "loan", "expected_status": "available", "mrn": mrn})

    def ret(self):
        return self.client.post("/item/D27/", {"action": "return", "expected_status": "on_loan"})

    def test_available_shows_only_the_issue_flow(self):
        r = self.client.get("/item/D27/")
        self.assertContains(r, 'value="loan"', count=1)
        self.assertNotContains(r, 'value="return"')
        self.assertContains(r, 'inputmode="numeric"')

    def test_on_loan_shows_only_return(self):
        services.issue_item(self.item, mrn="4100273")
        r = self.client.get("/item/D27/")
        self.assertContains(r, 'value="return"', count=1)
        self.assertNotContains(r, 'value="loan"')
        self.assertNotContains(r, 'name="mrn"')

    def test_opening_the_page_changes_nothing(self):
        self.client.get("/item/D27/")
        self.client.get("/item/NFC-D27/")
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Equipment.Status.AVAILABLE)
        self.assertFalse(Loan.objects.exists())

    def test_issue_shows_one_sentence_and_twice_makes_one_loan(self):
        r = self.issue()
        self.assertContains(r, "Issued to MRN 4100273")
        self.assertContains(r, "Due ")
        r = self.issue()  # 두 번 누르기 / 새로고침
        self.assertEqual(Loan.objects.count(), 1)
        self.assertContains(r, "Already on loan to MRN 4100273")

    def test_return_records_return_point_and_shows_next_patient(self):
        WaitlistEntry.objects.create(category=self.chairs, patient=services.get_patient("4100388"),
                                     priority=WaitlistEntry.Priority.URGENT)
        WaitlistEntry.objects.create(category=self.chairs, patient=services.get_patient("4100512"),
                                     priority=WaitlistEntry.Priority.DISCHARGE_CRITICAL)
        self.issue()
        r = self.ret()
        self.item.refresh_from_db()
        self.assertEqual((self.item.status, self.item.location), (Equipment.Status.AVAILABLE, "Return Point"))
        log = self.item.location_logs.first()
        self.assertEqual((log.event, log.location, log.source), (
            LocationLog.Event.SEEN, "Return Point", LocationLog.Source.PHONE))
        self.assertContains(r, "Next patient: MRN 4100512 (Discharge-critical)")
        self.assertNotContains(r, 'value="loan"')  # 완료 화면에서는 다음 동작 버튼을 바로 내밀지 않는다

        logs = LocationLog.objects.count()
        r = self.ret()  # 두 번 누르기
        self.assertContains(r, "Already returned")
        self.assertEqual(LocationLog.objects.count(), logs)

    def test_return_with_empty_waitlist(self):
        self.issue()
        r = self.ret()
        self.assertContains(r, "No one is waiting")
