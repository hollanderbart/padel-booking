#!/usr/bin/env python3
"""
KNLTB Padel Booking Script
Automatiseert het boeken van padelbanen op meetandplay.nl
"""

import os
import sys
import yaml
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from dotenv import load_dotenv

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, TimeoutError as PlaywrightTimeoutError

from session import SessionManager
from notify import (
    notify_booking_available,
    notify_no_courts_available,
    notify_booking_error,
    notify_session_expired
)


class PadelBooker:
    """Hoofdklasse voor het boeken van padelbanen."""

    def __init__(self, config_path: str = "config.yaml"):
        """
        Initialiseer de padel booker.

        Args:
            config_path: Pad naar het configuratiebestand
        """
        self.config = self._load_config(config_path)
        self.session_manager = SessionManager(
            self.config['session']['cookies_file']
        )

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """
        Laad de configuratie uit YAML bestand.

        Args:
            config_path: Pad naar het configuratiebestand

        Returns:
            Dictionary met configuratie
        """
        config_file = Path(config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Configuratiebestand niet gevonden: {config_path}")

        with open(config_file, 'r') as f:
            return yaml.safe_load(f)

    def _get_next_booking_date(self) -> datetime:
        """
        Bereken de datum voor de volgende boeking.

        Returns:
            Datetime object voor de gewenste boekingsdatum
        """
        # Map dag namen naar weekdag nummers (0 = maandag, 6 = zondag)
        weekdays = {
            'monday': 0, 'maandag': 0,
            'tuesday': 1, 'dinsdag': 1,
            'wednesday': 2, 'woensdag': 2,
            'thursday': 3, 'donderdag': 3,
            'friday': 4, 'vrijdag': 4,
            'saturday': 5, 'zaterdag': 5,
            'sunday': 6, 'zondag': 6
        }

        target_day = self.config['booking']['day'].lower()
        target_weekday = weekdays.get(target_day)

        if target_weekday is None:
            raise ValueError(f"Ongeldige dag in config: {target_day}")

        # Bereken de volgende gewenste dag
        today = datetime.now()
        days_ahead = (target_weekday - today.weekday()) % 7

        # Als het vandaag is en het tijdstip is al geweest, neem volgende week
        if days_ahead == 0:
            days_ahead = 7

        next_date = today + timedelta(days=days_ahead)
        return next_date

    def _setup_browser(self, headless: bool = True) -> tuple[Browser, BrowserContext]:
        """
        Zet de browser op met of zonder sessie.

        Args:
            headless: Of de browser in headless mode moet draaien

        Returns:
            Tuple van (browser, context)
        """
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=headless)

        # Maak een nieuwe context
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )

        # Probeer cookies te laden als ze bestaan
        if self.session_manager.cookies_exist():
            print("🔄 Bestaande sessie laden...")
            self.session_manager.load_cookies(context)

        return browser, context

    def _ensure_logged_in(self, browser: Browser, context: BrowserContext) -> BrowserContext:
        """
        Zorg ervoor dat de gebruiker is ingelogd.

        Args:
            browser: Playwright browser object
            context: Huidige browser context

        Returns:
            Browser context (mogelijk nieuw als login nodig was)
        """
        page = context.new_page()

        # Check of de sessie nog geldig is
        if self.session_manager.is_logged_in(page):
            page.close()
            return context

        # Sessie verlopen - vraag om handmatige login
        page.close()
        notify_session_expired()

        # Sluit oude context
        context.close()

        # Open browser voor handmatige login
        new_context = self.session_manager.manual_login(browser)
        return new_context

    def _search_courts(self, page: Page) -> bool:
        """
        Zoek naar beschikbare padelbanen.

        Args:
            page: Playwright page object

        Returns:
            True als er banen gevonden zijn, False anders
        """
        try:
            print("\n🔍 Zoeken naar beschikbare padelbanen...")

            # Navigeer naar de zoekpagina
            # OPMERKING: Deze URL en selectors moeten worden aangepast op basis van
            # de daadwerkelijke structuur van meetandplay.nl
            page.goto("https://www.meetandplay.nl/padel", wait_until="networkidle")

            # Stel zoekfilters in
            location = self.config['location']['city']
            radius = self.config['location']['radius_km']

            print(f"   Locatie: {location}")
            print(f"   Straal: {radius} km")
            print(f"   Type: Binnenbaan (padel dubbel)")

            # Zoek naar locatie invoerveld en vul in
            # Dit is een placeholder - de exacte selectors moeten worden bepaald
            try:
                location_input = page.locator('input[placeholder*="locatie"], input[name*="location"]').first
                location_input.fill(location)
                page.wait_for_timeout(1000)
            except:
                print("⚠️  Locatie invoerveld niet gevonden")

            # Selecteer sport type (Padel)
            try:
                sport_selector = page.locator('select[name*="sport"], button:has-text("Padel")').first
                if sport_selector.count() > 0:
                    sport_selector.click()
                    page.wait_for_timeout(500)
            except:
                print("⚠️  Sport selector niet gevonden")

            # Selecteer binnen/buiten
            court_type = self.config['booking']['court_type']
            if court_type == 'indoor':
                try:
                    indoor_option = page.locator('text=/binnen/i, input[value*="indoor"]').first
                    if indoor_option.count() > 0:
                        indoor_option.click()
                        page.wait_for_timeout(500)
                except:
                    print("⚠️  Binnenbaan filter niet gevonden")

            # Selecteer datum en tijd
            booking_date = self._get_next_booking_date()
            time_start = self.config['booking']['time_start']
            time_end = self.config['booking']['time_end']

            print(f"   Datum: {booking_date.strftime('%d-%m-%Y')}")
            print(f"   Tijd: {time_start} - {time_end}")

            # Vul datum in
            # Dit is een placeholder - de exacte implementatie hangt af van de site
            try:
                date_input = page.locator('input[type="date"], input[name*="date"]').first
                date_input.fill(booking_date.strftime('%Y-%m-%d'))
                page.wait_for_timeout(1000)
            except:
                print("⚠️  Datum invoerveld niet gevonden")

            # Vul start tijd in
            try:
                time_input = page.locator('input[type="time"], select[name*="time"]').first
                time_input.fill(time_start.replace(':', ''))
                page.wait_for_timeout(1000)
            except:
                print("⚠️  Tijd invoerveld niet gevonden")

            # Klik op zoeken knop
            try:
                search_button = page.locator('button:has-text("Zoek"), button[type="submit"]').first
                search_button.click()
                page.wait_for_load_state("networkidle")
            except:
                print("⚠️  Zoek knop niet gevonden")

            # Wacht op resultaten
            page.wait_for_timeout(2000)

            # Check of er resultaten zijn
            # Dit is een placeholder - de exacte selector hangt af van de site
            results = page.locator('.court-result, .booking-option, [data-testid*="court"]')

            if results.count() > 0:
                print(f"✓ {results.count()} baan(banen) gevonden!")
                return True
            else:
                print("⚠️  Geen beschikbare banen gevonden")
                return False

        except Exception as e:
            print(f"❌ Fout bij zoeken naar banen: {e}")
            return False

    def _select_and_book_court(self, page: Page) -> bool:
        """
        Selecteer de eerste beschikbare baan en ga naar betalingspagina.

        Args:
            page: Playwright page object

        Returns:
            True als succesvol, False anders
        """
        try:
            print("\n📝 Eerste beschikbare baan selecteren...")

            # Selecteer de eerste beschikbare baan
            # Dit is een placeholder - de exacte selectors moeten worden bepaald
            first_court = page.locator('.court-result, .booking-option, [data-testid*="court"]').first

            # Haal informatie op voor notificatie
            try:
                court_name = first_court.locator('.court-name, h2, h3').first.inner_text()
            except:
                court_name = "Onbekende baan"

            try:
                court_location = first_court.locator('.location, .address').first.inner_text()
            except:
                court_location = self.config['location']['city']

            time_slot = f"{self.config['booking']['time_start']} - {self.config['booking']['time_end']}"

            print(f"   Baan: {court_name}")
            print(f"   Locatie: {court_location}")
            print(f"   Tijd: {time_slot}")

            # Klik op de boekingsknop
            book_button = first_court.locator('button:has-text("Boek"), a:has-text("Boek")').first
            book_button.click()
            page.wait_for_load_state("networkidle")

            # Doorloop eventuele extra stappen (zoals aantal spelers bevestigen)
            page.wait_for_timeout(2000)

            # Zoek naar de "Doorgaan naar betaling" knop of vergelijkbaar
            try:
                checkout_button = page.locator(
                    'button:has-text("Betalen"), button:has-text("Doorgaan"), a:has-text("Afrekenen")'
                ).first

                if checkout_button.count() > 0:
                    checkout_button.click()
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(1000)
            except:
                print("⚠️  Checkout knop niet gevonden, mogelijk al op betalingspagina")

            # Check of we op de betalingspagina zijn
            if any(keyword in page.url.lower() for keyword in ['payment', 'checkout', 'betaling', 'betalen']):
                print("\n✓ Betalingspagina bereikt!")
                print("⏸️  Script stopt hier - gebruiker moet zelf betaling afronden\n")

                # Verstuur notificatie
                notify_booking_available(court_name, time_slot, court_location)

                # Wacht een moment zodat de gebruiker de pagina kan zien
                print("Browser blijft open voor 30 seconden...")
                page.wait_for_timeout(30000)

                return True
            else:
                print("⚠️  Niet op betalingspagina aangekomen")
                return False

        except Exception as e:
            print(f"❌ Fout bij selecteren en boeken van baan: {e}")
            return False

    def run(self, headless: bool = True) -> bool:
        """
        Voer het volledige boekingsproces uit.

        Args:
            headless: Of de browser in headless mode moet draaien

        Returns:
            True als succesvol, False anders
        """
        print("=" * 60)
        print("🎾 KNLTB Padel Booking Script")
        print("=" * 60)

        browser = None
        context = None

        try:
            # Zet browser op
            browser, context = self._setup_browser(headless=headless)

            # Zorg ervoor dat we zijn ingelogd
            context = self._ensure_logged_in(browser, context)

            # Maak een nieuwe pagina
            page = context.new_page()

            # Zoek naar beschikbare banen
            courts_found = self._search_courts(page)

            if not courts_found:
                notify_no_courts_available()
                return False

            # Selecteer en boek een baan
            booking_success = self._select_and_book_court(page)

            return booking_success

        except Exception as e:
            error_msg = f"Onverwachte fout: {e}"
            print(f"❌ {error_msg}")
            notify_booking_error(error_msg)
            return False

        finally:
            # Cleanup
            if context:
                context.close()
            if browser:
                browser.close()


def main():
    """Hoofdfunctie."""
    # Laad environment variables
    load_dotenv()

    # Parse command line argumenten
    headless = "--headed" not in sys.argv and "--no-headless" not in sys.argv

    try:
        booker = PadelBooker()
        success = booker.run(headless=headless)

        if success:
            print("\n✓ Script succesvol uitgevoerd!")
            sys.exit(0)
        else:
            print("\n⚠️  Script uitgevoerd maar geen boeking gemaakt")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n\n⚠️  Script gestopt door gebruiker")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Fatale fout: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
