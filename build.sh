#!/usr/bin/env bash
# Render build command. Runs on every deploy. Render's free tier has no shell,
# so the one-time setup (admin account, demo data) happens here, safely repeatable.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
python manage.py setup_roles
python manage.py ensure_superuser            # from DJANGO_SUPERUSER_USERNAME / _PASSWORD; never changes an existing account
if [ "${SEED_DEMO:-0}" = "1" ]; then
  python manage.py seed_demo --if-empty      # invented demo data, only into an empty database
fi
