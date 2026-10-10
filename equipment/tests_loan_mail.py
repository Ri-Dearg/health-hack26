from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings

from accounts.models import Staff
from equipment.models import Category, Equipment, Loan

NOTIFY = "demo-notify@example.com"  # 테스트용 주소. 실제 수신 주소는 환경변수로만 둔다.


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEMO_NOTIFY_MRN="1003",
    DEMO_NOTIFY_EMAIL=NOTIFY,
)
class MrnSelectAndLoanMailTests(TestCase):
    def setUp(self):
        self.chairs = Category.objects.create(name="Manual wheelchair")
        self.item = Equipment.objects.create(asset_number="D27", category=self.chairs, location="OT Store Room")
        self.client.force_login(Staff.objects.create_user("ot", password="x", is_staff=True))

    def loan(self, mrn):
        return self.client.post("/item/D27/", {"action": "loan", "expected_status": "available", "mrn": mrn})

    def test_mrn_is_a_select_with_1001_to_1006(self):
        r = self.client.get("/item/D27/")
        self.assertContains(r, '<select id="mrn" name="mrn" required>')
        for n in range(1001, 1007):
            self.assertContains(r, f'<option value="{n}">{n}</option>', html=False)
        self.assertNotContains(r, 'value="1007"')
        self.assertNotContains(r, '<input id="mrn"')

    def test_loan_to_1003_sends_one_mail(self):
        self.loan("1003")
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, [NOTIFY])
        self.assertIn("D27", sent.subject)
        self.assertIn("1003", sent.subject)

    def test_loan_to_other_mrn_sends_nothing(self):
        self.loan("1002")
        self.assertEqual(Loan.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_repeating_the_loan_does_not_send_again(self):
        self.loan("1003")
        self.loan("1003")  # already on loan -> already_done, no new loan
        self.assertEqual(Loan.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_return_sends_nothing(self):
        self.loan("1003")
        mail.outbox.clear()
        self.client.post("/item/D27/", {"action": "return", "expected_status": "on_loan"})
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(DEMO_NOTIFY_EMAIL="")
    def test_no_address_configured_sends_nothing(self):
        self.loan("1003")
        self.assertEqual(len(mail.outbox), 0)

    def test_mail_failure_does_not_undo_the_loan(self):
        with mock.patch("equipment.notify.send_mail", side_effect=OSError("smtp blocked")):
            r = self.loan("1003")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Issued to MRN 1003")
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Equipment.Status.ON_LOAN)
        self.assertEqual(Loan.objects.count(), 1)
