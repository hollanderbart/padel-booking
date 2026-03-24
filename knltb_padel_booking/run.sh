#!/usr/bin/env bash
set -e

# /config is de HA config map (persistent, buiten de container)
# Kopieer .env en cookies naar /app zodat booking.py ze vindt
[ -f /config/knltb/.env ] && cp /config/knltb/.env /app/.env
[ -f /config/knltb/config.yaml ] && cp /config/knltb/config.yaml /app/config.yaml
[ -f /config/knltb/.session_cookies.json ] && cp /config/knltb/.session_cookies.json /app/.session_cookies.json

cd /app
python booking.py

# Persisteer cookies zodat de sessie bewaard blijft tussen runs
mkdir -p /config/knltb
[ -f /app/.session_cookies.json ] && cp /app/.session_cookies.json /config/knltb/.session_cookies.json
