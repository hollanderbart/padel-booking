"""
Integratietest voor het Padel Booking script — Meet & Play provider.

De test verifieert de volledige flow tot en met de checkout-pagina,
maar rondt de betaling NIET af — er wordt dus geen echte boeking gemaakt.

Vereisten:
  - KNLTB_EMAIL en KNLTB_PASSWORD in omgeving of .env bestand
  - Playwright Chromium geïnstalleerd (playwright install chromium)

Uitvoeren:
  pytest test_integration.py -v -s
  pytest test_integration.py -v -s --headed
"""

import re
from datetime import datetime, timedelta

import pytest
from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright

from providers.meetandplay.booking import MeetAndPlayBooker, SEARCH_URL

# Laad .env zodat credentials beschikbaar zijn
load_dotenv()

MINIMAL_REQUEST = {
    "booking_request": {
        "location": {"city": "Boskoop", "radius_km": 20},
        "day": "thursday",
        "time_start": "19:30",
        "time_end": "21:00",
        "duration_minutes": 90,
        "court_type": "indoor",
        "game_type": "double",
        "weeks_ahead": 4,
    },
    "credentials": {},
    "provider_config": {"cookies_file": ".meetandplay_cookies.json"},
    "dry_run": False,
}


def _find_any_available_slot(page: Page, booker: MeetAndPlayBooker) -> dict | None:
    """
    Zoek flexibel een beschikbaar indoor slot bij een van de gevonden clubs
    in de komende 7 dagen, zonder beperking op tijdvenster of dagdeel.
    """
    city = booker._booking["location"]["city"]
    radius = str(booker._booking["location"]["radius_km"])

    for day_offset in range(7):
        date = datetime.now() + timedelta(days=day_offset)
        date_str = date.strftime("%d-%m-%Y")

        page.goto(SEARCH_URL, wait_until="load", timeout=60000)
        page.wait_for_timeout(1500)
        booker._accept_cookies(page)

        page.locator("select#sportId").select_option("2")
        page.wait_for_timeout(1500)

        loc_input = page.locator("input#location")
        loc_input.fill(city)
        loc_input.blur()
        page.wait_for_timeout(2500)

        page.locator("select#distance").select_option(radius)
        page.wait_for_timeout(1500)

        page.locator("select#indoor").select_option("INDOOR")
        page.wait_for_timeout(1500)

        lw_match = re.search(
            r"window\.Livewire\.find\('([^']+)'\)\.set\('date'", page.content()
        )
        if lw_match:
            page.evaluate(
                f"window.Livewire.find('{lw_match.group(1)}').set('date', '{date_str}')"
            )
            page.wait_for_timeout(2000)

        cards = page.locator(".c-club-card.mp-club-card")
        count = cards.count()

        for club_idx in range(count):
            card = cards.nth(club_idx)
            try:
                name = card.locator("h3").first.inner_text().strip()
                book_url = card.locator("a.mp-cta-link").first.get_attribute("href") or ""
                address = card.locator(".c-club-card__address").first.inner_text().strip()
            except Exception:
                continue

            if not book_url:
                continue

            club = {"name": name, "address": address, "url": book_url}

            page.goto(club["url"], wait_until="load", timeout=60000)
            page.wait_for_timeout(1500)
            booker._accept_cookies(page)

            sport_select = page.locator("select#sportId")
            if sport_select.count() > 0:
                sport_select.select_option("2")
                page.wait_for_timeout(1500)

            indoor_select = page.locator("select#indoor")
            if indoor_select.count() > 0:
                indoor_select.select_option("INDOOR")
                page.wait_for_timeout(1500)

            lw_match = re.search(
                r"window\.Livewire\.find\('([^']+)'\)\.set\('date'", page.content()
            )
            if lw_match:
                page.evaluate(
                    f"window.Livewire.find('{lw_match.group(1)}').set('date', '{date_str}')"
                )
                page.wait_for_timeout(2000)

            slots = page.locator(".timeslot-container a.timeslot")

            for i in range(slots.count()):
                slot = slots.nth(i)

                try:
                    label = slot.evaluate("""el => {
                        let s = el.closest('.timeslots');
                        return s && s.previousElementSibling ? s.previousElementSibling.innerText : '';
                    }""").lower()
                except Exception:
                    label = ""

                if "buiten" in label:
                    continue

                try:
                    time_text = slot.locator(".timeslot-time").first.inner_text().strip()
                except Exception:
                    time_text = "onbekend"

                try:
                    court_name = slot.locator(".timeslot-name").first.inner_text().strip()
                    court_name = court_name.split("\n")[0].strip()
                except Exception:
                    court_name = "Onbekende baan"

                slot_id = slot.get_attribute("id") or ""

                return {
                    "slot_id": slot_id,
                    "court_name": court_name,
                    "time_range": time_text.replace("\n", " "),
                    "club_name": club["name"],
                    "club_address": club["address"],
                    "club_url": club["url"],
                    "date_str": date_str,
                }

    return None


def _reach_checkout(page: Page, booker: MeetAndPlayBooker, slot_info: dict) -> str | None:
    """
    Klik het tijdslot aan, voeg toe aan winkelwagen en klik Afrekenen.
    Betaling wordt NIET afgerond — geeft de checkout-URL terug.
    """
    slot_id = slot_info["slot_id"]

    page.goto(slot_info["club_url"], wait_until="load", timeout=60000)
    page.wait_for_timeout(1500)
    booker._accept_cookies(page)

    lw_match = re.search(
        r"window\.Livewire\.find\('([^']+)'\)\.set\('date'", page.content()
    )
    if lw_match:
        page.evaluate(
            f"window.Livewire.find('{lw_match.group(1)}').set('date', '{slot_info['date_str']}')"
        )
        page.wait_for_timeout(2000)

    slot_anchor = page.locator(f"a.timeslot[id='{slot_id}']")
    if slot_anchor.count() == 0:
        slot_anchor = page.locator(f"a[id='{slot_id}']")
    if slot_anchor.count() == 0:
        return None

    slot_anchor.first.click()
    page.wait_for_timeout(2000)

    add_btn = page.locator('button:has-text("Toevoegen")')
    if add_btn.count() == 0:
        add_btn = page.locator('button:has-text("Reserveren")')
    if add_btn.count() == 0:
        add_btn = page.locator('button:has-text("Boeken")')
    if add_btn.count() == 0:
        return None

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
            break

    if checkout_btn is None:
        return None

    checkout_btn.first.click()

    checkout_keywords = ["reserveren", "winkelwagen", "payment", "checkout", "betaling", "betalen", "order", "bestelling"]
    try:
        page.wait_for_url(
            lambda url: any(kw in url.lower() for kw in checkout_keywords),
            timeout=15000,
        )
    except Exception:
        pass
    page.wait_for_timeout(1500)

    checkout_url = page.url

    on_checkout = any(kw in checkout_url.lower() for kw in checkout_keywords) or \
                  "Winkelwagen" in page.content()
    if not on_checkout:
        return None

    tos_checkbox = page.locator("input#tos")
    try:
        tos_checkbox.wait_for(state="visible", timeout=10000)
    except Exception:
        pass
    if tos_checkbox.count() > 0 and not tos_checkbox.is_checked():
        page.evaluate("document.querySelector('input#tos').click()")
        page.wait_for_timeout(1000)

    pay_btn = page.locator('button:has-text("Betaling starten")')
    if pay_btn.count() == 0:
        for sel in ['button:has-text("Betalen")', 'button:has-text("Nu betalen")', 'a:has-text("Betalen")']:
            candidate = page.locator(sel)
            if candidate.count() > 0 and candidate.first.is_visible():
                pay_btn = candidate
                break

    if pay_btn.count() == 0 or not pay_btn.first.is_visible():
        return checkout_url

    pay_btn.first.click()
    try:
        page.wait_for_url(
            lambda url: url != checkout_url,
            timeout=20000,
        )
    except Exception:
        pass
    page.wait_for_timeout(2000)

    return page.url


def test_full_booking_flow(headed):
    """
    Integratietest die de volledige flow doorloopt tot de checkout-pagina.
    De betaling wordt NIET afgerond — er wordt geen echte boeking gemaakt.
    """
    import os

    email = os.getenv("KNLTB_EMAIL", "").strip()
    password = os.getenv("KNLTB_PASSWORD", "").strip()

    if not email or not password:
        pytest.skip(
            "KNLTB_EMAIL en/of KNLTB_PASSWORD niet gevonden in omgeving of .env — test overgeslagen"
        )

    request = dict(MINIMAL_REQUEST)
    request["credentials"] = {"email": email, "password": password}

    booker = MeetAndPlayBooker(request)
    browser = None
    context = None

    with sync_playwright() as pw:
        booker._playwright = pw
        browser = pw.chromium.launch(headless=not headed)
        context = booker._make_context(browser)
        context = booker._ensure_logged_in(browser, context)

        try:
            page = context.new_page()

            clubs = booker._search_clubs(page)
            assert len(clubs) > 0, (
                "Geen clubs gevonden — controleer config.yaml (locatie/filters)"
            )

            slot_info = _find_any_available_slot(page, booker)
            assert slot_info is not None, (
                "Geen enkel beschikbaar indoor slot gevonden in de komende 7 dagen"
            )

            checkout_url = _reach_checkout(page, booker, slot_info)

            assert checkout_url is not None, (
                f"Checkout-pagina niet bereikt voor slot {slot_info['slot_id']} "
                f"bij {slot_info['club_name']}"
            )

            print("\n" + "=" * 60)
            print("TEST GESLAAGD — betaallink achterhaald (geen echte boeking)")
            print("=" * 60)
            print(f"Club:        {slot_info['club_name']}")
            print(f"Baan:        {slot_info['court_name']}")
            print(f"Tijd:        {slot_info['time_range']}")
            print(f"Betaallink:  {checkout_url}")
            print("=" * 60 + "\n")

        finally:
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

        booker._playwright = None
