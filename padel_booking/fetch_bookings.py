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
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

CONFIG_PATH = "config.yaml"
API_BASE_PLAYTOMIC = "https://api.playtomic.io"


# ---------------------------------------------------------------------------
# Configuratie
# ---------------------------------------------------------------------------

def load_config(config_path: str = CONFIG_PATH) -> dict:
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Configuratiebestand niet gevonden: {config_path}")
    with open(config_file) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Playtomic — toekomstige boekingen via REST API
# ---------------------------------------------------------------------------

def fetch_playtomic_bookings(email: str, password: str, token_cache_file: str) -> list[dict]:
    """
    Haalt toekomstige boekingen op via de Playtomic API.
    Endpoint: GET /v1/matches?user_id=<id>&status=CONFIRMED&start_min=<now>
    """
    import requests

    HEADERS = {
        "X-Requested-With": "com.playtomic.web",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/json",
    }

    session = requests.Session()
    session.headers.update(HEADERS)

    # Token laden of ophalen
    token_path = Path(token_cache_file)
    access_token = None
    user_id = None

    if token_path.exists():
        try:
            with open(token_path) as f:
                cached = json.load(f)
            expiry = datetime.fromisoformat(cached["expiry"])
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            from datetime import timedelta
            if expiry > datetime.now(tz=timezone.utc) + timedelta(minutes=5):
                access_token = cached["access_token"]
                user_id = cached.get("user_id")
                logger.info("Playtomic token geladen uit cache (verloopt %s)", expiry.isoformat())
        except Exception as e:
            logger.debug("Kon Playtomic token cache niet laden: %s", e)

    if not access_token:
        logger.info("Inloggen bij Playtomic als %s...", email)
        resp = session.post(
            f"{API_BASE_PLAYTOMIC}/v3/auth/login",
            json={"email": email, "password": password},
        )
        if resp.status_code == 401:
            logger.error("Playtomic inloggen mislukt — controleer PLAYTOMIC_EMAIL/PLAYTOMIC_PASSWORD")
            return []
        resp.raise_for_status()
        data = resp.json()
        access_token = data["access_token"]
        user_id = data.get("user_id")
        expiry_str = data.get("access_token_expiration")
        if expiry_str:
            from datetime import timedelta
            expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        else:
            from datetime import timedelta
            expiry = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        try:
            with open(token_path, "w") as f:
                json.dump({"access_token": access_token, "expiry": expiry.isoformat(), "user_id": user_id}, f)
        except Exception as e:
            logger.warning("Kon Playtomic token niet opslaan: %s", e)

    session.headers["Authorization"] = f"Bearer {access_token}"

    if not user_id:
        logger.warning("Playtomic user_id niet beschikbaar — boekingen ophalen niet mogelijk")
        return []

    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    logger.info("Playtomic toekomstige boekingen ophalen voor user %s...", user_id)

    # Haal matches op die bevestigd zijn en in de toekomst liggen
    try:
        resp = session.get(
            f"{API_BASE_PLAYTOMIC}/v1/matches",
            params={
                "user_id": user_id,
                "status": "CONFIRMED",
                "start_min": now_str,
                "size": 50,
                "sort": "start_date,asc",
            },
        )
        resp.raise_for_status()
        raw = resp.json()
        # De API geeft een gepagineerd object terug: {"content": [...], ...}
        # of een kale lijst bij oudere endpoints.
        if isinstance(raw, dict):
            matches = raw.get("content") or raw.get("data") or raw.get("matches") or []
        else:
            matches = raw
    except Exception as e:
        logger.warning("Playtomic boekingen ophalen mislukt: %s", e)
        return []

    logger.info("%d Playtomic boeking(en) gevonden", len(matches))

    bookings = []
    for match in matches:
        try:
            start_raw = match.get("start_date", "")
            if not start_raw:
                continue

            # Parseer start tijd
            try:
                start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                # Converteer naar lokale tijd
                local_dt = start_dt.astimezone()
                booked_date = local_dt.strftime("%Y-%m-%d")
                start_time = local_dt.strftime("%H:%M")
            except Exception:
                booked_date = start_raw[:10]
                start_time = start_raw[11:16] if len(start_raw) > 10 else "?"

            duration = match.get("duration", 0)
            try:
                end_h = int(start_time[:2]) * 60 + int(start_time[3:]) + int(duration)
                end_time = f"{end_h // 60:02d}:{end_h % 60:02d}"
            except Exception:
                end_time = "?"

            # Club/locatie info
            tenant = match.get("tenant", {}) or {}
            club_name = tenant.get("tenant_name", "") or match.get("tenant_name", "")
            address = tenant.get("address", {}) or {}
            club_address = address.get("full_address") or ", ".join(filter(None, [address.get("street", ""), address.get("city", "")]))

            # Court info
            resource = match.get("resource", {}) or {}
            court_name = resource.get("resource_name", "") or "Padelbaan"

            match_id = match.get("match_id", "") or match.get("id", "")
            payment_url = f"https://playtomic.io/booking/{match_id}" if match_id else ""

            bookings.append({
                "booked_date": booked_date,
                "provider": "playtomic",
                "club_name": club_name,
                "club_address": club_address,
                "court_name": court_name,
                "time_range": f"{start_time} - {end_time}",
                "payment_url": payment_url,
                "status": "confirmed",
            })
        except Exception as e:
            logger.debug("Fout bij verwerken Playtomic boeking: %s", e)

    return bookings


# ---------------------------------------------------------------------------
# Meet & Play — toekomstige boekingen via Playwright scraping
# ---------------------------------------------------------------------------

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
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )

            if session_mgr.cookies_exist():
                logger.info("Meet & Play sessie laden uit cookies...")
                session_mgr.load_cookies(context)

            page = context.new_page()

            # Controleer of de sessie nog geldig is
            if not session_mgr.is_logged_in(page):
                logger.info("Meet & Play sessie verlopen — opnieuw inloggen...")
                page.close()
                context.close()

                if not email or not password:
                    logger.warning("Geen Meet & Play credentials — boekingen overgeslagen")
                    browser.close()
                    return []

                context, success = session_mgr.auto_login(browser, email, password)
                if not success:
                    logger.warning("Meet & Play inloggen mislukt — boekingen overgeslagen")
                    browser.close()
                    return []
                page = context.new_page()

            logger.info("Meet & Play reserveringen ophalen...")
            page.goto("https://www.meetandplay.nl/mijn-reserveringen", wait_until="load", timeout=30000)
            page.wait_for_timeout(2000)

            # Accepteer cookies indien popup verschijnt
            try:
                cookie_btn = page.locator('button:has-text("Alles toestaan")')
                if cookie_btn.count() > 0:
                    cookie_btn.first.click()
                    page.wait_for_timeout(800)
            except Exception:
                pass

            # Wacht op reserveringen of "geen reserveringen" bericht
            try:
                page.wait_for_selector(
                    '.c-reservation-card, .reservation-card, [class*="reservation"], .c-alert',
                    timeout=10000,
                )
            except Exception:
                pass

            # Scrape reserveringskaarten
            today_str = datetime.now().strftime("%Y-%m-%d")

            # Probeer meerdere mogelijke selectors voor reserveringskaarten
            card_selectors = [
                ".c-reservation-card",
                ".reservation-card",
                "[class*='reservation-item']",
                "[class*='booking-card']",
                ".mp-reservation",
            ]

            cards = None
            for selector in card_selectors:
                candidate = page.locator(selector)
                if candidate.count() > 0:
                    cards = candidate
                    logger.info("Reserveringskaarten gevonden via selector: %s (%d stuks)", selector, candidate.count())
                    break

            if not cards or cards.count() == 0:
                logger.info("Geen reserveringskaarten gevonden op Meet & Play (pagina-inhoud check)")
                # Probeer via pagina-tekst te zien of er reserveringen zijn
                page_text = page.inner_text("body")
                if "geen reservering" in page_text.lower() or "no reservation" in page_text.lower():
                    logger.info("Meet & Play meldt: geen reserveringen")
                browser.close()
                return []

            today = datetime.now().date()

            for i in range(cards.count()):
                card = cards.nth(i)
                try:
                    card_text = card.inner_text()
                    logger.debug("Reserveringskaart %d tekst: %s", i, card_text[:200])

                    # Datum ophalen — zoek naar datum-element
                    date_str = None
                    for date_sel in [".c-reservation-card__date", ".reservation-date", "[class*='date']", "time"]:
                        date_el = card.locator(date_sel)
                        if date_el.count() > 0:
                            raw = date_el.first.inner_text().strip()
                            # Parseer "donderdag 3 april 2026" of "03-04-2026" of ISO
                            date_str = _parse_dutch_date(raw)
                            if date_str:
                                break
                            # Probeer datetime attribuut
                            dt_attr = date_el.first.get_attribute("datetime")
                            if dt_attr:
                                date_str = dt_attr[:10]
                                break

                    if not date_str:
                        # Probeer datum uit card_text te halen
                        import re
                        date_match = re.search(
                            r'\b(\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{4}-\d{2}-\d{2})\b',
                            card_text
                        )
                        if date_match:
                            date_str = _parse_dutch_date(date_match.group(1))

                    if not date_str:
                        logger.debug("Geen datum gevonden in kaart %d", i)
                        continue

                    # Alleen toekomstige boekingen
                    try:
                        from datetime import date as date_type
                        booking_dt = datetime.strptime(date_str, "%Y-%m-%d").date()
                        if booking_dt < today:
                            continue
                    except Exception:
                        pass

                    # Club naam
                    club_name = ""
                    for club_sel in [".c-reservation-card__club", ".club-name", "[class*='club']", "h3", "h4"]:
                        el = card.locator(club_sel)
                        if el.count() > 0:
                            club_name = el.first.inner_text().strip().split("\n")[0]
                            if club_name:
                                break

                    # Tijd
                    time_range = ""
                    for time_sel in [".c-reservation-card__time", ".reservation-time", "[class*='time']"]:
                        el = card.locator(time_sel)
                        if el.count() > 0:
                            time_range = el.first.inner_text().strip()
                            if time_range:
                                break

                    # Court naam
                    court_name = ""
                    for court_sel in [".c-reservation-card__court", ".court-name", "[class*='court']", "[class*='field']"]:
                        el = card.locator(court_sel)
                        if el.count() > 0:
                            court_name = el.first.inner_text().strip().split("\n")[0]
                            if court_name:
                                break

                    # Betaallink of boekingslink
                    payment_url = ""
                    link = card.locator("a[href*='betalen'], a[href*='payment'], a[href*='checkout'], a[href*='reservering']")
                    if link.count() > 0:
                        payment_url = link.first.get_attribute("href") or ""
                        if payment_url and not payment_url.startswith("http"):
                            payment_url = f"https://www.meetandplay.nl{payment_url}"

                    bookings.append({
                        "booked_date": date_str,
                        "provider": "meetandplay",
                        "club_name": club_name or "Meet & Play",
                        "club_address": "",
                        "court_name": court_name or "Padelbaan",
                        "time_range": time_range,
                        "payment_url": payment_url,
                        "status": "confirmed",
                    })
                    logger.info("Reservering gevonden: %s op %s om %s", club_name, date_str, time_range)

                except Exception as e:
                    logger.debug("Fout bij verwerken reserveringskaart %d: %s", i, e)

            session_mgr.save_cookies(context)
            browser.close()

    except Exception as e:
        logger.warning("Fout bij ophalen Meet & Play boekingen: %s", e)

    logger.info("%d Meet & Play boeking(en) gevonden", len(bookings))
    return bookings


def _parse_dutch_date(raw: str) -> str:
    """Converteert diverse datumformaten naar YYYY-MM-DD."""
    import re

    MONTHS = {
        "januari": 1, "februari": 2, "maart": 3, "april": 4,
        "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
        "september": 9, "oktober": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mrt": 3, "apr": 4, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
    }

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
    if m:
        day = int(m.group(1))
        month_name = m.group(2).lower()
        year = int(m.group(3))
        month = MONTHS.get(month_name)
        if month:
            return f"{year}-{month:02d}-{day:02d}"

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

    providers_config = config.get("providers", {})
    all_bookings = []

    # Meet & Play
    map_config = providers_config.get("meetandplay", {})
    if map_config.get("enabled", True):
        email = os.getenv("KNLTB_EMAIL", "")
        password = os.getenv("KNLTB_PASSWORD", "")
        cookies_file = map_config.get("cookies_file", ".meetandplay_cookies.json")
        if email:
            try:
                mnp_bookings = fetch_meetandplay_bookings(email, password, cookies_file)
                all_bookings.extend(mnp_bookings)
            except Exception as e:
                logger.warning("Meet & Play ophalen mislukt: %s", e)
        else:
            logger.info("KNLTB_EMAIL niet geconfigureerd — Meet & Play overgeslagen")

    # Playtomic
    pt_config = providers_config.get("playtomic", {})
    if pt_config.get("enabled", False):
        email = os.getenv("PLAYTOMIC_EMAIL", "")
        password = os.getenv("PLAYTOMIC_PASSWORD", "")
        token_cache = pt_config.get("token_cache_file", ".playtomic_token.json")
        if email:
            try:
                pt_bookings = fetch_playtomic_bookings(email, password, token_cache)
                all_bookings.extend(pt_bookings)
            except Exception as e:
                logger.warning("Playtomic ophalen mislukt: %s", e)
        else:
            logger.info("PLAYTOMIC_EMAIL niet geconfigureerd — Playtomic overgeslagen")

    # Sorteer op datum
    all_bookings.sort(key=lambda b: b.get("booked_date", ""))

    output_json = json.dumps(all_bookings, indent=2, ensure_ascii=False)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(output_json)
        logger.info("%d boeking(en) geschreven naar %s", len(all_bookings), args.output)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
