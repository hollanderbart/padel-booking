#!/usr/bin/env bash
set -e

# Zet /data/options.json om naar /app/.env en /app/config.yaml
python3 /app/options_to_config.py

# Zorg dat de config directory bestaat voordat booking.py draait
mkdir -p /config/knltb

# Herstel sessie cookies als aanwezig
[ -f /config/knltb/.session_cookies.json ] && \
    cp /config/knltb/.session_cookies.json /app/.session_cookies.json

cd /app
python booking.py --debug

# Persisteer cookies zodat de sessie bewaard blijft tussen runs
[ -f /app/.session_cookies.json ] && \
    cp /app/.session_cookies.json /config/knltb/.session_cookies.json
