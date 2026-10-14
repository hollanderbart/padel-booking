#!/usr/bin/env bash
set -euo pipefail

# Zet /data/options.json om naar /app/.env en /app/config.yaml
python3 /app/options_to_config.py

# Sessiebestanden, state en club-schema staan in /config/padel (blijft bewaard tussen runs/updates)
mkdir -p /config/padel
# Eenmalige migratie: oudere versies bewaarden de sessiebestanden in /data
for f in .meetandplay_cookies.json .playtomic_token.json; do
    if [ -f "/data/$f" ] && [ ! -f "/config/padel/$f" ]; then
        cp "/data/$f" "/config/padel/$f"
    fi
done

cd /app

MODE=$(python3 -c "
import yaml
cfg = yaml.safe_load(open('config.yaml'))
print(cfg.get('schedule', {}).get('mode', 'smart'))
" 2>/dev/null || echo "smart")

if [ "$MODE" = "smart" ]; then
    # Draait continu: start de booker op de vrijgavemomenten van de clubs + nachtelijke run
    exec python scheduler.py --debug
fi

# nightly: één run per start van de addon (bijv. via een HA automation om 00:01).
# Eerst de reserveringen uit je account ophalen (/config/padel/future_bookings.json),
# zodat de booker datums overslaat die al geboekt zijn.
FETCH_ENABLED=$(python3 -c "
import yaml
cfg = yaml.safe_load(open('config.yaml'))
print('true' if cfg.get('fetch_bookings', {}).get('enabled', True) else 'false')
" 2>/dev/null || echo "true")

if [ "$FETCH_ENABLED" = "true" ]; then
    python fetch_bookings.py --output /config/padel/future_bookings.json || true
else
    echo "fetch_bookings uitgeschakeld — overgeslagen"
fi

# Exit code 1 betekent "geen boeking gemaakt" en mag het script niet afbreken.
python orchestrator.py --debug || echo "Geen boeking gemaakt (exit $?)"
