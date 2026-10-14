"""
Meet & Play booking logica voor meetandplay.nl.
Gebruikt Playwright voor browser automatisering.
"""

import contextlib
import logging
import re
from datetime import datetime

from playwright.sync_api import Browser, BrowserContext, Locator, Page, sync_playwright

from providers.base import (
    ProviderResult,
    hhmm_to_minutes,
    upcoming_booking_dates,
    without_skip_dates,
)
from providers.meetandplay.session import SessionManager, accept_cookie_banner

logger = logging.getLogger(__name__)

PROVIDER = "meetandplay"
SEARCH_URL = "https://www.meetandplay.nl/zoeken"
PADEL_SPORT_ID = "2"

CHECKOUT_URL_KEYWORDS = ["reserveren", "winkelwagen", "payment", "checkout", "betaling", "betalen", "order", "bestelling"]
LIVEWIRE_DATE_RE = re.compile(r"window\.Livewire\.find\('([^']+)'\)\.set\('date'")
SLOT_LABEL_JS = """el => {
    let sibling = el.closest('.timeslots');
    if (!sibling) return '';
    let prev = sibling.previousElementSibling;
    return prev ? prev.innerText : '';
}"""


def _first_present(page: Page, selectors: list[str], visible: bool = False) -> Locator | None:
    """Geef de eerste locator uit `selectors` die op de pagina staat (en optioneel zichtbaar is)."""
    for selector in selectors:
        candidate = page.locator(selector)
        if candidate.count() > 0 and (not visible or candidate.first.is_visible()):
            return candidate
    return None


def _failure(error: str) -> ProviderResult:
    return ProviderResult(success=False, provider=PROVIDER, error=error)


class MeetAndPlayBooker:
    """Automatiseert het boeken van padelbanen op meetandplay.nl."""

    def __init__(self, request: dict):
        self._booking = request["booking_request"]
        self._credentials = request["credentials"]
        self._provider_config = request.get("provider_config", {})
        self._dry_run = request.get("dry_run", False)

        cookies_file = self._provider_config.get("cookies_file", ".meetandplay_cookies.json")
        self.session_manager = SessionManager(cookies_file)

    # ------------------------------------------------------------------
    # Datumberekening
    # ------------------------------------------------------------------

    def _get_upcoming_booking_dates(self, count: int = 3) -> list[datetime]:
        return upcoming_booking_dates(self._booking["day"], self._booking["time_start"], count)

    # ------------------------------------------------------------------
    # Browser setup
    # ------------------------------------------------------------------

    def _accept_cookies(self, page: Page) -> None:
        accept_cookie_banner(page)

    @staticmethod
    def _select_padel(page: Page) -> None:
        sport_select = page.locator("select#sportId")
        if sport_select.count() > 0 and not sport_select.is_disabled():
            sport_select.select_option(PADEL_SPORT_ID)
            page.wait_for_timeout(1500)

    @staticmethod
    def _set_livewire_date(page: Page, date_str: str) -> bool:
        """Zet de datum via het Livewire-component; False als het component ontbreekt."""
        lw_match = LIVEWIRE_DATE_RE.search(page.content())
        if not lw_match:
            return False
        page.evaluate(f"window.Livewire.find('{lw_match.group(1)}').set('date', '{date_str}')")
        page.wait_for_timeout(2500)
        return True

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

        if not (email and password):
            raise RuntimeError(
                "Geen credentials gevonden. Voeg KNLTB_EMAIL en KNLTB_PASSWORD toe aan .env"
            )

        logger.info("Credentials gevonden — automatisch inloggen...")
        new_context, success = self.session_manager.auto_login(browser, email, password)
        if not success:
            raise RuntimeError(
                "Automatisch inloggen mislukt. Controleer KNLTB_EMAIL/KNLTB_PASSWORD."
            )
        return new_context

    # ------------------------------------------------------------------
    # Zoeken naar clubs
    # ------------------------------------------------------------------

    def _search_clubs(self, page: Page, filter_on_date: bool = True) -> list[dict]:
        city = self._booking["location"]["city"]
        radius = str(self._booking["location"]["radius_km"])
        date_str = self._get_upcoming_booking_dates(count=1)[0].strftime("%d-%m-%Y")

        logger.info("Zoeken naar clubs in %s (straal %s km) op %s...", city, radius, date_str)

        page.goto(SEARCH_URL, wait_until="load", timeout=30000)
        page.wait_for_timeout(1500)
        self._accept_cookies(page)
        self._select_padel(page)

        loc_input = page.locator("input#location")
        loc_input.fill(city)
        loc_input.blur()
        page.wait_for_timeout(2500)

        page.locator("select#distance").select_option(radius)
        page.wait_for_timeout(1500)

        if self._booking.get("court_type", "indoor") == "indoor":
            page.locator("select#indoor").select_option("INDOOR")
            page.wait_for_timeout(1500)

        if filter_on_date and not self._set_livewire_date(page, date_str):
            logger.warning("Livewire datum-component niet gevonden op zoekpagina")

        cards = page.locator(".c-club-card.mp-club-card")
        count = cards.count()
        logger.info("%d club(s) gevonden na filteren", count)

        clubs = []
        for i in range(count):
            card = cards.nth(i)
            try:
                clubs.append({
                    "name": card.locator("h3").first.inner_text().strip(),
                    "address": card.locator(".c-club-card__address").first.inner_text().strip(),
                    "url": card.locator("a.mp-cta-link").first.get_attribute("href") or "",
                })
            except Exception as e:  # noqa: BLE001 — sla onleesbare kaart over
                logger.debug("Fout bij uitlezen club %d: %s", i, e)

        return clubs

    # ------------------------------------------------------------------
    # Tijdslot selecteren
    # ------------------------------------------------------------------

    def _apply_club_filters(self, page: Page, duration_minutes: int) -> None:
        self._select_padel(page)

        if self._booking.get("court_type", "indoor") == "indoor":
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

    def _label_skip_reason(self, label: str) -> str | None:
        """Geef een reden als het baanlabel niet past bij court_type/game_type."""
        court_type = self._booking.get("court_type", "indoor")
        game_type = self._booking.get("game_type", "double").lower()
        if court_type == "indoor" and "buiten" in label:
            return "buitenbaan"
        if game_type == "double" and "enkelspel" in label:
            return "enkelspel"
        if game_type == "single" and "dubbelspel" in label:
            return "dubbelspel"
        return None

    def _match_slot(self, slot: Locator, i: int, club: dict, duration_minutes: int) -> dict | None:
        """Geef slot-info terug als dit tijdslot aan alle voorwaarden voldoet, anders None."""
        try:
            label = slot.evaluate(SLOT_LABEL_JS).lower()
        except Exception:  # noqa: BLE001
            label = ""

        reason = self._label_skip_reason(label)
        if reason:
            logger.info("Slot %d overgeslagen: %s (%r)", i, reason, label)
            return None

        try:
            time_text = slot.locator(".timeslot-time").first.inner_text().strip()
            slot_start_str = time_text.split("–")[0].split("-")[0].strip().split("\n")[0].strip()[:5]
            slot_start_min = hhmm_to_minutes(slot_start_str)
        except Exception as e:  # noqa: BLE001
            logger.info("Slot %d overgeslagen: kon tijd niet lezen: %s", i, e)
            return None

        time_start, time_end = self._booking["time_start"], self._booking["time_end"]
        if not (hhmm_to_minutes(time_start) <= slot_start_min < hhmm_to_minutes(time_end)):
            logger.info(
                "Slot %d overgeslagen: tijd %s buiten venster %s–%s (label: %r)",
                i, slot_start_str, time_start, time_end, label
            )
            return None

        duration_match = re.search(r"(\d+)\s*min", time_text, re.IGNORECASE)
        if duration_match and int(duration_match.group(1)) != duration_minutes:
            logger.info(
                "Slot %d overgeslagen: duur %d min ≠ gewenste %d min (tijd: %s, label: %r)",
                i, int(duration_match.group(1)), duration_minutes, slot_start_str, label
            )
            return None

        try:
            court_name = slot.locator(".timeslot-name").first.inner_text().strip().split("\n")[0].strip()
        except Exception:  # noqa: BLE001
            court_name = "Onbekende baan"

        logger.info("Tijdslot gevonden: %s om %s (baan: %s)", club["name"], slot_start_str, court_name)
        return {
            "slot_id": slot.get_attribute("id") or "",
            "court_name": court_name,
            "time_range": time_text.replace("\n", " "),
            "club_name": club["name"],
            "club_address": club["address"],
        }

    def _find_timeslot(self, page: Page, club: dict, booking_date: datetime | None = None) -> dict | None:
        duration_minutes = int(self._booking.get("duration_minutes", 90))
        if booking_date is None:
            booking_date = self._get_upcoming_booking_dates(count=1)[0]
        date_str = booking_date.strftime("%d-%m-%Y")

        logger.info(
            "Controleren tijdsloten bij %s voor %s (%s–%s)...",
            club["name"], date_str, self._booking["time_start"], self._booking["time_end"]
        )

        page.goto(club["url"], wait_until="load", timeout=60000)
        page.wait_for_timeout(1500)
        self._accept_cookies(page)
        self._apply_club_filters(page, duration_minutes)
        self._set_livewire_date(page, date_str)

        slots = page.locator(".timeslot-container a.timeslot")
        logger.info("%d tijdslot(en) gevonden bij %s", slots.count(), club["name"])

        for i in range(slots.count()):
            slot_info = self._match_slot(slots.nth(i), i, club, duration_minutes)
            if slot_info:
                return slot_info

        logger.info("Geen geschikt tijdslot gevonden bij %s", club["name"])
        return None

    # ------------------------------------------------------------------
    # Boeking afronden
    # ------------------------------------------------------------------

    def _add_to_cart(self, page: Page, slot_id: str) -> bool:
        slot_anchor = _first_present(page, [f"a.timeslot[id='{slot_id}']", f"a[id='{slot_id}']"])
        if slot_anchor is None:
            logger.warning("Tijdslot-anker niet gevonden voor id %s", slot_id)
            return False

        slot_anchor.first.click()
        page.wait_for_timeout(2000)

        add_btn = _first_present(page, [
            'button:has-text("Toevoegen")',
            'button:has-text("Reserveren")',
            'button:has-text("Boeken")',
        ])
        if add_btn is None:
            logger.warning("'Toevoegen'/'Reserveren'/'Boeken'-knop niet gevonden na klik op tijdslot %s", slot_id)
            return False

        logger.info("'%s'-knop gevonden, klikken...", add_btn.first.inner_text().strip())
        add_btn.first.click()
        page.wait_for_timeout(2500)
        return True

    def _checkout(self, page: Page, slot_id: str) -> bool:
        """Klik 'Afrekenen' en controleer of de winkelwagen/betaalpagina bereikt is."""
        checkout_btn = None
        for selector in [
            f'button[wire\\:click="checkout({slot_id})"]:visible',
            'button[wire\\:click="checkout"]:visible',
            'button:visible:has-text("Afrekenen")',
            'a:visible:has-text("Afrekenen")',
        ]:
            if page.locator(selector).count() > 0:
                checkout_btn = page.locator(selector)
                logger.info("'Afrekenen'-knop gevonden via selector: %s", selector)
                break

        if checkout_btn is None:
            logger.warning("'Afrekenen'-knop niet gevonden na toevoegen")
            return False

        logger.info("'Afrekenen'-knop klikken...")
        checkout_btn.first.click()

        with contextlib.suppress(Exception):
            page.wait_for_url(
                lambda url: any(kw in url.lower() for kw in CHECKOUT_URL_KEYWORDS),
                timeout=15000,
            )
        page.wait_for_timeout(2000)

        current_url = page.url
        logger.info("URL na afrekenen: %s", current_url)

        if "inloggen" in current_url:
            logger.error("Doorgestuurd naar loginpagina tijdens boeking — sessie verlopen")
            return False

        try:
            page_html = page.content()
        except Exception:  # noqa: BLE001
            page_html = ""

        on_cart = "Winkelwagen" in page_html or any(kw in current_url.lower() for kw in CHECKOUT_URL_KEYWORDS)
        if not on_cart:
            logger.warning("Winkelwagen/betalingspagina niet bereikt. Huidige URL: %s", current_url)
            with contextlib.suppress(Exception):
                page.screenshot(path="debug_after_checkout.png", full_page=True)
        return on_cart

    @staticmethod
    def _fetch_payment_url(page: Page) -> str:
        """Accepteer de voorwaarden en klik door naar de betaalprovider; geef de resulterende URL terug."""
        logger.info("Winkelwagen bereikt — voorwaarden accepteren en betaallink ophalen...")
        current_url = page.url

        tos_checkbox = page.locator("input#tos")
        try:
            tos_checkbox.wait_for(state="visible", timeout=10000)
        except Exception:  # noqa: BLE001
            logger.warning("TOS checkbox niet gevonden binnen 10s")
        if tos_checkbox.count() > 0 and not tos_checkbox.is_checked():
            page.evaluate("document.querySelector('input#tos').click()")
            page.wait_for_timeout(1000)
            logger.info("Voorwaarden geaccepteerd")

        pay_btn = page.locator('button:has-text("Betaling starten")')
        if pay_btn.count() == 0:
            pay_btn = _first_present(
                page,
                ['button:has-text("Betalen")', 'button:has-text("Nu betalen")', 'a:has-text("Betalen")'],
                visible=True,
            ) or pay_btn

        if pay_btn.count() == 0 or not pay_btn.first.is_visible():
            logger.warning("Betaalknop niet gevonden — gebruik checkout-URL als fallback")
            return current_url

        logger.info("Betaalknop gevonden, klikken om betaalprovider-URL op te halen...")
        pay_btn.first.click()
        with contextlib.suppress(Exception):
            page.wait_for_url(lambda url: url != current_url, timeout=20000)
        page.wait_for_timeout(2000)
        logger.info("Betaalprovider-URL: %s", page.url)
        return page.url

    def _book_timeslot(self, page: Page, slot_info: dict) -> bool:
        slot_id = slot_info["slot_id"]
        logger.info(
            "Tijdslot %s toevoegen aan winkelwagen (%s bij %s)...",
            slot_id, slot_info["time_range"], slot_info["club_name"]
        )
        if not (self._add_to_cart(page, slot_id) and self._checkout(page, slot_id)):
            return False
        slot_info["payment_url"] = self._fetch_payment_url(page)
        return True

    # ------------------------------------------------------------------
    # Hoofdflow
    # ------------------------------------------------------------------

    def _try_dates_and_clubs(self, page: Page, booking_dates: list[datetime], clubs: list[dict]) -> ProviderResult:
        for booking_date in booking_dates:
            date_str = booking_date.strftime("%d-%m-%Y")
            logger.info("Probeer datum: %s", date_str)
            for club in clubs:
                try:
                    slot_info = self._find_timeslot(page, club, booking_date=booking_date)
                except Exception as club_err:  # noqa: BLE001 — één club mag de zoektocht niet afbreken
                    logger.warning("Fout bij %s op %s: %s", club["name"], date_str, club_err)
                    continue

                if not slot_info:
                    continue

                if self._dry_run:
                    logger.info("Dry-run: tijdslot gevonden maar boeking overgeslagen")
                    return _failure("dry_run — tijdslot gevonden maar niet geboekt")

                if self._book_timeslot(page, slot_info):
                    return ProviderResult(
                        success=True,
                        provider=PROVIDER,
                        booked_date=booking_date.strftime("%Y-%m-%d"),
                        slot_info={
                            key: slot_info.get(key, "")
                            for key in ("club_name", "club_address", "court_name", "time_range", "payment_url")
                        },
                    )
                logger.warning(
                    "Boeking mislukt bij %s op %s, volgende club proberen...", club["name"], date_str,
                )

        return _failure(f"Geen tijdslot gevonden voor {len(booking_dates)} datum(s) bij {len(clubs)} club(s)")

    def run(self) -> ProviderResult:
        logger.info("=" * 60)
        logger.info("Meet & Play provider gestart")
        logger.info("=" * 60)

        booking_dates = without_skip_dates(
            self._get_upcoming_booking_dates(count=self._booking.get("weeks_ahead", 4)),
            self._booking.get("skip_dates", []),
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
            return _failure("Alle doeldata al geboekt (skip_booked_dates)")

        with sync_playwright() as pw:
            browser = None
            context = None
            try:
                browser = pw.chromium.launch(headless=True)
                context = self._ensure_logged_in(browser, self.session_manager.new_context(browser))
                page = context.new_page()

                clubs = self._search_clubs(page)
                if not clubs:
                    logger.warning("Geen clubs gevonden met de opgegeven filters")
                    return _failure("Geen clubs gevonden")

                return self._try_dates_and_clubs(page, booking_dates, clubs)

            except Exception as e:  # noqa: BLE001 — subprocess moet altijd een ProviderResult geven
                logger.exception("Onverwachte fout in Meet & Play provider")
                return _failure(str(e))

            finally:
                for resource in (context, browser):
                    if resource:
                        with contextlib.suppress(Exception):
                            resource.close()
