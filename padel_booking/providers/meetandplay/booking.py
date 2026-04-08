"""
Meet & Play booking logica voor meetandplay.nl.
Gebruikt Playwright voor browser automatisering.
"""

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from providers.base import ProviderResult, SlotInfo
from providers.meetandplay.session import SessionManager

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.meetandplay.nl/zoeken"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SPORT_IDS = {
    "tennis": "1",
    "padel": "2",
    "squash": "4",
    "pickleball": "13",
}

WEEKDAYS = {
    "monday": 0, "maandag": 0,
    "tuesday": 1, "dinsdag": 1,
    "wednesday": 2, "woensdag": 2,
    "thursday": 3, "donderdag": 3,
    "friday": 4, "vrijdag": 4,
    "saturday": 5, "zaterdag": 5,
    "sunday": 6, "zondag": 6,
}


class MeetAndPlayBooker:
    """Automatiseert het boeken van padelbanen op meetandplay.nl."""

    def __init__(self, request: dict):
        self._request = request
        self._booking = request["booking_request"]
        self._credentials = request["credentials"]
        self._provider_config = request.get("provider_config", {})
        self._dry_run = request.get("dry_run", False)

        cookies_file = self._provider_config.get("cookies_file", ".meetandplay_cookies.json")
        self.session_manager = SessionManager(cookies_file)
        self._playwright = None

    # ------------------------------------------------------------------
    # Datumberekening
    # ------------------------------------------------------------------

    def _get_upcoming_booking_dates(self, count: int = 3) -> list:
        target_day_name = self._booking["day"].lower()
        target_weekday = WEEKDAYS.get(target_day_name)
        if target_weekday is None:
            raise ValueError(f"Ongeldige dag in config: {target_day_name}")

        time_start_str = self._booking["time_start"]
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

    def _make_context(self, browser: Browser) -> BrowserContext:
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=USER_AGENT,
        )
        if self.session_manager.cookies_exist():
            logger.info("Bestaande sessie laden uit cookies...")
            self.session_manager.load_cookies(context)
        return context

    def _accept_cookies(self, page: Page) -> None:
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

    def _ensure_logged_in(self, browser: Browser, context: BrowserContext) -> BrowserContext:
        page = context.new_page()
        logged_in = self.session_manager.is_logged_in(page)
        page.close()

        if logged_in:
            return context

        context.close()

        email = self._credentials.get("email", "").strip()
        password = self._credentials.get("password", "").strip()

        if email and password:
            logger.info("Credentials gevonden — automatisch inloggen...")
            new_context, success = self.session_manager.auto_login(browser, email, password)
            if success:
                return new_context
            raise RuntimeError(
                "Automatisch inloggen mislukt. Controleer KNLTB_EMAIL/KNLTB_PASSWORD."
            )

        raise RuntimeError(
            "Geen credentials gevonden. Voeg KNLTB_EMAIL en KNLTB_PASSWORD toe aan .env"
        )

    # ------------------------------------------------------------------
    # Zoeken naar clubs
    # ------------------------------------------------------------------

    def _search_clubs(self, page: Page) -> list[dict]:
        city = self._booking["location"]["city"]
        radius = str(self._booking["location"]["radius_km"])
        booking_date = self._get_upcoming_booking_dates(count=1)[0]
        date_str = booking_date.strftime("%d-%m-%Y")

        logger.info("Zoeken naar clubs in %s (straal %s km) op %s...", city, radius, date_str)

        page.goto(SEARCH_URL, wait_until="load", timeout=30000)
        page.wait_for_timeout(1500)
        self._accept_cookies(page)

        sport_select = page.locator("select#sportId")
        if sport_select.count() > 0 and not sport_select.is_disabled():
            sport_select.select_option("2")
            page.wait_for_timeout(1500)

        loc_input = page.locator("input#location")
        loc_input.fill(city)
        loc_input.blur()
        page.wait_for_timeout(2500)

        page.locator("select#distance").select_option(radius)
        page.wait_for_timeout(1500)

        court_type = self._booking.get("court_type", "indoor")
        if court_type == "indoor":
            page.locator("select#indoor").select_option("INDOOR")
            page.wait_for_timeout(1500)

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
        time_start = self._booking["time_start"]
        time_end = self._booking["time_end"]
        duration_minutes = int(self._booking.get("duration_minutes", 90))
        if booking_date is None:
            booking_date = self._get_upcoming_booking_dates(count=1)[0]
        date_str = booking_date.strftime("%d-%m-%Y")
        game_type = self._booking.get("game_type", "double").lower()

        logger.info(
            "Controleren tijdsloten bij %s voor %s (%s–%s)...",
            club["name"], date_str, time_start, time_end
        )

        page.goto(club["url"], wait_until="load", timeout=60000)
        page.wait_for_timeout(1500)
        self._accept_cookies(page)

        sport_select = page.locator("select#sportId")
        if sport_select.count() > 0 and not sport_select.is_disabled():
            sport_select.select_option("2")
            page.wait_for_timeout(1500)

        court_type = self._booking.get("court_type", "indoor")
        if court_type == "indoor":
            indoor_select = page.locator("select#indoor")
            if indoor_select.count() > 0:
                indoor_select.select_option("INDOOR")
                page.wait_for_timeout(1500)

        daypart_select = page.locator("select#dayPart")
        if daypart_select.count() > 0:
            daypart_select.select_option("evening")
            page.wait_for_timeout(1500)

        duration_select = page.locator("select#duration")
        if duration_select.count() > 0:
            duration_select.select_option(str(duration_minutes))
            page.wait_for_timeout(1500)
            logger.info("Duur ingesteld op %d minuten", duration_minutes)

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

        start_h, start_m = map(int, time_start.split(":"))
        end_h, end_m = map(int, time_end.split(":"))
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m

        slots = page.locator(".timeslot-container a.timeslot")
        logger.info("%d tijdslot(en) gevonden bij %s", slots.count(), club["name"])

        for i in range(slots.count()):
            slot = slots.nth(i)

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

            if court_type == "indoor" and "buiten" in court_type_label:
                logger.info("Slot %d overgeslagen: buitenbaan (%r)", i, court_type_label)
                continue
            if game_type == "double" and "enkelspel" in court_type_label:
                logger.info("Slot %d overgeslagen: enkelspel (%r)", i, court_type_label)
                continue
            if game_type == "single" and "dubbelspel" in court_type_label:
                logger.info("Slot %d overgeslagen: dubbelspel (%r)", i, court_type_label)
                continue

            try:
                time_text = slot.locator(".timeslot-time").first.inner_text().strip()
                slot_start_str = time_text.split("–")[0].split("-")[0].strip().split("\n")[0].strip()
                slot_start_str = slot_start_str[:5]
                sh, sm = map(int, slot_start_str.split(":"))
                slot_start_min = sh * 60 + sm
            except Exception as e:
                logger.info("Slot %d overgeslagen: kon tijd niet lezen: %s", i, e)
                continue

            if not (start_minutes <= slot_start_min < end_minutes):
                logger.info(
                    "Slot %d overgeslagen: tijd %s buiten venster %s–%s (label: %r)",
                    i, slot_start_str, time_start, time_end, court_type_label
                )
                continue

            duration_match = re.search(r"(\d+)\s*min", time_text, re.IGNORECASE)
            if duration_match:
                slot_duration = int(duration_match.group(1))
                if slot_duration != duration_minutes:
                    logger.info(
                        "Slot %d overgeslagen: duur %d min ≠ gewenste %d min (tijd: %s, label: %r)",
                        i, slot_duration, duration_minutes, slot_start_str, court_type_label
                    )
                    continue

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
        slot_id = slot_info["slot_id"]

        logger.info(
            "Tijdslot %s toevoegen aan winkelwagen (%s bij %s)...",
            slot_id, slot_info["time_range"], slot_info["club_name"]
        )

        slot_anchor = page.locator(f"a.timeslot[id='{slot_id}']")
        if slot_anchor.count() == 0:
            slot_anchor = page.locator(f"a[id='{slot_id}']")
        if slot_anchor.count() == 0:
            logger.warning("Tijdslot-anker niet gevonden voor id %s", slot_id)
            return False

        slot_anchor.first.click()
        page.wait_for_timeout(2000)

        add_btn = page.locator('button:has-text("Toevoegen")')
        if add_btn.count() == 0:
            add_btn = page.locator('button:has-text("Reserveren")')
        if add_btn.count() == 0:
            add_btn = page.locator('button:has-text("Boeken")')
        if add_btn.count() == 0:
            logger.warning("'Toevoegen'/'Reserveren'/'Boeken'-knop niet gevonden na klik op tijdslot %s", slot_id)
            return False

        logger.info("'%s'-knop gevonden, klikken...", add_btn.first.inner_text().strip())
        add_btn.first.click()
        page.wait_for_timeout(2500)

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
            return False

        logger.info("'Afrekenen'-knop klikken...")
        checkout_btn.first.click()

        checkout_url_keywords = ["reserveren", "winkelwagen", "payment", "checkout", "betaling", "betalen", "order", "bestelling"]
        try:
            page.wait_for_url(
                lambda url: any(kw in url.lower() for kw in checkout_url_keywords),
                timeout=15000,
            )
        except Exception:
            pass
        page.wait_for_timeout(2000)

        current_url = page.url
        logger.info("URL na afrekenen: %s", current_url)

        if "inloggen" in current_url:
            logger.error("Doorgestuurd naar loginpagina tijdens boeking — sessie verlopen")
            return False

        try:
            page_html = page.content()
        except Exception:
            page_html = ""

        on_cart = (
            "winkelwagen" in current_url.lower()
            or "Winkelwagen" in page_html
        )

        if not on_cart:
            payment_keywords = ["payment", "checkout", "betaling", "betalen", "order", "bestelling", "reserveren"]
            if any(kw in current_url.lower() for kw in payment_keywords):
                on_cart = True

        if not on_cart:
            logger.warning("Winkelwagen/betalingspagina niet bereikt. Huidige URL: %s", current_url)
            try:
                page.screenshot(path="debug_after_checkout.png", full_page=True)
            except Exception:
                pass
            return False

        logger.info("Winkelwagen bereikt — voorwaarden accepteren en betaallink ophalen...")

        tos_checkbox = page.locator("input#tos")
        try:
            tos_checkbox.wait_for(state="visible", timeout=10000)
        except Exception:
            logger.warning("TOS checkbox niet gevonden binnen 10s")
        if tos_checkbox.count() > 0 and not tos_checkbox.is_checked():
            page.evaluate("document.querySelector('input#tos').click()")
            page.wait_for_timeout(1000)
            logger.info("Voorwaarden geaccepteerd")

        pay_btn = page.locator('button:has-text("Betaling starten")')
        if pay_btn.count() == 0:
            for sel in ['button:has-text("Betalen")', 'button:has-text("Nu betalen")', 'a:has-text("Betalen")']:
                candidate = page.locator(sel)
                if candidate.count() > 0 and candidate.first.is_visible():
                    pay_btn = candidate
                    break

        if pay_btn.count() > 0 and pay_btn.first.is_visible():
            logger.info("Betaalknop gevonden, klikken om betaalprovider-URL op te halen...")
            pay_btn.first.click()
            try:
                page.wait_for_url(
                    lambda url: url != current_url,
                    timeout=20000,
                )
            except Exception:
                pass
            page.wait_for_timeout(2000)
            current_url = page.url
            logger.info("Betaalprovider-URL: %s", current_url)
        else:
            logger.warning("Betaalknop niet gevonden — gebruik checkout-URL als fallback")

        slot_info["payment_url"] = current_url
        return True

    # ------------------------------------------------------------------
    # Hoofdflow
    # ------------------------------------------------------------------

    def run(self) -> ProviderResult:
        logger.info("=" * 60)
        logger.info("Meet & Play provider gestart")
        logger.info("=" * 60)

        weeks_ahead = self._booking.get("weeks_ahead", 4)
        booking_dates = self._get_upcoming_booking_dates(count=weeks_ahead)

        skip_dates = set(self._booking.get("skip_dates", []))
        if skip_dates:
            before = len(booking_dates)
            booking_dates = [d for d in booking_dates if d.strftime("%Y-%m-%d") not in skip_dates]
            logger.info(
                "skip_booked_dates: %d datum(s) overgeslagen %s",
                before - len(booking_dates),
                sorted(skip_dates),
            )

        logger.info(
            "Zoeken naar tijdsloten voor de komende %d %s-avonden (%s–%s):",
            len(booking_dates),
            self._booking["day"],
            self._booking["time_start"],
            self._booking["time_end"],
        )
        for d in booking_dates:
            logger.info("  - %s", d.strftime("%d-%m-%Y"))

        if not booking_dates:
            logger.info("Alle doeldata al geboekt — geen verdere actie nodig")
            return ProviderResult(
                success=False,
                provider="meetandplay",
                error="Alle doeldata al geboekt (skip_booked_dates)",
            )

        browser = None
        context = None

        with sync_playwright() as pw:
            self._playwright = pw
            try:
                browser = pw.chromium.launch(headless=True)
                context = self._make_context(browser)
                context = self._ensure_logged_in(browser, context)

                page = context.new_page()

                clubs = self._search_clubs(page)
                if not clubs:
                    logger.warning("Geen clubs gevonden met de opgegeven filters")
                    return ProviderResult(
                        success=False,
                        provider="meetandplay",
                        error="Geen clubs gevonden",
                    )

                for booking_date in booking_dates:
                    date_str = booking_date.strftime("%d-%m-%Y")
                    logger.info("Probeer datum: %s", date_str)
                    for club in clubs:
                        try:
                            slot_info = self._find_timeslot(page, club, booking_date=booking_date)
                        except Exception as club_err:
                            logger.warning(
                                "Fout bij %s op %s: %s", club["name"], date_str, club_err
                            )
                            continue

                        if slot_info:
                            if self._dry_run:
                                logger.info("Dry-run: tijdslot gevonden maar boeking overgeslagen")
                                return ProviderResult(
                                    success=False,
                                    provider="meetandplay",
                                    error="dry_run — tijdslot gevonden maar niet geboekt",
                                )

                            success = self._book_timeslot(page, slot_info)
                            if success:
                                return ProviderResult(
                                    success=True,
                                    provider="meetandplay",
                                    booked_date=booking_date.strftime("%Y-%m-%d"),
                                    slot_info={
                                        "club_name": slot_info["club_name"],
                                        "club_address": slot_info["club_address"],
                                        "court_name": slot_info["court_name"],
                                        "time_range": slot_info["time_range"],
                                        "payment_url": slot_info.get("payment_url", ""),
                                    },
                                )
                            logger.warning(
                                "Boeking mislukt bij %s op %s, volgende club proberen...",
                                club["name"], date_str,
                            )

                return ProviderResult(
                    success=False,
                    provider="meetandplay",
                    error=f"Geen tijdslot gevonden voor {len(booking_dates)} datum(s) bij {len(clubs)} club(s)",
                )

            except Exception as e:
                logger.exception("Onverwachte fout in Meet & Play provider")
                return ProviderResult(
                    success=False,
                    provider="meetandplay",
                    error=str(e),
                )

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
