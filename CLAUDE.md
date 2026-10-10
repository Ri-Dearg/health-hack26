# OT equipment tracker: Django prototype

The code for the hackathon in `/Users/Rory/Claude/Projects/hackathon`. Read that folder's `CLAUDE.md` and `docs/PLAN.md` (source of truth for features, rules and the demo) before changing behaviour.

- Django 5.2 LTS, Python 3.13, venv in `.venv` (managed with uv). Run `.venv/bin/python manage.py test` after changes.
- The admin does all CRUD for now. Loan rules live in `equipment/services.py`; future endpoints and pages must call those functions, not duplicate the logic.
- Patient = MRN only, never names. Demo data is invented (`seed_demo`).
- Statuses are the brief's five. "Overdue" and "needs an owner" are loan flags.
- `LocationLog` is append-only.
- Excel round trip: `equipment/spreadsheet.py` (fingerprinted rows, three-way compare, preview before apply). Keep `db_row()` and the parsers producing identical canonical values, or untouched exports will show as changes; `RoundTripTests` guard this.
- Synergy: `equipment/synergy.py` is design-only (FHIR preview, ADT handler). Design doc: hackathon `docs/integration-design.md`.
- Demo devices run against localhost; the iPhone (NFC via tag URL or Shortcuts) needs the laptop's LAN IP in `ALLOWED_HOSTS`.
- Deploy target: Render Blueprint (`render.yaml`, `build.sh`). The free tier has no shell, so `build.sh` does one-time setup (`ensure_superuser` from env vars, `seed_demo --if-empty` when `SEED_DEMO=1`); keep every build step safe to repeat. Free tier blocks SMTP, so Gmail SMTP only works locally or on a paid instance.
