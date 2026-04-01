"""
Notificatie systeem voor het booking script.
Ondersteunt HA mobile app push notificaties en macOS notificaties.
"""

import os
import subprocess
import platform
import logging

logger = logging.getLogger(__name__)

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


class Notifier:
    """Beheer notificaties voor verschillende platformen."""

    def __init__(self):
        self.platform = platform.system()

    def send(self, title: str, message: str, sound: bool = True, url: str = "") -> None:
        # Probeer altijd eerst HA mobile push als SUPERVISOR_TOKEN aanwezig is
        if os.environ.get("SUPERVISOR_TOKEN"):
            self._send_ha_push(title, message, url)
            return

        if self.platform == "Darwin":
            self._send_macos(title, message, sound)
        else:
            self._send_console(title, message)

    def _send_ha_push(self, title: str, message: str, url: str = "") -> None:
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
        if url:
            payload["data"] = {"url": url}

        try:
            resp = _requests.post(service_url, json=payload, headers=headers, timeout=10)
            resp.raise_for_status()
            logger.info("HA push notificatie verzonden: %s", title)
        except Exception as e:
            logger.warning("HA push notificatie mislukt: %s — fallback naar console", e)
            self._send_console(title, message)

    def _send_macos(self, title: str, message: str, sound: bool = True) -> None:
        try:
            title = title.replace('"', '\\"')
            message = message.replace('"', '\\"')
            sound_part = ' sound name "default"' if sound else ''
            script = f'display notification "{message}" with title "{title}"{sound_part}'
            subprocess.run(['osascript', '-e', script], check=True, capture_output=True, text=True)
            logger.info("macOS notificatie verzonden: %s", title)
        except Exception as e:
            logger.warning("macOS notificatie mislukt: %s", e)
            self._send_console(title, message)

    def _send_console(self, title: str, message: str) -> None:
        separator = "=" * 60
        print(f"\n{separator}")
        print(f"{title}")
        print(f"{separator}")
        print(message)
        print(f"{separator}\n")


def notify_booking_available(court_name: str, time: str, location: str, payment_url: str = "") -> None:
    notifier = Notifier()
    message = f"{court_name}\n{time}\n{location}"
    if payment_url:
        message += f"\n\n{payment_url}"
    notifier.send(title="Padelbaan geboekt!", message=message, sound=True, url=payment_url)


def notify_no_courts_available() -> None:
    """Verstuur notificatie dat er geen banen beschikbaar zijn."""
    notifier = Notifier()
    notifier.send(
        title="Geen padelbanen beschikbaar",
        message="Er zijn momenteel geen binnenbanen beschikbaar in de opgegeven regio en tijd.",
        sound=False
    )


def notify_booking_error(error_message: str) -> None:
    """
    Verstuur notificatie bij een fout in het boekingsproces.

    Args:
        error_message: Beschrijving van de fout
    """
    notifier = Notifier()
    notifier.send(
        title="Fout bij boeken padelbaan",
        message=f"Er is een fout opgetreden: {error_message}",
        sound=True
    )


def notify_session_expired() -> None:
    """Verstuur notificatie dat de sessie is verlopen."""
    notifier = Notifier()
    notifier.send(
        title="Sessie verlopen",
        message="Je sessie is verlopen. Log opnieuw in via de browser.",
        sound=True
    )
