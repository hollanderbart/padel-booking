import json
import yaml

with open("/data/options.json") as f:
    opts = json.load(f)

# Schrijf .env
with open("/app/.env", "w") as f:
    f.write(f"KNLTB_EMAIL={opts['knltb_email']}\n")
    f.write(f"KNLTB_PASSWORD={opts['knltb_password']}\n")
    if opts.get("ha_notify_device_id"):
        f.write(f"HA_NOTIFY_DEVICE_ID={opts['ha_notify_device_id']}\n")

# Schrijf config.yaml
config = {
    "location": {
        "city": opts["location_city"],
        "radius_km": opts["location_radius_km"],
    },
    "booking": {
        "day": opts["booking_day"],
        "time_start": opts["booking_time_start"],
        "time_end": opts["booking_time_end"],
        "duration_minutes": int(opts["duration_minutes"]),
        "court_type": opts["court_type"],
        "game_type": opts["game_type"],
        "weeks_ahead": int(opts["weeks_ahead"]),
    },
    "session": {
        "cookies_file": ".session_cookies.json",
    },
}
with open("/app/config.yaml", "w") as f:
    yaml.dump(config, f, allow_unicode=True)

print(f"Config geladen: {opts['location_city']}, {opts['booking_day']} {opts['booking_time_start']}–{opts['booking_time_end']}")
