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
from notify import notify_booking_available

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
        return self._get_upcoming_booking_dates(count=1)[0]

    def _get_upcoming_booking_dates(self, count: int = 3) -> list:
        """
        Geef een lijst van de eerstvolgende `count` boekingsdagen (bijv. de komende
        3 donderdagen) terug, gesorteerd van vroegst naar latest.

        Als vandaag de gewenste dag is maar het tijdslot al voorbij is, wordt
        vandaag overgeslagen en begint de reeks bij volgende week.
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
            slot_time = today.replace(
                hour=target_hour, minute=target_minute, second=0, microsecond=0
            )
            if today >= slot_time:
                days_ahead = 7

        first_date = today + timedelta(days=days_ahead)
        return [first_date + timedelta(weeks=i) for i in range(count)]

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
        context.close()

        email = os.getenv("KNLTB_EMAIL", "").strip()
        password = os.getenv("KNLTB_PASSWORD", "").strip()

        if email and password:
            logger.info("Credentials gevonden in omgeving — automatisch inloggen...")
            new_context, success = self.session_manager.auto_login(browser, email, password)
            if success:
                return new_context
            logger.error("Automatisch inloggen mislukt — script stopt (geen X server beschikbaar in Docker)")
            raise RuntimeError(
                "Automatisch inloggen mislukt. Controleer KNLTB_EMAIL/KNLTB_PASSWORD in /config/knltb/.env"
            )

        logger.error("Geen KNLTB_EMAIL/KNLTB_PASSWORD in omgeving — script stopt")
        raise RuntimeError(
            "Geen credentials gevonden. Voeg KNLTB_EMAIL en KNLTB_PASSWORD toe aan /config/knltb/.env"
        )

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

        page.goto(SEARCH_URL, wait_until="load", timeout=30000)
        page.wait_for_timeout(1500)
        self._accept_cookies(page)

        # Sport: Padel (sla over als het veld uitgeschakeld is — al vooringesteld)
        sport_select = page.locator("select#sportId")
        if sport_select.count() > 0 and not sport_select.is_disabled():
            sport_select.select_option("2")
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

    def _find_timeslot(self, page: Page, club: dict, booking_date: Optional[datetime] = None) -> Optional[dict]:
        """
        Navigeer naar de clubpagina, stel filters in en zoek een tijdslot
        dat binnen het gewenste tijdvenster valt.

        Args:
            booking_date: De doeldatum. Als None, wordt de eerstvolgende boekingsdag gebruikt.

        Returns:
            Dict met 'slot_id', 'court_name', 'time_range' of None.
        """
        time_start = self.config["booking"]["time_start"]   # bijv. "19:30"
        time_end = self.config["booking"]["time_end"]       # bijv. "21:00"
        duration_minutes = int(self.config["booking"].get("duration_minutes", 90))
        if booking_date is None:
            booking_date = self._get_next_booking_date()
        date_str = booking_date.strftime("%d-%m-%Y")
        game_type = self.config["booking"].get("game_type", "double").lower()

        logger.info(
            "Controleren tijdsloten bij %s voor %s (%s–%s)...",
            club["name"], date_str, time_start, time_end
        )

        page.goto(club["url"], wait_until="load", timeout=30000)
        page.wait_for_timeout(1500)
        self._accept_cookies(page)

        # Sport: Padel (sla over als het veld uitgeschakeld is — al vooringesteld)
        sport_select = page.locator("select#sportId")
        if sport_select.count() > 0 and not sport_select.is_disabled():
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

        # Duur selecteren (bijv. 90 minuten)
        duration_select = page.locator("select#duration")
        if duration_select.count() > 0:
            duration_select.select_option(str(duration_minutes))
            page.wait_for_timeout(1500)
            logger.info("Duur ingesteld op %d minuten", duration_minutes)
        else:
            logger.debug("Geen duration-select gevonden op pagina")

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

            # Controleer duur als die vermeld staat in de tijdslot-tekst
            duration_match = re.search(r"(\d+)\s*min", time_text, re.IGNORECASE)
            if duration_match:
                slot_duration = int(duration_match.group(1))
                if slot_duration != duration_minutes:
                    logger.debug(
                        "Tijdslot om %s overgeslagen: duur %d min ≠ gewenste %d min",
                        slot_start_str, slot_duration, duration_minutes
                    )
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

        # Stap 1: klik op het tijdslot (opent detail/modal)
        # Gebruik altijd attribuut-selector: numerieke IDs zijn ongeldig als CSS #id selector
        slot_anchor = page.locator(f"a.timeslot[id='{slot_id}']")
        if slot_anchor.count() == 0:
            slot_anchor = page.locator(f"a[id='{slot_id}']")

        if slot_anchor.count() == 0:
            logger.warning("Tijdslot-anker niet gevonden voor id %s", slot_id)
            return False

        slot_anchor.first.click()
        page.wait_for_timeout(2000)

        # Stap 2: klik "Toevoegen" — kan binnen het slot staan OF buiten (modal/sidebar)
        add_btn = page.locator('button:has-text("Toevoegen")')
        if add_btn.count() == 0:
            # Alternatieve tekst
            add_btn = page.locator('button:has-text("Reserveren")')
        if add_btn.count() == 0:
            add_btn = page.locator('button:has-text("Boeken")')

        if add_btn.count() == 0:
            logger.warning("'Toevoegen'/'Reserveren'/'Boeken'-knop niet gevonden na klik op tijdslot %s", slot_id)
            logger.debug("Pagina-inhoud (fragment): %s", page.content()[:2000])
            return False

        logger.info("'%s'-knop gevonden, klikken...", add_btn.first.inner_text().strip())
        add_btn.first.click()
        page.wait_for_timeout(2500)

        # Stap 3: klik "Afrekenen" — gebruik de zichtbare knop via Playwright's :visible filter
        # Er kunnen meerdere "Afrekenen"-knoppen in de DOM zijn (zichtbaar + verborgen).
        checkout_locators = [
            f'button[wire\\:click="checkout({slot_id})"]:visible',
            'button[wire\\:click="checkout"]:visible',
            'button:visible:has-text("Afrekenen")',
            'a:visible:has-text("Afrekenen")',
        ]

        checkout_btn = None
        for selector in checkout_locators:
            candidate = page.locator(selector)
            if candidate.count() > 0:
                checkout_btn = candidate
                logger.info("'Afrekenen'-knop gevonden via selector: %s", selector)
                break

        if checkout_btn is None:
            logger.warning("'Afrekenen'-knop niet gevonden na toevoegen")
            logger.debug("Pagina-inhoud (fragment): %s", page.content()[:3000])
            return False

        logger.info("'Afrekenen'-knop klikken...")
        checkout_btn.first.click()

        try:
            page.wait_for_load_state("load", timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(2000)

        current_url = page.url
        logger.info("URL na afrekenen: %s", current_url)

        # Sessie verlopen tijdens boeking?
        if "inloggen" in current_url:
            logger.error("Doorgestuurd naar loginpagina tijdens boeking — sessie verlopen")
            return False

        # Meetandplay gebruikt Livewire: de winkelwagenpagina laadt op dezelfde URL.
        # Controleer of de breadcrumb of paginatitel "Winkelwagen" toont.
        try:
            page_html = page.content()
        except Exception:
            page_html = ""
        on_cart = (
            "winkelwagen" in current_url.lower()
            or "Winkelwagen" in page_html
        )

        if not on_cart:
            # Mogelijk directe redirect naar betaalpagina of /reserveren
            payment_keywords = ["payment", "checkout", "betaling", "betalen", "order", "bestelling", "reserveren"]
            if any(kw in current_url.lower() for kw in payment_keywords):
                on_cart = True  # behandel als succes hieronder

        if not on_cart:
            logger.warning("Winkelwagen/betalingspagina niet bereikt. Huidige URL: %s", current_url)
            try:
                page.screenshot(path="debug_after_checkout.png", full_page=True)
                logger.info("Debug-screenshot opgeslagen: debug_after_checkout.png")
            except Exception:
                pass
            return False

        logger.info("Winkelwagen bereikt — wachten op betaalknop...")

        # Wacht op de betaalknop en klik erop
        pay_selectors = [
            'a:has-text("Betalen")',
            'button:has-text("Betalen")',
            'a:has-text("Afrekenen")',
            'button:has-text("Nu betalen")',
            'a[href*="betalen"]',
            'a[href*="payment"]',
        ]
        pay_btn = None
        for _ in range(6):  # maximaal ~12 seconden wachten
            for sel in pay_selectors:
                candidate = page.locator(sel)
                if candidate.count() > 0:
                    try:
                        if candidate.first.is_visible():
                            pay_btn = candidate
                            break
                    except Exception:
                        pass
            if pay_btn:
                break
            page.wait_for_timeout(2000)

        if pay_btn:
            logger.info("Betaalknop gevonden, klikken...")
            pay_btn.first.click()
            page.wait_for_timeout(3000)
            current_url = page.url
            logger.info("URL na betaalknop: %s", current_url)

        # Succes: notificeer en houd browser open
        notify_booking_available(
            slot_info["court_name"],
            slot_info["time_range"],
            f"{slot_info['club_name']} — {slot_info['club_address']}",
            current_url,
        )
        print("\n" + "=" * 60)
        print("BOEKING GESLAAGD — WINKELWAGEN BEREIKT")
        print("=" * 60)
        print(f"Club:  {slot_info['club_name']}")
        print(f"Adres: {slot_info['club_address']}")
        print(f"Baan:  {slot_info['court_name']}")
        print(f"Tijd:  {slot_info['time_range']}")
        print(f"URL:   {current_url}")
        print("=" * 60)
        print("Script stopt. Rond de betaling zelf af in de browser.")
        print("=" * 60 + "\n")
        # Houd browser 30 seconden open zodat de gebruiker kan kijken
        page.wait_for_timeout(30000)
        return True

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

        booking_dates = self._get_upcoming_booking_dates(count=4)
        logger.info(
            "Zoeken naar tijdsloten voor de komende 4 %s-avonden (%s–%s):",
            self.config["booking"]["day"],
            self.config["booking"]["time_start"],
            self.config["booking"]["time_end"],
        )
        for d in booking_dates:
            logger.info("  - %s", d.strftime("%d-%m-%Y"))

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
                    return False

                # Stap 2: probeer per datum en per club een tijdslot te vinden en te boeken
                for booking_date in booking_dates:
                    date_str = booking_date.strftime("%d-%m-%Y")
                    logger.info("Probeer datum: %s", date_str)
                    for club in clubs:
                        slot_info = self._find_timeslot(page, club, booking_date=booking_date)
                        if slot_info:
                            success = self._book_timeslot(page, slot_info)
                            if success:
                                return True
                            logger.warning(
                                "Boeking mislukt bij %s op %s, volgende club proberen...",
                                club["name"], date_str,
                            )

                logger.warning(
                    "Geen beschikbaar tijdslot gevonden voor alle %d datum(s) bij alle %d club(s)",
                    len(booking_dates), len(clubs),
                )
                return False

            except Exception as e:
                error_msg = f"Onverwachte fout: {e}"
                logger.exception(error_msg)
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
