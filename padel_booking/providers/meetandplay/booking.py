"""
Meet & Play booking logica voor meetandplay.nl.
Gebruikt Playwright voor browser automatisering.
"""

import contextlib
import logging
import re
from datetime import datetime
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Locator, Page, sync_playwright

from providers.base import (
    ProviderResult,
    booking_days,
    hhmm_to_minutes,
    upcoming_booking_dates,
    without_skip_dates,
)
from providers.meetandplay.session import SessionManager, accept_cookie_banner, save_debug_page

logger = logging.getLogger(__name__)

PROVIDER = "meetandplay"
SEARCH_URL = "https://www.meetandplay.nl/zoeken"
PADEL_SPORT_ID = "2"

START_TIME_SELECTOR = 'input[name="time"]'
# Enkelspel staat soms alleen in de baannaam, bijv. "Padelhal | Baan 5 (SINGLE | max. 2 spelers)"
SINGLES_MARKERS = ("enkelspel", "single", "max. 2 spelers", "max 2 spelers")
CHECKOUT_URL_KEYWORDS = ["reserveren", "winkelwagen", "payment", "checkout", "betaling", "betalen", "order", "bestelling"]
PAY_BUTTON_SELECTORS = [
    'button:has-text("Betaling starten")',
    'button:has-text("Nu betalen")',
    'button:has-text("Betalen")',
    'a:has-text("Betalen")',
]
PAY_ATTEMPTS = 2
LIVEWIRE_DATE_RE = re.compile(r"window\.Livewire\.find\('([^']+)'\)\.set\('date'")
SLOT_LABEL_JS = """el => {
    let sibling = el.closest('.timeslots');
    if (!sibling) return '';
    let prev = sibling.previousElementSibling;
    return prev ? prev.innerText : '';
}"""


def _is_payment_url(url: str) -> bool:
    """Betaalpagina = alles buiten meetandplay.nl zelf (iDEAL, de boekingssite van de club, …)."""
    host = urlparse(url).netloc.lower()
    return bool(host) and host not in ("meetandplay.nl", "www.meetandplay.nl")


def _first_present(page: Page, selectors: list[str], visible: bool = False) -> Locator | None:
    """Geef de eerste locator uit `selectors` die op de pagina staat (en optioneel zichtbaar is)."""
    for selector in selectors:
        candidate = page.locator(selector)
        if candidate.count() > 0 and (not visible or candidate.first.is_visible()):
            return candidate
    return None


class PendingReservationError(Exception):
    """Deze sessie heeft al een openstaande (onbetaalde) reservering bij een andere club."""


def _failure(error: str) -> ProviderResult:
    return ProviderResult(success=False, provider=PROVIDER, error=error)


class MeetAndPlayBooker:
    """Automatiseert het boeken van padelbanen op meetandplay.nl."""

    def __init__(self, request: dict):
        self._booking = request["booking_request"]
        self._credentials = request["credentials"]
        self._provider_config = request.get("provider_config", {})
        self._dry_run = request.get("dry_run", False)
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

        cookies_file = self._provider_config.get("cookies_file", ".meetandplay_cookies.json")
        self.session_manager = SessionManager(cookies_file)

    # ------------------------------------------------------------------
    # Datumberekening
    # ------------------------------------------------------------------

    def _get_upcoming_booking_dates(self, count: int = 3) -> list[datetime]:
        return upcoming_booking_dates(booking_days(self._booking), self._booking["time_start"], count)

    def _candidate_dates(self) -> list[datetime]:
        """Op te zoeken datums: de opgegeven datums bij een gerichte run, anders de komende weken."""
        only_dates = self._booking.get("only_dates")
        if only_dates:
            return [datetime.fromisoformat(d) for d in only_dates]
        return self._get_upcoming_booking_dates(count=self._booking.get("weeks_ahead", 4))

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

    def _fresh_session_page(self) -> Page:
        """Nieuwe sessie (opnieuw inloggen zonder cookies): een eigen, lege reservering."""
        logger.info("Nieuwe sessie starten zodat de openstaande reservering behouden blijft...")
        if self._context:
            with contextlib.suppress(Exception):
                self._context.close()
        self._context = None
        email = self._credentials.get("email", "").strip()
        password = self._credentials.get("password", "").strip()
        context, success = self.session_manager.auto_login(self._browser, email, password)
        if not success:
            raise RuntimeError("Opnieuw inloggen voor een nieuwe sessie mislukt")
        self._context = context
        return context.new_page()

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

        self._select_duration(page, duration_minutes)

    @staticmethod
    def _select_duration(page: Page, duration_minutes: int) -> None:
        """Zet het duurfilter: knoppen ("60 minuten") op de huidige pagina, dropdown op de oude."""
        duration_select = page.locator("select#duration")
        if duration_select.count() > 0:
            duration_select.select_option(str(duration_minutes))
            page.wait_for_timeout(1500)
            logger.info("Duur ingesteld op %d minuten", duration_minutes)
            return

        pill = page.locator(f'.pill[wire\\:click="toggleDuration({duration_minutes})"]')
        if pill.count() == 0:
            logger.info("Geen duurfilter voor %d minuten op deze pagina — duur wordt per slot gecontroleerd", duration_minutes)
            return
        if "inactive" in (pill.first.get_attribute("class") or ""):
            pill.first.click()
            page.wait_for_timeout(1500)
        logger.info("Duur ingesteld op %d minuten", duration_minutes)

    @staticmethod
    def _available_start_times(page: Page) -> list[str] | None:
        """
        Starttijden uit de "Tijdstip"-kiezer — precies de tijden die die dag nog beschikbaar zijn.
        None als de pagina geen kiezer heeft (dan staan alle slots direct op de pagina).
        """
        radios = page.locator(START_TIME_SELECTOR)
        if radios.count() == 0:
            return None
        return radios.evaluate_all("els => els.map(e => e.value)")

    @staticmethod
    def _select_start_time(page: Page, start_time: str) -> None:
        # De radio zelf is visueel verborgen (Bootstrap btn-check); klik het bijbehorende label
        page.locator(f'label[for="time-{start_time}"]').click()
        page.wait_for_timeout(2000)

    def _match_listed_slots(self, page: Page, club: dict, duration_minutes: int) -> dict | None:
        slots = page.locator(".timeslot-container a.timeslot")
        logger.info("%d tijdslot(en) gevonden bij %s", slots.count(), club["name"])
        for i in range(slots.count()):
            slot_info = self._match_slot(slots.nth(i), i, club, duration_minutes)
            if slot_info:
                return slot_info
        return None

    def _label_skip_reason(self, label: str, court_name: str = "") -> str | None:
        """Geef een reden als het baanlabel of de baannaam niet past bij court_type/game_type."""
        court_type = self._booking.get("court_type", "indoor")
        game_type = self._booking.get("game_type", "double").lower()
        if court_type == "indoor" and "buiten" in label:
            return "buitenbaan"
        if game_type == "double" and any(m in f"{label} {court_name.lower()}" for m in SINGLES_MARKERS):
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

        try:
            court_name = slot.locator(".timeslot-name").first.inner_text().strip().split("\n")[0].strip()
        except Exception:  # noqa: BLE001
            court_name = "Onbekende baan"

        reason = self._label_skip_reason(label, court_name)
        if reason:
            logger.info("Slot %d overgeslagen: %s (%r, baan: %r)", i, reason, label, court_name)
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

        start_times = self._available_start_times(page)
        if start_times is None:
            slot_info = self._match_listed_slots(page, club, duration_minutes)
        else:
            # De slotlijst toont alleen de geselecteerde starttijd: loop alle tijden in het venster af
            window_start = hhmm_to_minutes(self._booking["time_start"])
            window_end = hhmm_to_minutes(self._booking["time_end"])
            in_window = [t for t in start_times if window_start <= hhmm_to_minutes(t) < window_end]
            logger.info(
                "Beschikbare starttijden bij %s: %s — binnen venster: %s",
                club["name"], ", ".join(start_times) or "geen", ", ".join(in_window) or "geen",
            )
            slot_info = None
            for start_time in in_window:
                self._select_start_time(page, start_time)
                slot_info = self._match_listed_slots(page, club, duration_minutes)
                if slot_info:
                    break

        if not slot_info:
            logger.info("Geen geschikt tijdslot gevonden bij %s", club["name"])
        return slot_info

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

        # Elk slot heeft een eigen knop; klik die van dit slot, niet de eerste op de pagina
        add_selector = 'button:has-text("Toevoegen"), button:has-text("Reserveren"), button:has-text("Boeken")'
        own_btn = slot_anchor.first.locator(add_selector)
        add_btn = own_btn if own_btn.count() > 0 else _first_present(page, add_selector.split(", "))
        if add_btn is None:
            logger.warning("'Toevoegen'/'Reserveren'/'Boeken'-knop niet gevonden na klik op tijdslot %s", slot_id)
            return False

        logger.info("'%s'-knop gevonden, klikken...", add_btn.first.inner_text().strip())
        add_btn.first.click()
        page.wait_for_timeout(2500)
        self._raise_if_pending_reservation(page)
        return True

    @staticmethod
    def _raise_if_pending_reservation(page: Page) -> None:
        """
        Meet & Play staat per sessie één openstaande reservering toe. Staat er al een (bijv. van een
        eerdere boeking die nog betaald moet worden), dan verschijnt "Je reservering bij …" met
        Annuleren / Doorgaan. Doorgaan laat de andere reservering vervallen — dus annuleren.
        """
        erase_btn = page.locator('button[wire\\:click="erase"]')
        if erase_btn.count() == 0 or not erase_btn.first.is_visible():
            return
        logger.info("Openstaande reservering in deze sessie — pop-up annuleren (andere reservering blijft staan)")
        with contextlib.suppress(Exception):
            page.locator('.modal.active button[wire\\:click="hide"]').first.click()
            page.wait_for_timeout(1000)
        raise PendingReservationError("openstaande reservering in deze sessie")

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
        page.wait_for_timeout(1000)
        self._raise_if_pending_reservation(page)

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
            save_debug_page(page, "debug_after_checkout")
        return on_cart

    @staticmethod
    def _accept_terms(page: Page) -> None:
        tos_checkbox = page.locator("input#tos")
        try:
            tos_checkbox.wait_for(state="visible", timeout=10000)
        except Exception:  # noqa: BLE001
            logger.warning("TOS checkbox niet gevonden binnen 10s")
            return
        if tos_checkbox.is_checked():
            return
        # Echte klik (met events) zodat Livewire het vinkje oppikt; JS-klik als terugval
        try:
            tos_checkbox.check(timeout=5000)
        except Exception:  # noqa: BLE001
            page.evaluate("document.querySelector('input#tos').click()")
        page.wait_for_timeout(1500)
        logger.info("Voorwaarden geaccepteerd (aangevinkt: %s)", tos_checkbox.is_checked())

    @staticmethod
    def _payment_page_url(page: Page) -> str:
        """URL van de betaalpagina — in dit tabblad of in een nieuw geopend tabblad — of ""."""
        if _is_payment_url(page.url):
            return page.url
        with contextlib.suppress(Exception):
            for other in page.context.pages:
                if other is not page and _is_payment_url(other.url):
                    return other.url
        return ""

    def _fetch_payment_url(self, page: Page) -> str:
        """
        Accepteer de voorwaarden en klik door naar de betaalprovider. Geeft de betaal-URL terug,
        of "" als de betaalpagina niet bereikt is (de winkelwagen zit dan alleen in de sessie van de bot).
        """
        logger.info("Winkelwagen bereikt — voorwaarden accepteren en betaallink ophalen...")
        self._accept_terms(page)

        for attempt in range(1, PAY_ATTEMPTS + 1):
            pay_btn = _first_present(page, PAY_BUTTON_SELECTORS, visible=True)
            if pay_btn is None:
                logger.warning("Betaalknop niet gevonden (poging %d)", attempt)
                break
            logger.info("'%s' klikken (poging %d)...", pay_btn.first.inner_text().strip(), attempt)
            pay_btn.first.click()
            with contextlib.suppress(Exception):
                page.wait_for_url(_is_payment_url, timeout=30000)
            page.wait_for_timeout(2000)
            if url := self._payment_page_url(page):
                logger.info("Betaalprovider-URL: %s", url)
                return url
            logger.warning("Betaalpagina niet bereikt na klik (URL: %s)", page.url)
            self._accept_terms(page)

        save_debug_page(page, "debug_payment_failed")
        return ""

    def _book_timeslot(self, page: Page, slot_info: dict) -> bool:
        slot_id = slot_info["slot_id"]
        logger.info(
            "Tijdslot %s toevoegen aan winkelwagen (%s bij %s)...",
            slot_id, slot_info["time_range"], slot_info["club_name"]
        )
        if not (self._add_to_cart(page, slot_id) and self._checkout(page, slot_id)):
            return False
        slot_info["payment_url"] = self._fetch_payment_url(page)
        if not slot_info["payment_url"]:
            logger.warning("Geen betaallink — slot telt niet als geboekt")
            return False
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

                try:
                    booked = self._book_timeslot(page, slot_info)
                except PendingReservationError:
                    page = self._fresh_session_page()
                    slot_info = self._find_timeslot(page, club, booking_date=booking_date)
                    booked = bool(slot_info) and self._book_timeslot(page, slot_info)

                if booked:
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

        booking_dates = without_skip_dates(self._candidate_dates(), self._booking.get("skip_dates", []))

        logger.info(
            "Zoeken naar tijdsloten voor de komende %d %s-avonden (%s–%s):",
            len(booking_dates),
            "/".join(booking_days(self._booking)),
            self._booking["time_start"],
            self._booking["time_end"],
        )
        for d in booking_dates:
            logger.info("  - %s", d.strftime("%d-%m-%Y"))

        if not booking_dates:
            logger.info("Alle doeldata al geboekt — geen verdere actie nodig")
            return _failure("Alle doeldata al geboekt (skip_booked_dates)")

        with sync_playwright() as pw:
            try:
                self._browser = pw.chromium.launch(headless=True)
                self._context = self._ensure_logged_in(self._browser, self.session_manager.new_context(self._browser))
                page = self._context.new_page()

                # Gerichte run (scheduler): direct naar de opgegeven clubs, zonder zoekpagina
                clubs = self._booking.get("clubs") or self._search_clubs(page)
                if not clubs:
                    logger.warning("Geen clubs gevonden met de opgegeven filters")
                    return _failure("Geen clubs gevonden")

                return self._try_dates_and_clubs(page, booking_dates, clubs)

            except Exception as e:  # noqa: BLE001 — subprocess moet altijd een ProviderResult geven
                logger.exception("Onverwachte fout in Meet & Play provider")
                return _failure(str(e))

            finally:
                for resource in (self._context, self._browser):
                    if resource:
                        with contextlib.suppress(Exception):
                            resource.close()
