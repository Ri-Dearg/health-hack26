import io
from django.test import TestCase
from django.utils import timezone
from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill

from equipment import services
from equipment.models import Category, Equipment, Loan, Patient, WaitlistEntry
from equipment.spreadsheet import export_bytes, import_workbook


def edit(xlsx, sheet, asset, header, value):
    wb = load_workbook(io.BytesIO(xlsx))
    ws = wb[sheet]
    headers = [c.value for c in ws[1]]
    for row in ws.iter_rows(min_row=2):
        if row[0].value == asset:
            row[headers.index(header)].value = value
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


class RoundTripTests(TestCase):
    def setUp(self):
        self.boards = Category.objects.create(name="Sliding board")
        self.a = Equipment.objects.create(asset_number="SLB-001", category=self.boards, barcode="5012345")
        self.b = Equipment.objects.create(asset_number="SLB-002", category=self.boards)
        services.issue_item(self.b, mrn="MRN9", ward="Ward 2B")
        WaitlistEntry.objects.create(category=self.boards, patient=services.get_patient("MRN7"),
                                     priority=WaitlistEntry.Priority.URGENT)

    def test_untouched_export_imports_with_no_changes(self):
        report = import_workbook(io.BytesIO(export_bytes()), dry_run=False)
        self.assertEqual(report.changes, 0, report)
        self.assertEqual(report.conflicts, [])
        self.assertEqual(report.unchanged, 2)

    def test_edit_in_excel_is_applied(self):
        xlsx = edit(export_bytes(), "Sliding board", "SLB-001", "Notes", "Strap frayed")
        report = import_workbook(io.BytesIO(xlsx), dry_run=False)
        self.assertEqual(report.updated, [("SLB-001", ["notes"])])
        self.a.refresh_from_db()
        self.assertEqual(self.a.notes, "Strap frayed")

    def test_dry_run_changes_nothing(self):
        xlsx = edit(export_bytes(), "Sliding board", "SLB-001", "Notes", "Strap frayed")
        self.assertEqual(len(import_workbook(io.BytesIO(xlsx), dry_run=True).updated), 1)
        self.a.refresh_from_db()
        self.assertEqual(self.a.notes, "")

    def test_change_in_app_only_is_kept(self):
        xlsx = export_bytes()
        Equipment.objects.filter(pk=self.a.pk).update(status=Equipment.Status.MISSING)
        report = import_workbook(io.BytesIO(xlsx), dry_run=False)
        self.assertEqual(report.changes, 0)
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, Equipment.Status.MISSING)

    def test_change_in_both_is_a_conflict_and_not_applied(self):
        xlsx = edit(export_bytes(), "Sliding board", "SLB-001", "Notes", "Excel edit")
        Equipment.objects.filter(pk=self.a.pk).update(notes="App edit")
        report = import_workbook(io.BytesIO(xlsx), dry_run=False)
        self.assertEqual([c[0] for c in report.conflicts], ["SLB-001"])
        self.a.refresh_from_db()
        self.assertEqual(self.a.notes, "App edit")

    def test_marking_returned_in_excel_closes_loan_and_offers_waitlist(self):
        xlsx = edit(export_bytes(), "Sliding board", "SLB-002", "Status", "Available")
        xlsx = edit(xlsx, "Sliding board", "SLB-002", "On loan", "No")
        report = import_workbook(io.BytesIO(xlsx), dry_run=False)
        self.assertFalse(Loan.objects.open().exists())
        self.assertIn("MRN7", report.offers[0])

    def test_waitlist_round_trip(self):
        report = import_workbook(io.BytesIO(export_bytes()), dry_run=False)
        self.assertEqual((report.waitlist_created, report.waitlist_updated), (0, 0))


class OriginalTrackerTests(TestCase):
    """A made-up file shaped like the brief's description of TUH's tracker."""

    def make_tracker(self):
        wb = Workbook()
        ws = wb.active
        ws.title = "Sliding boards"
        ws.append(["Asset No.", "Barcode", "Serial No", "Patient Name", "MRN", "Location", "Date Issued", "Service info"])
        ws.append(["SB-01", 5012345678900, "S1", "Invented Person", "mrn123", "Ward 3C", "05/10/2026", "01/03/2026"])
        ws.append(["SB-02", None, None, None, None, "OT store", None, None])
        for cell in ws[3]:
            cell.fill = PatternFill("solid", fgColor="FFFF0000")  # red: missing (assumed key)
        wl = wb.create_sheet("Waitlist")
        wl.append(["MRN", "Equipment required", "Ward", "Priority"])
        wl.append(["MRN555", "Sliding boards", "AMU", "Discharge critical"])
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()

    def test_first_import_of_tuh_tracker(self):
        report = import_workbook(io.BytesIO(self.make_tracker()), dry_run=False)
        self.assertEqual(sorted(report.created), ["SB-01", "SB-02"])
        self.assertTrue(any("Patient names are never stored" in w for w in report.warnings))

        sb1 = Equipment.objects.get(asset_number="SB-01")
        self.assertEqual(sb1.category.name, "Sliding boards")
        self.assertEqual(sb1.barcode, "5012345678900")
        self.assertEqual(str(sb1.last_service_date), "2026-03-01")
        loan = sb1.open_loan
        self.assertIsNone(loan, "no Status or On loan column, so no loan is guessed")
        self.assertEqual(Equipment.objects.get(asset_number="SB-02").status, Equipment.Status.MISSING)
        self.assertEqual(list(Patient.objects.values_list("mrn", flat=True)), ["MRN555"])
        self.assertEqual(WaitlistEntry.objects.get().priority, WaitlistEntry.Priority.DISCHARGE_CRITICAL)

    def test_on_loan_column_opens_loan_with_issue_date(self):
        wb = load_workbook(io.BytesIO(self.make_tracker()))
        ws = wb["Sliding boards"]
        ws["I1"], ws["I2"] = "On loan", "Y"
        out = io.BytesIO()
        wb.save(out)
        import_workbook(io.BytesIO(out.getvalue()), dry_run=False)
        loan = Equipment.objects.get(asset_number="SB-01").open_loan
        self.assertEqual(loan.patient.mrn, "MRN123")
        self.assertEqual(timezone.localdate(loan.issued_at).isoformat(), "2026-10-05")
        self.assertEqual(timezone.localdate(loan.due_at).isoformat(), "2026-11-04")  # 30 days, across the clock change
        self.assertTrue(loan.needs_owner)
