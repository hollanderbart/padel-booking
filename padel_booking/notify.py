"""
Notificatie systeem voor het booking script.
Ondersteunt HA mobile app push notificaties en macOS notificaties.
"""

import os
import re
import subprocess
import platform
import logging
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

# Meet & Play betaalt via iDEAL (Mollie); een iDEAL-betaling verloopt na 15 minuten en dan
# annuleert Meet & Play de reservering. Alleen bij zulke links is de deadline bekend.
PAYMENT_WINDOW = timedelta(minutes=15)
PAYMENT_HOSTS = ("mollie.com", "ideal.nl")
WEEKDAYS_NL = ("ma", "di", "wo", "do", "vr", "za", "zo")


def _applescript_str(value: str) -> str:
    """Quote een string als AppleScript-literal (backslashes en quotes escapen)."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Notifier:
    """Beheer notificaties voor verschillende platformen."""

    def __init__(self):
        self.platform = platform.system()

    def send(self, title: str, message: str, sound: bool = True, url: str = "", time_sensitive: bool = False) -> None:
        # Probeer altijd eerst HA mobile push als SUPERVISOR_TOKEN aanwezig is
        if os.environ.get("SUPERVISOR_TOKEN"):
            self._send_ha_push(title, message, url, time_sensitive)
            return

        if self.platform == "Darwin":
            self._send_macos(title, message, sound)
        else:
            self._send_console(title, message)

    def _send_ha_push(self, title: str, message: str, url: str = "", time_sensitive: bool = False) -> None:
        if not _HAS_REQUESTS:
            logger.warning("requests niet beschikbaar — kan geen HA push sturen")
            self._send_console(title, message)
            return

        token = os.environ["SUPERVISOR_TOKEN"]
        device_id = os.environ.get("HA_NOTIFY_DEVICE_ID", "").strip()
        base_url = "http://supervisor/core/api"

        if device_id:
            service_url = f"{base_url}/services/notify/mobile_app_{device_id}"
        else:
            service_url = f"{base_url}/services/notify/notify"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {"title": title, "message": message}
        data = {}
        if url:
            data["url"] = url
        if time_sensitive:
            # iOS: komt door Focus/Slaap heen en blijft op het toegangsscherm staan
            data["push"] = {"interruption-level": "time-sensitive"}
        if data:
            payload["data"] = data

        try:
            resp = _requests.post(service_url, json=payload, headers=headers, timeout=10)
            resp.raise_for_status()
            logger.info("HA push notificatie verzonden: %s", title)
        except Exception as e:  # noqa: BLE001 — notificatie is best-effort
            logger.warning("HA push notificatie mislukt: %s — fallback naar console", e)
            self._send_console(title, message)

    def _send_macos(self, title: str, message: str, sound: bool = True) -> None:
        try:
            sound_part = ' sound name "default"' if sound else ""
            script = f'display notification {_applescript_str(message)} with title {_applescript_str(title)}{sound_part}'
            subprocess.run(['osascript', '-e', script], check=True, capture_output=True, text=True)
            logger.info("macOS notificatie verzonden: %s", title)
        except Exception as e:  # noqa: BLE001 — notificatie is best-effort
            logger.warning("macOS notificatie mislukt: %s", e)
            self._send_console(title, message)

    def _send_console(self, title: str, message: str) -> None:
        separator = "=" * 60
        print(f"\n{separator}")
        print(f"{title}")
        print(f"{separator}")
        print(message)
        print(f"{separator}\n")


def payment_deadline(payment_url: str, sent_at: datetime) -> datetime | None:
    """Uiterste betaalmoment voor een iDEAL/Mollie-link; None als de link geen bekende betaalpagina is."""
    host = urlparse(payment_url).netloc.lower()
    if any(host == h or host.endswith("." + h) for h in PAYMENT_HOSTS):
        return sent_at + PAYMENT_WINDOW
    return None


def slot_label(club_name: str, booked_date: str, time_range: str) -> str:
    """Korte omschrijving voor een titel, bijv. "Hal 0172, do 12-11 20:00"."""
    parts = [club_name]
    when = []
    if booked_date:
        day = date.fromisoformat(booked_date)
        when.append(f"{WEEKDAYS_NL[day.weekday()]} {day:%d-%m}")
    if start := re.search(r"\d{1,2}:\d{2}", time_range or ""):
        when.append(start.group(0))
    if when:
        parts.append(" ".join(when))
    return ", ".join(p for p in parts if p)


def notify_booking_available(
    court_name: str,
    time: str,
    location: str,
    payment_url: str = "",
    booked_date: str = "",
    provider: str = "",
    deadline: datetime | None = None,
) -> None:
    club_name = location.split(" — ", maxsplit=1)[0]
    label = slot_label(club_name, booked_date, time)
    provider_line = f"via {provider}\n" if provider else ""
    message = f"{provider_line}{court_name} — {time}\n{location}"
    if deadline:
        title = f"Betaal vóór {deadline:%H:%M}: {label}"
        message += "\n\nTik om te betalen. Niet betaald binnen 15 minuten = reservering vervalt."
    else:
        title = f"Padelbaan beschikbaar: {label}"
        if payment_url:
            message += f"\n\nTik om te boeken bij {club_name}."
    Notifier().send(title=title, message=message, sound=True, url=payment_url, time_sensitive=bool(payment_url))


def notify_payment_reminder(club_name: str, booked_date: str, time_range: str, payment_url: str,
                            deadline: datetime | None = None) -> None:
    """Herinnering: de reservering is na een tijdje nog niet betaald."""
    label = slot_label(club_name, booked_date, time_range)
    title = f"Nog niet betaald — vóór {deadline:%H:%M}: {label}" if deadline else f"Nog niet betaald: {label}"
    message = "Je reservering vervalt als je niet op tijd betaalt.\n\nTik om te betalen."
    Notifier().send(title=title, message=message, sound=True, url=payment_url, time_sensitive=True)
