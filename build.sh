#!/usr/bin/env bash
# Render build script — runs once on every deploy before the web process starts.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input
python manage.py seed_plans
