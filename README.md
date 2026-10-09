# OT equipment tracker (hackathon prototype)

Django 5.2 LTS app for TUH Occupational Therapy's equipment loan pool (Healthcare Innovation Challenge 2026, Challenge 2). For now, the Django admin handles all the CRUD.

## Run it locally

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env            # then set SECRET_KEY to any long random string
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo          # invented data; --reset to reload
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver
```

Open http://127.0.0.1:8000/admin/. The demo staff `aoife`, `ciaran` and `niamh` (password in `seed_demo.py`) are in the "OT staff" group, so you can log in as one and see what a non-admin sees.

Tests: `.venv/bin/python manage.py test`

## Tables

| Model | What it is |
|---|---|
| `accounts.Staff` | Custom user. Admin = superuser; user = "OT staff" group (can add and change, can't delete). Staff number, plus a hashed keycard ID |
| `Category` | Nine categories, each with a default loan period (30 days) |
| `Equipment` | One physical item: asset number, barcode, serial, pool (OT or Winter Initiative), the brief's five statuses, current location |
| `Tag` (chips / tags) | Bluetooth/micro:bit, NFC, QR or barcode. An item can carry several tags, and a tag can be moved to another item |
| `Patient` | **MRN only.** No names |
| `Loan` | Who issued which item, to which MRN, on which ward, when it's due and when it came back. Only one open loan per item (enforced in the database) |
| `WaitlistEntry` | Category needed, MRN, priority (discharge-critical, urgent, routine), then sorted by time waiting |
| `LocationLog` | Append-only history of every sighting or movement (reader, RSSI, raw payload). Read-only in the admin |

The loan rules live in `equipment/services.py` (`issue_item`, `return_item`, `get_patient`, `send_overdue_notices`). The admin actions call them today, and the events endpoint should call the same functions later.

## Excel round trip (changeover from the spreadsheet)

On the Equipment list in the admin: **Export to Excel** and **Import from Excel**. The export uses the tracker's layout (a Read me tab, one tab per category and a Waitlist tab), with the status in words and colour.

Import reads either our export or TUH's original tracker. It always shows a preview first. Each exported row has a hidden fingerprint, so a re-import:
- skips rows nobody touched in Excel (the app's version stands)
- applies rows changed only in Excel
- lists rows changed in **both** Excel and the app as conflicts, and changes nothing for them

Patient-name columns are ignored, so only the MRN is kept. Columns missing from a sheet are left alone. With no Status column, the row's fill colour is used; that colour key is a guess until we see the real tracker (`FILL_TO_STATUS` in `equipment/spreadsheet.py`). The full design and the changeover plan are in the hackathon folder: `docs/integration-design.md`.

### TUH's register

Tested against the transcribed Wheelchair Asset Register (Asset Register and Cushion Register tabs, 74 items). The importer understands its layout:

| Register | In the app |
|---|---|
| Asset No., Type, Size (WxDxH), Serial No., HC21 No., DML No., Max Wgt, Comments, Last Service date, Purchased in, Replacement cover | Same-named fields (Type = make and model). `SN:`/`DML` prefixes and `N/A` are cleaned up |
| AT+ / AT- (column A) | On AssetTrak yes/no (*assumed meaning*) |
| Status `OWWP` | On loan (*assumed: on ward with patient*). No patient or therapist is recorded, so these open as loans that need an owner, dated from "Updated" |
| `STL …` | On loan, short-term (went home) |
| `OT Dept`, `Ready for use`, a storeroom | Available |
| `Workshop`, `WASH` | Awaiting repair (the brief has no "being cleaned" status) |
| `Missing …`, `Awaiting reimbursement` | Missing |
| Anything else (`With D50`, `?`, `Patrick to update Sarah`) | Kept in the notes; item flagged for someone to check |
| Grey "For the provision of clients in …" rows | Start a pool (e.g. "Burkitt / ARHC", "WSU / ASU only (own funding)") |
| Yellow Asset No. / red row | Flagged / missing |
| Updated | When the item's location was last confirmed |
| Patient Name | Ignored, never stored |

The import also warns about identifiers shared by two items (D11 and D12 have the same HC21 and DML numbers in the register). The export uses the register's own headings first, so staff recognise it.

## Synergy (design only)

`equipment/synergy.py` is the seam where Synergy (TUH's EPR) would connect. It isn't connected to anything:
- `loan_to_fhir(loan)`: the loan as a FHIR R4 DeviceUseStatement. Each loan's admin page shows it as "What Synergy would receive".
- `handle_adt("A02" | "A03", mrn)`: patient transfer moves their loans to the new ward; discharge turns open loans into short-term loans.
- `services.get_patient(mrn)`: the stub to swap for a FHIR Patient lookup.

## Commands

- `seed_demo [--reset]`: invented demo data (20 items, 3 loans: one normal, one overdue, one "needs an owner", and a waitlist)
- `setup_roles`: creates or refreshes the "OT staff" group permissions (runs on every deploy)
- `send_overdue_notices`: emails the issuing OT once per overdue loan
- `export_tracker [file.xlsx] [--pool ot|winter]` / `import_tracker file.xlsx [--apply] [--pool winter]`: the Excel round trip from the command line (import previews unless `--apply`)
- `simulate_adt A03 MRN100001 [--ward ...]`: pretend Synergy sent a discharge (A03) or transfer (A02)

## Devices on localhost (demo)

- **The micro:bit bridge and the laptop browser** use `http://localhost:8000` directly.
- **iPhone NFC.** iOS has no Web NFC, so the iPhone uses a tag's URL (background tag reading) or a Shortcuts automation that calls a URL. Either way it has to reach the laptop, and `localhost` on the phone means the phone itself. Run `python manage.py runserver 0.0.0.0:8000`, add the laptop's Wi-Fi IP to `ALLOWED_HOSTS` in `.env`, and point the tags or Shortcut at `http://<laptop-ip>:8000/...`. Venue Wi-Fi often stops devices reaching each other; a phone hotspot avoids that.
- **Android (if used).** Web NFC and the camera need a secure page. Use Chrome's USB port forwarding (`chrome://inspect` → Port forwarding, `8000 → localhost:8000`), and the phone's `http://localhost:8000` counts as secure.

## Deploy on Render

1. Push this folder to a GitHub repo.
2. In Render: **New > Blueprint**, then pick the repo. `render.yaml` creates the web service and a Postgres database, and generates `SECRET_KEY`.
3. After the first deploy, open the service's **Shell** and run `python manage.py createsuperuser` (and `seed_demo` if you want the demo data).

Free tier limits to plan around ([Render docs](https://render.com/docs/free)):
- The web service sleeps after a period with no traffic and takes up to a minute to wake. **Open the site at about 08:45 before judging.**
- The free Postgres database expires 30 days after creation. That's fine for the hackathon; export the data if you want to keep it.
- No persistent disk, so don't use SQLite on Render. Data would vanish on every deploy.

## Email (Gmail)

Locally, emails print to the console until you set `EMAIL_HOST`. To send through the team Gmail account:

1. The account needs 2-Step Verification on, and an **App Password** (Google Account → Security → App passwords). Your normal Gmail password won't work.
2. Add this to `.env` (never commit it; `.env` is in `.gitignore`):
   ```
   EMAIL_HOST=smtp.gmail.com
   EMAIL_PORT=587
   EMAIL_USE_TLS=1
   EMAIL_HOST_USER=<the gmail address>
   EMAIL_HOST_PASSWORD=<the 16-character app password; spaces are fine>
   DEFAULT_FROM_EMAIL=OT Equipment <the gmail address>
   ```
3. Restart `runserver`, then send a test email with Django's built-in command:
   ```bash
   .venv/bin/python manage.py sendtestemail you@example.com
   ```
4. Overdue notices go to the issuing OT's email address (their Staff record), via `send_overdue_notices`.

Gmail allows about 500 messages a day from a normal account, which is plenty for a demo.

**Render's free web services block outbound SMTP (ports 25, 465 and 587)** ([changelog](https://render.com/changelog/free-web-services-will-no-longer-allow-outbound-traffic-to-smtp-ports)). On Render, Gmail SMTP will time out unless you either:
- move the web service to a paid instance, then add the same `EMAIL_*` values under Environment in the Render dashboard (not in `render.yaml`), or
- send over HTTPS instead: an email API such as Postmark, Mailgun or SendGrid (each has a Django backend package), or the Gmail API.

For the demo, the console backend plus a screenshot of the email is enough.
