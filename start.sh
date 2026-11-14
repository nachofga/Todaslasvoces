#!/usr/bin/env bash
# Arranque del servidor (Render: "Start Command").
# 1) aplica los cambios de base de datos  2) carga listas y usuario de gestión  3) arranca la web
set -o errexit
python manage.py migrate --noinput
python manage.py bootstrap
# Un proceso con 8 hilos: suficiente para el piloto y cabe en 512 MB de memoria.
exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --threads 8 --timeout 180 --access-logfile -
