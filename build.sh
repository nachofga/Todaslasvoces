#!/usr/bin/env bash
# Se ejecuta en cada despliegue (Render: "Build Command").
set -o errexit
pip install --upgrade pip
pip install -r requirements.txt
python manage.py collectstatic --noinput
