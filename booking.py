#!/usr/bin/env python3
"""
KNLTB Padel Booking Script
Automatiseert het boeken van padelbanen op meetandplay.nl

Werking:
  1. Hergebruik opgeslagen cookies als de sessie nog geldig is.
  2. Als de sessie verlopen is: probeer automatisch in te loggen via
     KNLTB_EMAIL / KNLTB_PASSWORD uit het .env bestand. Als die niet
     beschikbaar zijn, open dan een zichtbare browser voor handmatige login.
  3. Zoek op meetandplay.nl/zoeken naar beschikbare clubs in de regio.
  4. Ga naar de clubpagina en filter op Padel + binnenbaan + avond.
  5. Selecteer het eerste beschikbare tijdslot dat binnen het gewenste
     tijdvenster valt.
  6. Klik door tot aan de betalingspagina en stuur een notificatie.
"""

import logging
import os
import re
import sys
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from playwright.sync_api import (
    sync_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from session import SessionManager
from notify import (
    notify_booking_available,
    notify_no_courts_available,
    notify_booking_error,
    notify_session_expired,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constanten
# ---------------------------------------------------------------------------

SEARCH_URL = "https://www.meetandplay.nl/zoeken"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Sport-ID's op meetandplay.nl
SPORT_IDS = {
    "tennis": "1",
    "padel": "2",
    "squash": "4",
    "pickleball": "13",
}

# Dagelabels voor Livewire dagdeel-filter
DAY_PARTS = {
    "morning": "morning",
    "ochtend": "morning",
    "afternoon": "afternoon",
    "middag": "afternoon",
    "evening": "evening",
    "avond": "evening",
}

# Dagnamen naar weekdag-nummer (0 = maandag)
WEEKDAYS = {
    "monday": 0, "maandag": 0,
    "tuesday": 1, "dinsdag": 1,
    "wednesday": 2, "woensdag": 2,
    "thursday": 3, "donderdag": 3,
    "friday": 4, "vrijdag": 4,
    "saturday": 5, "zaterdag": 5,
    "sunday": 6, "zondag": 6,
}


# ---------------------------------------------------------------------------
# Hoofdklasse
# ---------------------------------------------------------------------------

class PadelBooker:
    """Automatiseert het boeken van padelbanen op meetandplay.nl."""

    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self.session_manager = SessionManager(
            self.config["session"]["cookies_file"]
        )
        self._playwright = None

    # ------------------------------------------------------------------
    # Configuratie
    # ------------------------------------------------------------------

    def _load_config(self, config_path: str) -> dict:
        config_file = Path(config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Configuratiebestand niet gevonden: {config_path}")
        with open(config_file, "r") as f:
            return yaml.safe_load(f)

    # ------------------------------------------------------------------
    # Datumberekening
    # ------------------------------------------------------------------

    def _get_next_booking_date(self) -> datetime:
        """
        Bereken de datum van de eerstvolgende boekingsdag (bijv. donderdag).

        Als vandaag die dag is en het starttijdstip is nog niet verstreken,
        wordt vandaag teruggegeven. Anders de volgende week.
        """
        target_day_name = self.config["booking"]["day"].lower()
        target_weekday = WEEKDAYS.get(target_day_name)
        if target_weekday is None:
            raise ValueError(f"Ongeldige dag in config: {target_day_name}")

        time_start_str = self.config["booking"]["time_start"]
        target_hour, target_minute = map(int, time_start_str.split(":"))

        today = datetime.now()
        days_ahead = (target_weekday - today.weekday()) % 7

        if days_ahead == 0:
            # Vandaag is de gewenste dag: check of het tijdslot al voorbij is
            slot_time = today.replace(
                hour=target_hour, minute=target_minute, second=0, microsecond=0
            )
            if today >= slot_time:
                # Tijdslot al voorbij: neem volgende week
                days_ahead = 7

        return today + timedelta(days=days_ahead)

    # ------------------------------------------------------------------
    # Browser setup
    # ------------------------------------------------------------------

    def _make_context(self, browser: Browser, headless: bool) -> BrowserContext:
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=USER_AGENT,
        )
        if self.session_manager.cookies_exist():
            logger.info("Bestaande sessie laden uit cookies...")
            self.session_manager.load_cookies(context)
        return context

    def _accept_cookies(self, page: Page) -> None:
        """Sluit het cookieconsentvenster als het aanwezig is."""
        try:
            btn = page.locator('button:has-text("Alles toestaan")')
            if btn.count() > 0:
                btn.first.click()
                page.wait_for_timeout(800)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def _ensure_logged_in(
        self, browser: Browser, context: BrowserContext, headless: bool
    ) -> BrowserContext:
        """
        Controleer of de sessie nog geldig is en log opnieuw in indien nodig.
        """
        page = context.new_page()
        logged_in = self.session_manager.is_logged_in(page)
        page.close()

        if logged_in:
            return context

        # Sessie verlopen
        notify_session_expired()
        context.close()

        email = os.getenv("KNLTB_EMAIL", "").strip()
        password = os.getenv("KNLTB_PASSWORD", "").strip()

        if email and password:
            logger.info("Credentials gevonden in omgeving — automatisch inloggen...")
            return self.session_manager.auto_login(browser, email, password)

        # Geen credentials: handmatige login (geeft een headed browser)
        logger.info("Geen KNLTB_EMAIL/KNLTB_PASSWORD in omgeving — handmatige login")
        headed_browser = self._playwright.chromium.launch(headless=False)
        new_context = self.session_manager.manual_login(headed_browser)
        # Kopieer cookies naar de primaire (headless) context
        cookies = new_context.cookies()
        headed_browser.close()
        fresh_context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=USER_AGENT,
        )
        fresh_context.add_cookies(cookies)
        return fresh_context

    # ------------------------------------------------------------------
    # Zoeken naar clubs
    # ------------------------------------------------------------------

    def _search_clubs(self, page: Page) -> list[dict]:
        """
        Zoek beschikbare clubs op meetandplay.nl/zoeken.

        Filters:
          - Sport: Padel (ID 2)
          - Locatie: stad uit config
          - Afstand: straal uit config
          - Daktype: INDOOR

        Returns:
            Lijst van dicts met 'name', 'address', 'url' per club.
        """
        city = self.config["location"]["city"]
        radius = str(self.config["location"]["radius_km"])
        booking_date = self._get_next_booking_date()
        date_str = booking_date.strftime("%d-%m-%Y")

        logger.info("Zoeken naar clubs in %s (straal %s km) op %s...", city, radius, date_str)

        page.goto(SEARCH_URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1500)
        self._accept_cookies(page)

        # Sport: Padel
        page.locator("select#sportId").select_option("2")
        page.wait_for_timeout(1500)

        # Locatie invoeren en blur triggeren (Livewire)
        loc_input = page.locator("input#location")
        loc_input.fill(city)
        loc_input.blur()
        page.wait_for_timeout(2500)

        # Afstand
        page.locator("select#distance").select_option(radius)
        page.wait_for_timeout(1500)

        # Daktype: binnen
        court_type = self.config["booking"].get("court_type", "indoor")
        if court_type == "indoor":
            page.locator("select#indoor").select_option("INDOOR")
            page.wait_for_timeout(1500)

        # Datum instellen via Livewire (het datumveld is readonly)
        html = page.content()
        lw_match = re.search(
            r"window\.Livewire\.find\('([^']+)'\)\.set\('date'", html
        )
        if lw_match:
            lw_id = lw_match.group(1)
            page.evaluate(
                f"window.Livewire.find('{lw_id}').set('date', '{date_str}')"
            )
            page.wait_for_timeout(2500)
        else:
            logger.warning("Livewire datum-component niet gevonden op zoekpagina")

        # Haal resultaten op
        cards = page.locator(".c-club-card.mp-club-card")
        count = cards.count()
        logger.info("%d club(s) gevonden na filteren", count)

        clubs = []
        for i in range(count):
            card = cards.nth(i)
            try:
                name = card.locator("h3").first.inner_text().strip()
                address = card.locator(".c-club-card__address").first.inner_text().strip()
                book_url = card.locator("a.mp-cta-link").first.get_attribute("href") or ""
                clubs.append({"name": name, "address": address, "url": book_url})
            except Exception as e:
                logger.debug("Fout bij uitlezen club %d: %s", i, e)

        return clubs

    # ------------------------------------------------------------------
    # Tijdslot selecteren
    # ------------------------------------------------------------------

    def _find_timeslot(self, page: Page, club: dict) -> Optional[dict]:
        """
        Navigeer naar de clubpagina, stel filters in en zoek een tijdslot
        dat binnen het gewenste tijdvenster valt.

        Returns:
            Dict met 'slot_id', 'court_name', 'time_range' of None.
        """
        time_start = self.config["booking"]["time_start"]   # bijv. "19:00"
        time_end = self.config["booking"]["time_end"]       # bijv. "21:30"
        booking_date = self._get_next_booking_date()
        date_str = booking_date.strftime("%d-%m-%Y")
        game_type = self.config["booking"].get("game_type", "double").lower()

        logger.info(
            "Controleren tijdsloten bij %s voor %s (%s–%s)...",
            club["name"], date_str, time_start, time_end
        )

        page.goto(club["url"], wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1500)
        self._accept_cookies(page)

        # Sport: Padel
        sport_select = page.locator("select#sportId")
        if sport_select.count() > 0:
            sport_select.select_option("2")
            page.wait_for_timeout(1500)

        # Daktype: binnen
        court_type = self.config["booking"].get("court_type", "indoor")
        if court_type == "indoor":
            indoor_select = page.locator("select#indoor")
            if indoor_select.count() > 0:
                indoor_select.select_option("INDOOR")
                page.wait_for_timeout(1500)

        # Dagdeel: avond
        daypart_select = page.locator("select#dayPart")
        if daypart_select.count() > 0:
            daypart_select.select_option("evening")
            page.wait_for_timeout(1500)

        # Datum instellen via Livewire
        html = page.content()
        lw_match = re.search(
            r"window\.Livewire\.find\('([^']+)'\)\.set\('date'", html
        )
        if lw_match:
            lw_id = lw_match.group(1)
            page.evaluate(
                f"window.Livewire.find('{lw_id}').set('date', '{date_str}')"
            )
            page.wait_for_timeout(2500)

        # Parseer start- en eindtijd voor vergelijking
        start_h, start_m = map(int, time_start.split(":"))
        end_h, end_m = map(int, time_end.split(":"))
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m

        # Zoek door alle beschikbare tijdsloten
        slots = page.locator(".timeslot-container a.timeslot")
        logger.info("%d tijdslot(en) gevonden bij %s", slots.count(), club["name"])

        for i in range(slots.count()):
            slot = slots.nth(i)

            # Controleer court type label (binnen/dubbelspel) wanneer game_type=double
            # Het bovenliggende element bevat de court-type context.
            # We lezen de tekst van het dichtstbijzijnde mp-court-type boven dit slot.
            try:
                court_type_label = slot.evaluate(
                    """el => {
                        let sibling = el.closest('.timeslots');
                        if (!sibling) return '';
                        let prev = sibling.previousElementSibling;
                        return prev ? prev.innerText : '';
                    }"""
                ).lower()
            except Exception:
                court_type_label = ""

            # Sla buiten-banen over als we binnenbanen willen
            if court_type == "indoor" and "buiten" in court_type_label:
                continue

            # Controleer of game_type overeenkomt (dubbelspel / enkelspel)
            if game_type == "double" and "enkelspel" in court_type_label:
                continue
            if game_type == "single" and "dubbelspel" in court_type_label:
                continue

            # Lees tijdstip uit het tijdslot
            try:
                time_text = slot.locator(".timeslot-time").first.inner_text().strip()
                # Format: "19:00 - 20:00\n90 minuten" of "19:00 - 20:00"
                slot_start_str = time_text.split("–")[0].split("-")[0].strip().split("\n")[0].strip()
                slot_start_str = slot_start_str[:5]  # "HH:MM"
                sh, sm = map(int, slot_start_str.split(":"))
                slot_start_min = sh * 60 + sm
            except Exception as e:
                logger.debug("Kon tijdslot-tijd niet lezen: %s", e)
                continue

            # Controleer of het tijdslot binnen het gewenste venster valt
            if not (start_minutes <= slot_start_min < end_minutes):
                continue

            # Gevonden!
            try:
                court_name = slot.locator(".timeslot-name").first.inner_text().strip()
                court_name = court_name.split("\n")[0].strip()
            except Exception:
                court_name = "Onbekende baan"

            slot_id = slot.get_attribute("id") or ""
            logger.info(
                "Tijdslot gevonden: %s om %s (baan: %s)", club["name"], slot_start_str, court_name
            )
            return {
                "slot_id": slot_id,
                "court_name": court_name,
                "time_range": time_text.replace("\n", " "),
                "club_name": club["name"],
                "club_address": club["address"],
            }

        logger.info("Geen geschikt tijdslot gevonden bij %s", club["name"])
        return None

    # ------------------------------------------------------------------
    # Boeking afronden
    # ------------------------------------------------------------------

    def _book_timeslot(self, page: Page, slot_info: dict) -> bool:
        """
        Voeg het tijdslot toe aan de winkelwagen en ga naar betaling.

        De flow:
          1. Klik op het tijdslot → "Toevoegen"-knop verschijnt
          2. Klik "Toevoegen" (voegt toe aan winkelwagen)
          3. Klik "Afrekenen" → redirect naar betalingspagina
          4. Stuur notificatie

        Returns:
            True als betalingspagina bereikt, False anders.
        """
        slot_id = slot_info["slot_id"]

        logger.info(
            "Tijdslot %s toevoegen aan winkelwagen (%s bij %s)...",
            slot_id, slot_info["time_range"], slot_info["club_name"]
        )

        # Stap 1: klik op het tijdslot (activeert de "Toevoegen"-knop)
        slot_anchor = page.locator(f"a.timeslot#{slot_id}")
        if slot_anchor.count() == 0:
            # Fallback: zoek op data-attribute
            slot_anchor = page.locator(f"a[id='{slot_id}']")

        add_btn = slot_anchor.locator('button:has-text("Toevoegen")')
        if add_btn.count() == 0:
            logger.warning("'Toevoegen'-knop niet gevonden in tijdslot %s", slot_id)
            return False

        add_btn.first.click()
        page.wait_for_timeout(2500)

        # Stap 2: klik "Afrekenen"
        checkout_btn = page.locator(f'button[wire\\:click="checkout({slot_id})"]')
        if checkout_btn.count() == 0:
            # Bredere selector als fallback
            checkout_btn = page.locator('button:has-text("Afrekenen")')

        if checkout_btn.count() == 0:
            logger.warning("'Afrekenen'-knop niet gevonden na toevoegen")
            return False

        checkout_btn.first.click()

        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(2000)

        current_url = page.url
        logger.info("URL na afrekenen: %s", current_url)

        # Controleer of we op een betaal/checkout pagina zijn
        payment_keywords = ["payment", "checkout", "betaling", "betalen", "order", "bestelling"]
        if any(kw in current_url.lower() for kw in payment_keywords):
            logger.info("Betalingspagina bereikt!")
            notify_booking_available(
                slot_info["court_name"],
                slot_info["time_range"],
                f"{slot_info['club_name']} — {slot_info['club_address']}",
            )
            print("\n" + "=" * 60)
            print("BETALINGSPAGINA BEREIKT")
            print("=" * 60)
            print(f"Club:  {slot_info['club_name']}")
            print(f"Adres: {slot_info['club_address']}")
            print(f"Baan:  {slot_info['court_name']}")
            print(f"Tijd:  {slot_info['time_range']}")
            print("=" * 60)
            print("Script stopt. Rond de betaling zelf af in de browser.")
            print("=" * 60 + "\n")
            # Houd browser 30 seconden open zodat de gebruiker kan kijken
            page.wait_for_timeout(30000)
            return True

        # Mogelijk werd doorgestuurd naar login (sessie verlopen tijdens boeking)
        if "inloggen" in current_url:
            logger.error("Doorgestuurd naar loginpagina tijdens boeking — sessie verlopen")
        else:
            logger.warning(
                "Betalingspagina niet bereikt. Huidige URL: %s", current_url
            )
        return False

    # ------------------------------------------------------------------
    # Hoofdflow
    # ------------------------------------------------------------------

    def run(self, headless: bool = True) -> bool:
        """
        Voer het volledige boekingsproces uit.

        Returns:
            True als een tijdslot succesvol geboekt is, anders False.
        """
        logger.info("=" * 60)
        logger.info("KNLTB Padel Booking Script gestart")
        logger.info("=" * 60)

        booking_date = self._get_next_booking_date()
        logger.info(
            "Doeldatum: %s (%s %s–%s)",
            booking_date.strftime("%d-%m-%Y"),
            self.config["booking"]["day"],
            self.config["booking"]["time_start"],
            self.config["booking"]["time_end"],
        )

        browser = None
        context = None

        with sync_playwright() as pw:
            self._playwright = pw
            try:
                browser = pw.chromium.launch(headless=headless)
                context = self._make_context(browser, headless)
                context = self._ensure_logged_in(browser, context, headless)

                page = context.new_page()

                # Stap 1: zoek clubs in de regio
                clubs = self._search_clubs(page)
                if not clubs:
                    logger.warning("Geen clubs gevonden met de opgegeven filters")
                    notify_no_courts_available()
                    return False

                # Stap 2: probeer per club een tijdslot te vinden en te boeken
                for club in clubs:
                    slot_info = self._find_timeslot(page, club)
                    if slot_info:
                        success = self._book_timeslot(page, slot_info)
                        if success:
                            return True
                        # Tijdslot gevonden maar boeking mislukt: ga door naar volgende club
                        logger.warning(
                            "Boeking mislukt bij %s, volgende club proberen...",
                            club["name"]
                        )

                logger.warning(
                    "Geen beschikbaar tijdslot gevonden bij alle %d club(s)", len(clubs)
                )
                notify_no_courts_available()
                return False

            except Exception as e:
                error_msg = f"Onverwachte fout: {e}"
                logger.exception(error_msg)
                notify_booking_error(error_msg)
                return False

            finally:
                self._playwright = None
                if context:
                    try:
                        context.close()
                    except Exception:
                        pass
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    load_dotenv()

    headless = "--headed" not in sys.argv and "--no-headless" not in sys.argv

    # Optioneel: verhoog log-level naar DEBUG via --debug vlag
    if "--debug" in sys.argv:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        booker = PadelBooker()
        success = booker.run(headless=headless)

        if success:
            logger.info("Script succesvol uitgevoerd — boeking geïnitieerd")
            sys.exit(0)
        else:
            logger.warning("Script uitgevoerd maar geen boeking gemaakt")
            sys.exit(1)

    except KeyboardInterrupt:
        logger.info("Script gestopt door gebruiker (Ctrl+C)")
        sys.exit(130)
    except Exception as e:
        logger.critical("Fatale fout: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
