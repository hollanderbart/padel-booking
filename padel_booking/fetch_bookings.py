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


def fetch_playtomic_bookings(email: str, password: str, token_cache_file: str) -> list[dict] | None:
    """
    Haalt toekomstige boekingen op via de Playtomic API (None als ophalen mislukt).
    Endpoint: GET /v1/matches?user_id=<id>&status=CONFIRMED&start_min=<now>
    """
    client = PlaytomicClient(email=email, password=password, token_cache_file=token_cache_file)
    try:
        matches = client.get_upcoming_matches()
    except PlaytomicAuthError as e:
        logger.error("%s", e)
        return None
    except Exception as e:  # noqa: BLE001 — ophalen is best-effort
        logger.warning("Playtomic boekingen ophalen mislukt: %s", e)
        return None

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

MEETANDPLAY_RESERVATIONS_URL = "https://meetandplay.nl/mijn-aankopen"
# Tabblad "Aankomend": per reservering een <a class="timeslot --order-item"> met
#   .timeslot-date-day / -month ("22" / "okt"), .timeslot-time ("20:00 - 21:30 90 minuten"),
#   .timeslot-name ("Padelbaan 4 - Tennis en Padel Meerzicht") en als laatste .timeslot-name de status ("Betaald")
CARD_SELECTOR = "a.timeslot.--order-item"
TIME_RANGE_RE = re.compile(r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}")
CANCELLED_WORDS = ("geannuleerd", "geweigerd", "verlopen")

DUTCH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mrt": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
}


def _infer_date(day: int, month: int, today: date) -> date:
    """
    De kaart toont geen jaar: neem het voorkomen dat het dichtst bij vandaag ligt. 'Aankomend'
    toont ook net verstreken reserveringen (22 okt op 23 okt); die horen bij dit jaar, niet bij het volgende.
    """
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        with contextlib.suppress(ValueError):  # 29 februari bestaat niet elk jaar
            candidates.append(date(year, month, day))
    return min(candidates, key=lambda d: abs((d - today).days))


def _text(card, selector: str) -> str:
    el = card.locator(selector)
    return el.first.inner_text().strip() if el.count() > 0 else ""


def _parse_order_card(card, today: date) -> dict | None:
    day = _text(card, ".timeslot-date-day")
    month = DUTCH_MONTHS.get(_text(card, ".timeslot-date-month").lower().strip("."))
    if not day.isdigit() or not month:
        logger.debug("Geen datum gevonden in reserveringskaart: %s", card.inner_text()[:200])
        return None
    booked = _infer_date(int(day), month, today)
    if booked < today:
        logger.info("Reservering op %s overgeslagen (verstreken)", booked.isoformat())
        return None

    names = card.locator(".timeslot-name")
    # Eerste regel van de eerste .timeslot-name: "Padelbaan 4 - Tennis en Padel Meerzicht"
    title = names.first.inner_text().strip().split("\n")[0] if names.count() > 0 else ""
    court_name, _, club_name = title.partition(" - ")
    status = names.last.inner_text().strip() if names.count() > 1 else ""
    if any(word in status.lower() for word in CANCELLED_WORDS):
        logger.info("Reservering op %s overgeslagen (status: %s)", booked.isoformat(), status)
        return None

    time_match = TIME_RANGE_RE.search(_text(card, ".timeslot-time"))
    time_range = re.sub(r"\s*-\s*", " - ", time_match.group(0)) if time_match else ""
    href = card.get_attribute("href") or ""
    if href and not href.startswith("http"):
        href = f"https://meetandplay.nl{href}"

    logger.info("Reservering gevonden: %s op %s om %s (%s)", club_name or title, booked.isoformat(),
                time_range or "?", status or "status onbekend")
    return {
        "booked_date": booked.isoformat(),
        "provider": "meetandplay",
        "club_name": club_name or title or "Meet & Play",
        "club_address": "",
        "court_name": court_name or "Padelbaan",
        "time_range": time_range,
        "payment_url": href,
        "status": status.lower() or "confirmed",
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

    logger.info("Meet & Play reserveringen ophalen (%s)...", MEETANDPLAY_RESERVATIONS_URL)
    page.goto(MEETANDPLAY_RESERVATIONS_URL, wait_until="load", timeout=30000)
    accept_cookie_banner(page)

    # Livewire laadt de kaarten na; zonder reserveringen verschijnt er geen kaart
    with contextlib.suppress(Exception):
        page.wait_for_selector(CARD_SELECTOR, timeout=10000)

    cards = page.locator(CARD_SELECTOR)
    logger.info("%d reserveringskaart(en) op 'Aankomend'", cards.count())

    today = datetime.now().date()
    bookings = []
    for i in range(cards.count()):
        try:
            booking = _parse_order_card(cards.nth(i), today)
        except Exception as e:  # noqa: BLE001 — één onleesbare kaart mag de rest niet blokkeren
            logger.debug("Fout bij verwerken reserveringskaart %d: %s", i, e)
            continue
        if booking:
            bookings.append(booking)
    return bookings


def fetch_meetandplay_bookings(email: str, password: str, cookies_file: str) -> list[dict] | None:
    """
    Haalt toekomstige boekingen op van meetandplay.nl/mijn-aankopen (tabblad "Aankomend").
    Gebruikt Playwright om in te loggen en de reserveringenpagina te scrapen.
    Geeft None terug als ophalen mislukt (dan is "geen boekingen" niet te onderscheiden van een fout).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright niet beschikbaar — Meet & Play boekingen overgeslagen")
        return None

    from providers.meetandplay.session import SessionManager

    session_mgr = SessionManager(cookies_file)
    bookings = None

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
        return None

    if bookings is not None:
        logger.info("%d Meet & Play boeking(en) gevonden", len(bookings))
    return bookings


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
    failed = []
    for spec, credentials, provider_cfg in enabled_providers(config):
        if not credentials["email"]:
            logger.info("%s niet geconfigureerd — %s overgeslagen", spec.email_env, spec.name)
            continue
        try:
            bookings = fetchers[spec.name](
                credentials["email"], credentials["password"], provider_cfg[spec.session_file_key],
            )
        except Exception as e:  # noqa: BLE001 — één provider mag de andere niet blokkeren
            logger.warning("%s ophalen mislukt: %s", spec.name, e)
            bookings = None
        if bookings is None:
            failed.append(spec.name)
        else:
            all_bookings.extend(bookings)

    # Een mislukte provider zou bestaande boekingen "wissen", waarna de booker die datums
    # opnieuw probeert te boeken — laat het bestand dan ongemoeid.
    if failed and args.output:
        logger.warning("Ophalen mislukt voor %s — %s niet bijgewerkt", ", ".join(failed), args.output)
        sys.exit(1)

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
