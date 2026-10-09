"""Excel round trip for the Acute Equipment tracker, for the changeover period.

Export: a Read me tab, one tab per category (the tracker's own layout) and a Waitlist tab.
Import: reads our export or TUH's original tracker. Each exported row carries a hidden
fingerprint of what it said at export, so a re-import can tell who changed what:

    unchanged in Excel                  -> skipped (the app stays the source of truth)
    changed in Excel only               -> applied
    changed in Excel and in the app     -> conflict: listed for a person, nothing applied
    no fingerprint (TUH's original file
    or a row added by hand)             -> applied

Only columns present in the sheet are applied, so a tracker without a "Pool" column doesn't
blank the pool. Patient names are never imported: a name column is ignored with a warning.
"""

import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.utils import timezone
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from accounts.models import Staff

from . import services
from .models import Category, Equipment, LocationLog, WaitlistEntry

S = Equipment.Status

# (key, header, width). The order is the export's column order.
ITEM_COLUMNS = [
    ("asset_number", "Asset number", 14),
    ("category", "Category", 30),
    ("description", "Description", 24),
    ("barcode", "Barcode", 16),
    ("serial_number", "Serial number", 16),
    ("pool", "Pool", 16),
    ("status", "Status", 26),
    ("location", "Location", 18),
    ("on_loan", "On loan", 9),
    ("mrn", "Patient MRN", 14),
    ("ward", "Ward", 12),
    ("bed", "Bed", 6),
    ("issued_by", "Issued by", 18),
    ("issued", "Date issued", 12),
    ("due", "Due back", 12),
    ("short_term", "Short-term loan", 10),
    ("purchase_date", "Purchase date", 13),
    ("last_service", "Last service", 13),
    ("notes", "Notes", 30),
]
WAITLIST_COLUMNS = [
    ("mrn", "Patient MRN", 14),
    ("category", "Category", 30),
    ("ward", "Ward", 12),
    ("priority", "Priority", 20),
    ("requested", "Requested", 12),
    ("wl_status", "Status", 12),
]
FINGERPRINT_HEADER = "Row check (do not edit)"

# Header spellings we accept, after lower-casing and stripping punctuation. Extend when we see the real tracker.
ITEM_ALIASES = {
    "asset_number": ["asset number", "asset no", "asset", "asset id", "asset tag"],
    "category": ["category", "equipment category", "type"],
    "description": ["description", "item", "equipment", "make model", "model"],
    "barcode": ["barcode", "bar code"],
    "serial_number": ["serial number", "serial no", "serial", "s n", "sn"],
    "pool": ["pool"],
    "status": ["status", "state"],
    "location": ["location", "current location", "where"],
    "on_loan": ["on loan", "loaned", "on loan y n"],
    "mrn": ["patient mrn", "mrn", "medical record number", "medical records number", "mrn number", "hospital number"],
    "ward": ["ward"],
    "bed": ["bed", "bed number", "bed no"],
    "issued_by": ["issued by", "therapist", "prescribing ot", "ot", "prescribing therapist"],
    "issued": ["date issued", "issue date", "issued", "date of issue", "date out"],
    "due": ["due back", "due date", "return by"],
    "short_term": ["short term loan", "short term", "discharged with patient", "home loan"],
    "purchase_date": ["purchase date", "date purchased", "purchased"],
    "last_service": ["last service", "service date", "last serviced", "service information", "service info", "service"],
    "notes": ["notes", "comments", "comment"],
    "fingerprint": [FINGERPRINT_HEADER.lower().replace("(", "").replace(")", "")],
}
WAITLIST_ALIASES = {
    "mrn": ITEM_ALIASES["mrn"],
    "category": ITEM_ALIASES["category"] + ["equipment required", "equipment needed", "item required"],
    "ward": ["ward"],
    "priority": ["priority", "clinical priority", "clinical need"],
    "requested": ["requested", "date requested", "date added", "date"],
    "wl_status": ["status"],
}
# Never stored, whatever the spreadsheet says.
NAME_HEADERS = {"patient name", "name", "patient", "first name", "forename", "surname", "last name", "full name"}
SKIP_SHEETS = {"read me", "readme", "instructions", "legend", "key", "summary"}
WAITLIST_SHEETS = {"waitlist", "wait list", "waiting list"}

# Row tints by status. Words go in the Status column too, so nobody relies on colour.
STATUS_FILLS = {
    S.AVAILABLE: "FFE2EFDA",
    S.ON_LOAN: "FFDDEBF7",
    S.AWAITING_REPAIR: "FFFCE4D6",
    S.MISSING: "FFF8CBAD",
    S.ALLOCATED: "FFFFF2CC",
}
# Fill colour -> status, used only when a sheet has no Status column (TUH's tracker is colour-coded).
# Guesses until we see the real file: add its exact ARGB codes here.
FILL_TO_STATUS = {fill: status for status, fill in STATUS_FILLS.items()} | {
    "FFFF0000": S.MISSING,          # red
    "FFFFC000": S.AWAITING_REPAIR,  # amber
    "FFFFFF00": S.ON_LOAN,          # yellow (short-term loan in the brief's colour key, assumed)
}


@dataclass
class ImportReport:
    dry_run: bool
    created: list = field(default_factory=list)
    updated: list = field(default_factory=list)      # (asset number, [field names])
    unchanged: int = 0
    conflicts: list = field(default_factory=list)    # (asset number, reason)
    warnings: list = field(default_factory=list)
    offers: list = field(default_factory=list)       # returned items with someone waiting
    waitlist_created: int = 0
    waitlist_updated: int = 0

    @property
    def changes(self):
        return len(self.created) + len(self.updated) + self.waitlist_created + self.waitlist_updated


# ---------- normalising values so Excel and the database compare equal ----------

def _norm_header(value):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # barcodes typed into Excel come back as floats
    if isinstance(value, (datetime, date)):
        return _date(value)
    return str(value).strip()


def _date(value):
    """Return an ISO date string, or "" if blank. Raises ValueError if it can't be read."""
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):  # Irish day-first
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"can't read date '{text}'")


def _yes(value):
    return "yes" if _text(value).lower() in {"y", "yes", "true", "1", "x", "✓", "✔"} else "no"


def _status(value):
    """Map free text to a status. Returns (status, short_term) or (None, False) if unknown."""
    text = _text(value).lower()
    if not text:
        return None, False
    for status in S:
        if text in (status.value, status.label.lower()):
            return status.value, False
    if "short" in text or "home" in text:
        return S.ON_LOAN.value, True
    for words, status in [
        (("repair", "service", "faulty", "broken"), S.AWAITING_REPAIR),
        (("missing", "lost"), S.MISSING),
        (("allocated", "reserved"), S.ALLOCATED),
        (("loan", "issued", "out"), S.ON_LOAN),
        (("available", "in store", "in stock", "free"), S.AVAILABLE),
    ]:
        if any(w in text for w in words):
            return status.value, False
    return None, False


def _pool(value):
    return Equipment.Pool.WINTER.value if "winter" in _text(value).lower() else Equipment.Pool.OT.value


def _priority(value):
    text = _text(value).lower()
    if "discharge" in text or "critical" in text or text == "1":
        return WaitlistEntry.Priority.DISCHARGE_CRITICAL
    if "urgent" in text or text == "2":
        return WaitlistEntry.Priority.URGENT
    return WaitlistEntry.Priority.ROUTINE


def _local_dt(iso, at=time(12)):
    return timezone.make_aware(datetime.combine(date.fromisoformat(iso), at))


def db_row(item):
    """The item as canonical strings: exactly what an untouched export row parses back to."""
    loan = item.open_loan
    return {
        "asset_number": item.asset_number,
        "category": item.category.name,
        "description": item.description,
        "barcode": item.barcode,
        "serial_number": item.serial_number,
        "pool": item.pool,
        "status": item.status,
        "location": item.location,
        "on_loan": "yes" if loan else "no",
        "mrn": loan.patient.mrn if loan and loan.patient else "",
        "ward": loan.ward if loan else "",
        "bed": loan.bed if loan else "",
        "issued_by": str(loan.issued_by) if loan and loan.issued_by else "",
        "issued": timezone.localdate(loan.issued_at).isoformat() if loan else "",
        "due": timezone.localdate(loan.due_at).isoformat() if loan else "",
        "short_term": "yes" if loan and loan.short_term_loan else "no",
        "purchase_date": _date(item.purchase_date),
        "last_service": _date(item.last_service_date),
        "notes": item.notes,
    }


def fingerprint(row):
    canonical = json.dumps([row.get(key, "") for key, _, _ in ITEM_COLUMNS], ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ---------- export ----------

def _sheet_title(name, used):
    title = re.sub(r"[\[\]:*?/\\]", "-", name)[:31]
    base, n = title, 2
    while title.lower() in used:
        title = f"{base[:28]} {n}"
        n += 1
    used.add(title.lower())
    return title


def _write_table(ws, columns, rows, fills=None):
    bold = Font(bold=True)
    for col, (_, header, width) in enumerate(columns, start=1):
        ws.cell(row=1, column=col, value=header).font = bold
        ws.column_dimensions[get_column_letter(col)].width = width
    for r, values in enumerate(rows, start=2):
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=r, column=col, value=value)
            if fills and fills[r - 2]:
                cell.fill = PatternFill("solid", fgColor=fills[r - 2])
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions


def build_workbook(pool=None):
    wb = Workbook()
    readme = wb.active
    readme.title = "Read me"
    lines = [
        ("OT equipment tracker export", None),
        ("Exported", timezone.localtime().strftime("%d/%m/%Y %H:%M")),
        ("", None),
        ("The app is the source of truth. This file is a snapshot.", None),
        ("You can edit a row and import the file back. Rows you didn't change are skipped.", None),
        ("If the app also changed a row since this export, the import lists it as a conflict and changes nothing.", None),
        ("Patient names are never stored: record the MRN only.", None),
        ("Don't edit or delete the hidden 'Row check' column.", None),
        ("", None),
        ("Status key (the Status column always says it in words)", None),
    ]
    for r, (a, b) in enumerate(lines, start=1):
        readme.cell(row=r, column=1, value=a)
        if b:
            readme.cell(row=r, column=2, value=b)
    readme["A1"].font = Font(bold=True, size=14)
    for r, status in enumerate(S, start=len(lines) + 1):
        readme.cell(row=r, column=1, value=status.label).fill = PatternFill("solid", fgColor=STATUS_FILLS[status])
    readme.column_dimensions["A"].width = 100

    items = Equipment.objects.select_related("category").prefetch_related("loans__patient", "loans__issued_by")
    if pool:
        items = items.filter(pool=pool)
    used = {"read me", "waitlist"}
    columns = ITEM_COLUMNS + [("fingerprint", FINGERPRINT_HEADER, 4)]
    for category in Category.objects.all():
        cat_items = [i for i in items if i.category_id == category.pk]
        if not cat_items:
            continue
        ws = wb.create_sheet(_sheet_title(category.name, used))
        rows, fills = [], []
        for item in cat_items:
            row = db_row(item)
            display = row | {
                "pool": item.get_pool_display(),
                "status": item.get_status_display(),
                "on_loan": row["on_loan"].capitalize(),
                "short_term": row["short_term"].capitalize(),
            }
            for key in ("issued", "due", "purchase_date", "last_service"):
                display[key] = date.fromisoformat(row[key]) if row[key] else None
            rows.append([display[key] for key, _, _ in ITEM_COLUMNS] + [fingerprint(row)])
            fills.append(STATUS_FILLS[item.status])
        _write_table(ws, columns, rows, fills)
        for r in range(2, len(rows) + 2):
            for key in ("issued", "due", "purchase_date", "last_service"):
                col = [k for k, _, _ in ITEM_COLUMNS].index(key) + 1
                ws.cell(row=r, column=col).number_format = "DD/MM/YYYY"
        ws.column_dimensions[get_column_letter(len(columns))].hidden = True

    ws = wb.create_sheet("Waitlist")
    entries = WaitlistEntry.objects.exclude(status=WaitlistEntry.Status.CANCELLED).select_related("patient", "category")
    _write_table(ws, WAITLIST_COLUMNS, [
        [e.patient.mrn, e.category.name, e.ward, e.get_priority_display(),
         timezone.localdate(e.requested_at), e.get_status_display()]
        for e in entries
    ])
    for r in range(2, entries.count() + 2):
        ws.cell(row=r, column=5).number_format = "DD/MM/YYYY"
    return wb


def export_bytes(pool=None):
    buffer = io.BytesIO()
    build_workbook(pool).save(buffer)
    return buffer.getvalue()


# ---------- import ----------

def _find_header(ws, aliases, report):
    """Find the header row (within the first 10 rows) and map column index -> key."""
    lookup = {alias: key for key, names in aliases.items() for alias in names}
    first_key = next(iter(aliases))
    for r, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=True), start=1):
        headers = [_norm_header(v) for v in row]
        if not any(lookup.get(h) == first_key for h in headers):
            continue
        mapping, ignored = {}, []
        for i, h in enumerate(headers):
            if h in NAME_HEADERS:
                report.warnings.append(f"'{ws.title}': ignored the '{row[i]}' column. Patient names are never stored.")
            elif h in lookup and lookup[h] not in mapping.values():
                mapping[i] = lookup[h]
            elif h:
                ignored.append(str(row[i]))
        if ignored:
            report.warnings.append(f"'{ws.title}': columns not recognised, so not imported: {', '.join(ignored)}.")
        return r, mapping
    return None, {}


def _parse_item_row(values, mapping, ws, cell_fill, report):
    raw = {key: values[i] if i < len(values) else None for i, key in mapping.items()}
    row, short_term_from_status = {}, False
    for key, value in raw.items():
        try:
            if key in ("issued", "due", "purchase_date", "last_service"):
                row[key] = _date(value)
            elif key in ("on_loan", "short_term"):
                row[key] = _yes(value)
            elif key == "pool":
                row[key] = _pool(value)
            elif key == "status":
                status, short_term_from_status = _status(value)
                if status:
                    row[key] = status
                elif _text(value):
                    report.warnings.append(f"'{ws.title}' {_text(raw.get('asset_number'))}: status '{value}' not recognised, ignored.")
            else:
                row[key] = _text(value)
        except ValueError as exc:
            report.warnings.append(f"'{ws.title}' {_text(raw.get('asset_number'))}: {exc}, ignored.")
    if "status" not in mapping.values() and cell_fill in FILL_TO_STATUS:
        row["status"] = FILL_TO_STATUS[cell_fill].value
    if short_term_from_status and "short_term" not in row:
        row["short_term"] = "yes"
    if row.get("mrn"):
        row["mrn"] = row["mrn"].upper()
    return row


def _apply_item(row, sheet_title, default_pool, report):
    asset = row["asset_number"]
    stored_fp = row.pop("fingerprint", "")
    item = Equipment.objects.filter(asset_number=asset).select_related("category").first()

    if item:
        current = db_row(item)
        file_view = current | row  # columns missing from the sheet count as unchanged
        if file_view == current:
            report.unchanged += 1
            return
        if stored_fp:
            if fingerprint(file_view) == stored_fp:
                report.unchanged += 1  # untouched in Excel; the app has moved on, and the app wins
                return
            if fingerprint(current) != stored_fp:
                changed = [k for k in row if row[k] != current.get(k)]
                report.conflicts.append((asset, f"changed in both Excel and the app since export ({', '.join(changed)})"))
                return

    name = row.get("category") or sheet_title
    category = Category.objects.filter(name__iexact=name).first()
    if not category:
        category = Category.objects.create(name=name)
        report.warnings.append(f"New category created: '{name}'.")

    changed = []
    if not item:
        item = Equipment(asset_number=asset, category=category, pool=row.get("pool", default_pool))
        before = {}
    else:
        before = db_row(item)
    if item.category_id != category.pk:
        item.category = category
    for key, attr in [("description", "description"), ("barcode", "barcode"), ("serial_number", "serial_number"),
                      ("pool", "pool"), ("notes", "notes")]:
        if key in row:
            setattr(item, attr, row[key])
    for key, attr in [("purchase_date", "purchase_date"), ("last_service", "last_service_date")]:
        if key in row:
            setattr(item, attr, date.fromisoformat(row[key]) if row[key] else None)
    item.save()

    # Loan state: the Status column wins; otherwise the On loan column.
    if "status" in row:
        wants_loan = row["status"] == S.ON_LOAN
    elif "on_loan" in row:
        wants_loan = row["on_loan"] == "yes"
    else:
        wants_loan = None
    loan = item.open_loan
    staff = _match_staff(row.get("issued_by"), asset, report)
    if wants_loan and not loan:
        issued_at = _local_dt(row["issued"]) if row.get("issued") else None
        services.issue_item(
            item, staff=staff, mrn=row.get("mrn", ""), ward=row.get("ward", ""), bed=row.get("bed", ""),
            source=LocationLog.Source.SPREADSHEET, issued_at=issued_at,
            due_at=_local_dt(row["due"]) if row.get("due") else None,
            short_term_loan=row.get("short_term") == "yes",
        )
    elif wants_loan and loan:
        if "mrn" in row:
            loan.patient = services.get_patient(row["mrn"]) if row["mrn"] else None
        for key in ("ward", "bed"):
            if key in row:
                setattr(loan, key, row[key])
        if row.get("due"):
            loan.due_at = _local_dt(row["due"])
        if "short_term" in row:
            loan.short_term_loan = row["short_term"] == "yes"
        if staff:
            loan.issued_by = staff
        loan.save()
    elif wants_loan is False and loan:
        offer = services.return_item(item, source=LocationLog.Source.SPREADSHEET)
        if offer:
            report.offers.append(f"{asset} is back. Next on the waitlist: {offer}.")

    item.refresh_from_db()
    if "status" in row and item.status != row["status"]:
        item.status = row["status"]
    if "location" in row and row["location"] and item.location != row["location"]:
        item.location = row["location"]
        item.location_updated_at = timezone.now()
    item.save()

    after = db_row(item)
    if not before:
        report.created.append(asset)
    else:
        changed = [k for k in after if after[k] != before.get(k)]
        if changed:
            report.updated.append((asset, changed))
        else:
            report.unchanged += 1


def _match_staff(text, asset, report):
    if not text:
        return None
    text = text.strip()
    for staff in Staff.objects.filter(is_active=True):
        if text.lower() in {staff.username.lower(), str(staff).lower(), (staff.staff_number or "").lower()}:
            return staff
    report.warnings.append(f"{asset}: 'Issued by' {text} doesn't match a staff member, so the loan needs an owner.")
    return None


def _apply_waitlist(ws, report):
    header_row, mapping = _find_header(ws, WAITLIST_ALIASES, report)
    if not header_row:
        report.warnings.append(f"'{ws.title}': no 'MRN' header found, tab skipped.")
        return
    for values in ws.iter_rows(min_row=header_row + 1, values_only=True):
        raw = {key: values[i] if i < len(values) else None for i, key in mapping.items()}
        mrn, cat_name = _text(raw.get("mrn")).upper(), _text(raw.get("category"))
        if not mrn or not cat_name:
            continue
        category = Category.objects.filter(name__iexact=cat_name).first()
        if not category:
            report.warnings.append(f"Waitlist: category '{cat_name}' not found, row for {mrn} skipped.")
            continue
        patient = services.get_patient(mrn)
        entry = WaitlistEntry.objects.filter(patient=patient, category=category).exclude(
            status=WaitlistEntry.Status.CANCELLED).first()
        fields = {}
        if "priority" in mapping.values():
            fields["priority"] = _priority(raw.get("priority"))
        if "ward" in mapping.values():
            fields["ward"] = _text(raw.get("ward"))
        if "wl_status" in mapping.values() and _text(raw.get("wl_status")):
            text = _text(raw["wl_status"]).lower()
            fields["status"] = next((s.value for s in WaitlistEntry.Status if text in (s.value, s.label.lower())),
                                    WaitlistEntry.Status.WAITING.value)
        if raw.get("requested"):
            try:
                fields["requested_at"] = _local_dt(_date(raw["requested"]), time(9))
            except ValueError as exc:
                report.warnings.append(f"Waitlist {mrn}: {exc}, ignored.")
        if not entry:
            WaitlistEntry.objects.create(patient=patient, category=category, **fields)
            report.waitlist_created += 1
        else:
            if entry.requested_at and "requested_at" in fields and \
                    timezone.localdate(entry.requested_at) == fields["requested_at"].date():
                del fields["requested_at"]  # same day: keep the precise time
            diff = {k: v for k, v in fields.items() if getattr(entry, k) != v}
            if diff:
                for k, v in diff.items():
                    setattr(entry, k, v)
                entry.save()
                report.waitlist_updated += 1


def import_workbook(file, *, dry_run=True, default_pool=Equipment.Pool.OT):
    """Read an .xlsx (path or file object). With dry_run, report what would change and change nothing."""
    report = ImportReport(dry_run=dry_run)
    wb = load_workbook(file, data_only=True)
    with transaction.atomic():
        for ws in wb.worksheets:
            title = _norm_header(ws.title)
            if title in SKIP_SHEETS:
                continue
            if title in WAITLIST_SHEETS:
                _apply_waitlist(ws, report)
                continue
            header_row, mapping = _find_header(ws, ITEM_ALIASES, report)
            if not header_row:
                report.warnings.append(f"'{ws.title}': no 'Asset number' header found, tab skipped.")
                continue
            seen = set()
            for row_cells in ws.iter_rows(min_row=header_row + 1):
                values = [c.value for c in row_cells]
                fill = row_cells[0].fill.fgColor.rgb if row_cells and row_cells[0].fill.fill_type == "solid" else None
                row = _parse_item_row(values, mapping, ws, fill if isinstance(fill, str) else None, report)
                if not row.get("asset_number"):
                    continue
                if row["asset_number"] in seen:
                    report.warnings.append(f"'{ws.title}': asset {row['asset_number']} appears twice, second row skipped.")
                    continue
                seen.add(row["asset_number"])
                _apply_item(row, ws.title, default_pool, report)
        if dry_run:
            transaction.set_rollback(True)
    return report
