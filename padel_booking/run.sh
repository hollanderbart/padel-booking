#!/usr/bin/env bash
set -e

# Zet /data/options.json om naar /app/.env en /app/config.yaml
python3 /app/options_to_config.py

# Zorg dat de config directory bestaat voordat het script draait
mkdir -p /config/padel

# Herstel provider sessie-bestanden als aanwezig
[ -f /config/padel/.meetandplay_cookies.json ] && \
    cp /config/padel/.meetandplay_cookies.json /data/.meetandplay_cookies.json
[ -f /config/padel/.playtomic_token.json ] && \
    cp /config/padel/.playtomic_token.json /data/.playtomic_token.json

cd /app
python orchestrator.py --debug

# Persisteer provider sessie-bestanden zodat ze bewaard blijven tussen runs
[ -f /data/.meetandplay_cookies.json ] && \
    cp /data/.meetandplay_cookies.json /config/padel/.meetandplay_cookies.json
[ -f /data/.playtomic_token.json ] && \
    cp /data/.playtomic_token.json /config/padel/.playtomic_token.json
