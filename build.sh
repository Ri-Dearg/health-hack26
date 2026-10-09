#!/usr/bin/env bash
# Render build command. Runs on every deploy.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
python manage.py setup_roles
