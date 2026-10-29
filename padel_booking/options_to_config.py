import json
import yaml

with open("/data/options.json", encoding="utf-8") as f:
    opts = json.load(f)

# Schrijf .env
with open("/app/.env", "w", encoding="utf-8") as f:
    f.write(f"KNLTB_EMAIL={opts['knltb_email']}\n")
    f.write(f"KNLTB_PASSWORD={opts['knltb_password']}\n")
    if opts.get("ha_notify_device_id"):
        f.write(f"HA_NOTIFY_DEVICE_ID={opts['ha_notify_device_id']}\n")
    if opts.get("playtomic_enabled") and opts.get("playtomic_email"):
        f.write(f"PLAYTOMIC_EMAIL={opts['playtomic_email']}\n")
    if opts.get("playtomic_enabled") and opts.get("playtomic_password"):
        f.write(f"PLAYTOMIC_PASSWORD={opts['playtomic_password']}\n")

# booking_days (lijst, sinds 2.5.0); oudere installaties hebben alleen booking_day
booking_days = opts.get("booking_days") or [opts.get("booking_day", "thursday")]

# Schrijf config.yaml
config = {
    "location": {
        "city": opts["location_city"],
        "radius_km": opts["location_radius_km"],
        "latitude": opts.get("location_latitude"),
        "longitude": opts.get("location_longitude"),
    },
    "booking": {
        "days": booking_days,
        "time_start": opts["booking_time_start"],
        "time_end": opts["booking_time_end"],
        "duration_minutes": int(opts["duration_minutes"]),
        "court_type": opts["court_type"],
        "game_type": opts["game_type"],
        "weeks_ahead": int(opts["weeks_ahead"]),
    },
    "providers": {
        "meetandplay": {
            "enabled": opts.get("meetandplay_enabled", True),
            "cookies_file": "/config/padel/.meetandplay_cookies.json",
        },
        "playtomic": {
            "enabled": opts.get("playtomic_enabled", False),
            "token_cache_file": "/config/padel/.playtomic_token.json",
        },
    },
    "fetch_bookings": {
        "enabled": opts.get("fetch_bookings_enabled", True),
        "output_file": "/config/padel/future_bookings.json",
        "skip_booked_dates": opts.get("skip_booked_dates", True),
    },
    "schedule": {
        # smart: draai continu en boek op de vrijgavemomenten per club; nightly: één run per start
        "mode": opts.get("schedule_mode", "smart"),
        "url": "https://raw.githubusercontent.com/hollanderbart/padel-booking/booking-research/club_schedule.json",
        "cache_file": "/config/padel/club_schedule.json",
    },
    "state": {
        "booking_state_file": "/config/padel/.booking_state.json",
        "history_file": "/config/padel/booking_history.json",
        "last_run_file": "/config/padel/last_run.json",
        "pending_payments_file": "/config/padel/pending_payments.json",
    },
}

with open("/app/config.yaml", "w", encoding="utf-8") as f:
    yaml.dump(config, f, allow_unicode=True)

print(f"Config geladen: {opts['location_city']}, {'/'.join(booking_days)} {opts['booking_time_start']}–{opts['booking_time_end']}")
