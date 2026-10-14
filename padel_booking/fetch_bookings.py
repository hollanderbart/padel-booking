"""
Haal toekomstige boekingen op van meetandplay.nl en Playtomic.

Schrijft een JSON-lijst naar stdout (of naar --output bestand) met
alle toekomstige reserveringen van de geconfigureerde accounts.

Gebruik:
  python fetch_bookings.py
  python fetch_bookings.py --output /config/padel/future_bookings.json
  python fetch_bookings.py --debug
"""

import argparse
import contextlib
import json
import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

from orchestrator import enabled_providers, load_config
from providers.base import hhmm_to_minutes
from providers.playtomic.booking import format_address
from providers.playtomic.client import PlaytomicAuthError, PlaytomicClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Playtomic — toekomstige boekingen via REST API
# ---------------------------------------------------------------------------

def _playtomic_match_to_booking(match: dict) -> dict | None:
    start_raw = match.get("start_date", "")
    if not start_raw:
        return None

    try:
        local_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone()
        booked_date = local_dt.strftime("%Y-%m-%d")
        start_time = local_dt.strftime("%H:%M")
    except ValueError:
        booked_date = start_raw[:10]
        start_time = start_raw[11:16] if len(start_raw) > 10 else "?"

    try:
        end_min = hhmm_to_minutes(start_time) + int(match.get("duration", 0))
        end_time = f"{end_min // 60:02d}:{end_min % 60:02d}"
    except (TypeError, ValueError):
        end_time = "?"

    tenant = match.get("tenant") or {}
    address = tenant.get("address") or {}
    resource = match.get("resource") or {}
    match_id = match.get("match_id", "") or match.get("id", "")

    return {
        "booked_date": booked_date,
        "provider": "playtomic",
        "club_name": tenant.get("tenant_name", "") or match.get("tenant_name", ""),
        "club_address": address.get("full_address") or format_address(address),
        "court_name": resource.get("resource_name", "") or "Padelbaan",
        "time_range": f"{start_time} - {end_time}",
        "payment_url": f"https://playtomic.io/booking/{match_id}" if match_id else "",
        "status": "confirmed",
    }


def fetch_playtomic_bookings(email: str, password: str, token_cache_file: str) -> list[dict]:
    """
    Haalt toekomstige boekingen op via de Playtomic API.
    Endpoint: GET /v1/matches?user_id=<id>&status=CONFIRMED&start_min=<now>
    """
    client = PlaytomicClient(email=email, password=password, token_cache_file=token_cache_file)
    try:
        matches = client.get_upcoming_matches()
    except PlaytomicAuthError as e:
        logger.error("%s", e)
        return []
    except Exception as e:  # noqa: BLE001 — ophalen is best-effort
        logger.warning("Playtomic boekingen ophalen mislukt: %s", e)
        return []

    logger.info("%d Playtomic boeking(en) gevonden", len(matches))
    bookings = []
    for match in matches:
        try:
            booking = _playtomic_match_to_booking(match)
        except Exception as e:  # noqa: BLE001
            logger.debug("Fout bij verwerken Playtomic boeking: %s", e)
            continue
        if booking:
            bookings.append(booking)
    return bookings


# ---------------------------------------------------------------------------
# Meet & Play — toekomstige boekingen via Playwright scraping
# ---------------------------------------------------------------------------

MEETANDPLAY_RESERVATIONS_URL = "https://www.meetandplay.nl/mijn-reserveringen"
CARD_SELECTORS = [
    ".c-reservation-card",
    ".reservation-card",
    "[class*='reservation-item']",
    "[class*='booking-card']",
    ".mp-reservation",
]
DATE_SELECTORS = [".c-reservation-card__date", ".reservation-date", "[class*='date']", "time"]
CLUB_SELECTORS = [".c-reservation-card__club", ".club-name", "[class*='club']", "h3", "h4"]
TIME_SELECTORS = [".c-reservation-card__time", ".reservation-time", "[class*='time']"]
COURT_SELECTORS = [".c-reservation-card__court", ".court-name", "[class*='court']", "[class*='field']"]
PAYMENT_LINK_SELECTOR = "a[href*='betalen'], a[href*='payment'], a[href*='checkout'], a[href*='reservering']"

DUTCH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mrt": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
}


def _first_text(card, selectors: list[str], first_line: bool = False) -> str:
    """Tekst van het eerste niet-lege element dat een van de selectors matcht."""
    for selector in selectors:
        el = card.locator(selector)
        if el.count() > 0:
            text = el.first.inner_text().strip()
            if first_line:
                text = text.split("\n")[0]
            if text:
                return text
    return ""


def _card_date(card, card_text: str) -> str:
    for selector in DATE_SELECTORS:
        el = card.locator(selector)
        if el.count() == 0:
            continue
        # Parseer "donderdag 3 april 2026" of "03-04-2026" of ISO
        date_str = _parse_dutch_date(el.first.inner_text().strip())
        if date_str:
            return date_str
        dt_attr = el.first.get_attribute("datetime")
        if dt_attr:
            return dt_attr[:10]

    date_match = re.search(r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{4}-\d{2}-\d{2})\b", card_text)
    return _parse_dutch_date(date_match.group(1)) if date_match else ""


def _parse_reservation_card(card, i: int, today: date) -> dict | None:
    card_text = card.inner_text()
    logger.debug("Reserveringskaart %d tekst: %s", i, card_text[:200])

    date_str = _card_date(card, card_text)
    if not date_str:
        logger.debug("Geen datum gevonden in kaart %d", i)
        return None

    # Alleen toekomstige boekingen
    with contextlib.suppress(ValueError):
        if datetime.strptime(date_str, "%Y-%m-%d").date() < today:
            return None

    club_name = _first_text(card, CLUB_SELECTORS, first_line=True)
    time_range = _first_text(card, TIME_SELECTORS)
    court_name = _first_text(card, COURT_SELECTORS, first_line=True)

    payment_url = ""
    link = card.locator(PAYMENT_LINK_SELECTOR)
    if link.count() > 0:
        payment_url = link.first.get_attribute("href") or ""
        if payment_url and not payment_url.startswith("http"):
            payment_url = f"https://www.meetandplay.nl{payment_url}"

    logger.info("Reservering gevonden: %s op %s om %s", club_name, date_str, time_range)
    return {
        "booked_date": date_str,
        "provider": "meetandplay",
        "club_name": club_name or "Meet & Play",
        "club_address": "",
        "court_name": court_name or "Padelbaan",
        "time_range": time_range,
        "payment_url": payment_url,
        "status": "confirmed",
    }


def _logged_in_context(browser, session_mgr, email: str, password: str):
    """Geef een ingelogde browsercontext terug, of None als inloggen niet lukt."""
    context = session_mgr.new_context(browser)
    page = context.new_page()
    logged_in = session_mgr.is_logged_in(page)
    page.close()
    if logged_in:
        return context

    logger.info("Meet & Play sessie verlopen — opnieuw inloggen...")
    context.close()
    if not email or not password:
        logger.warning("Geen Meet & Play credentials — boekingen overgeslagen")
        return None

    context, success = session_mgr.auto_login(browser, email, password)
    if not success:
        logger.warning("Meet & Play inloggen mislukt — boekingen overgeslagen")
        return None
    return context


def _scrape_reservations(page) -> list[dict]:
    from providers.meetandplay.session import accept_cookie_banner

    logger.info("Meet & Play reserveringen ophalen...")
    page.goto(MEETANDPLAY_RESERVATIONS_URL, wait_until="load", timeout=30000)
    page.wait_for_timeout(2000)
    accept_cookie_banner(page)

    # Wacht op reserveringen of "geen reserveringen" bericht
    with contextlib.suppress(Exception):
        page.wait_for_selector(
            '.c-reservation-card, .reservation-card, [class*="reservation"], .c-alert',
            timeout=10000,
        )

    cards = None
    for selector in CARD_SELECTORS:
        candidate = page.locator(selector)
        if candidate.count() > 0:
            cards = candidate
            logger.info("Reserveringskaarten gevonden via selector: %s (%d stuks)", selector, candidate.count())
            break

    if cards is None:
        logger.info("Geen reserveringskaarten gevonden op Meet & Play (pagina-inhoud check)")
        page_text = page.inner_text("body").lower()
        if "geen reservering" in page_text or "no reservation" in page_text:
            logger.info("Meet & Play meldt: geen reserveringen")
        return []

    today = datetime.now().date()
    bookings = []
    for i in range(cards.count()):
        try:
            booking = _parse_reservation_card(cards.nth(i), i, today)
        except Exception as e:  # noqa: BLE001 — één onleesbare kaart mag de rest niet blokkeren
            logger.debug("Fout bij verwerken reserveringskaart %d: %s", i, e)
            continue
        if booking:
            bookings.append(booking)
    return bookings


def fetch_meetandplay_bookings(email: str, password: str, cookies_file: str) -> list[dict]:
    """
    Haalt toekomstige boekingen op van meetandplay.nl/mijn-reserveringen.
    Gebruikt Playwright om in te loggen en de reserveringenpagina te scrapen.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright niet beschikbaar — Meet & Play boekingen overgeslagen")
        return []

    from providers.meetandplay.session import SessionManager

    session_mgr = SessionManager(cookies_file)
    bookings = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = _logged_in_context(browser, session_mgr, email, password)
                if context:
                    bookings = _scrape_reservations(context.new_page())
                    session_mgr.save_cookies(context)
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001 — ophalen is best-effort
        logger.warning("Fout bij ophalen Meet & Play boekingen: %s", e)

    logger.info("%d Meet & Play boeking(en) gevonden", len(bookings))
    return bookings


def _parse_dutch_date(raw: str) -> str:
    """Converteert diverse datumformaten naar YYYY-MM-DD."""
    raw = raw.strip().lower()

    # ISO formaat: 2026-04-03
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # DD-MM-YYYY of DD/MM/YYYY
    m = re.match(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", raw)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"

    # "donderdag 3 april 2026" of "3 april 2026"
    m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", raw)
    if m and m.group(2) in DUTCH_MONTHS:
        return f"{int(m.group(3))}-{DUTCH_MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"

    return ""


# ---------------------------------------------------------------------------
# Hoofdflow
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Haal toekomstige padel boekingen op")
    parser.add_argument("--output", help="Schrijf JSON naar dit bestand (ipv stdout)")
    parser.add_argument("--debug", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )

    load_dotenv()

    try:
        config = load_config()
    except FileNotFoundError as e:
        logger.error("%s", e)
        print("[]")
        sys.exit(1)

    fetchers = {
        "meetandplay": fetch_meetandplay_bookings,
        "playtomic": fetch_playtomic_bookings,
    }
    all_bookings = []
    for spec, credentials, provider_cfg in enabled_providers(config):
        if not credentials["email"]:
            logger.info("%s niet geconfigureerd — %s overgeslagen", spec.email_env, spec.name)
            continue
        try:
            all_bookings.extend(fetchers[spec.name](
                credentials["email"], credentials["password"], provider_cfg[spec.session_file_key],
            ))
        except Exception as e:  # noqa: BLE001 — één provider mag de andere niet blokkeren
            logger.warning("%s ophalen mislukt: %s", spec.name, e)

    # Sorteer op datum
    all_bookings.sort(key=lambda b: b.get("booked_date", ""))

    output_json = json.dumps(all_bookings, indent=2, ensure_ascii=False)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(output_json)
        logger.info("%d boeking(en) geschreven naar %s", len(all_bookings), args.output)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
