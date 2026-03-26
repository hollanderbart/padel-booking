"""
Notificatie systeem voor het booking script.
Ondersteunt macOS notificaties.
"""

import subprocess
import platform
from typing import Optional


class Notifier:
    """Beheer notificaties voor verschillende platformen."""

    def __init__(self):
        """Initialiseer de notifier."""
        self.platform = platform.system()

    def send(self, title: str, message: str, sound: bool = True) -> None:
        """
        Verstuur een notificatie.

        Args:
            title: Titel van de notificatie
            message: Bericht van de notificatie
            sound: Of er een geluid moet worden afgespeeld
        """
        if self.platform == "Darwin":  # macOS
            self._send_macos(title, message, sound)
        else:
            # Fallback: print naar console
            self._send_console(title, message)

    def _send_macos(self, title: str, message: str, sound: bool = True) -> None:
        """
        Verstuur een macOS notificatie via osascript.

        Args:
            title: Titel van de notificatie
            message: Bericht van de notificatie
            sound: Of er een geluid moet worden afgespeeld
        """
        try:
            # Escape quotes in title en message
            title = title.replace('"', '\\"')
            message = message.replace('"', '\\"')

            sound_part = ' sound name "default"' if sound else ''

            script = f'''
            display notification "{message}" with title "{title}"{sound_part}
            '''

            subprocess.run(
                ['osascript', '-e', script],
                check=True,
                capture_output=True,
                text=True
            )
            print(f"✓ Notificatie verzonden: {title}")

        except subprocess.CalledProcessError as e:
            print(f"⚠️  Fout bij verzenden van macOS notificatie: {e}")
            self._send_console(title, message)
        except Exception as e:
            print(f"⚠️  Onverwachte fout bij notificatie: {e}")
            self._send_console(title, message)

    def _send_console(self, title: str, message: str) -> None:
        """
        Print de notificatie naar de console als fallback.

        Args:
            title: Titel van de notificatie
            message: Bericht van de notificatie
        """
        separator = "=" * 60
        print(f"\n{separator}")
        print(f"📢 {title}")
        print(f"{separator}")
        print(message)
        print(f"{separator}\n")


def notify_booking_available(court_name: str, time: str, location: str) -> None:
    """
    Verstuur notificatie dat er een baan beschikbaar is.

    Args:
        court_name: Naam van de baan
        time: Tijdstip van de boeking
        location: Locatie van de baan
    """
    notifier = Notifier()
    notifier.send(
        title="Padelbaan gevonden!",
        message=f"Baan beschikbaar: {court_name}\nTijd: {time}\nLocatie: {location}\n\nGa naar de betalingspagina om te bevestigen.",
        sound=True
    )


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
