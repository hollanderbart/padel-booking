"""
Integratietest voor het KNLTB Padel Booking script.

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

from booking import PadelBooker, SEARCH_URL

# Laad .env zodat credentials beschikbaar zijn
load_dotenv()


def _find_any_available_slot(page: Page, booker: PadelBooker) -> dict | None:
    """
    Zoek flexibel een beschikbaar indoor slot bij een van de gevonden clubs
    in de komende 7 dagen, zonder beperking op tijdvenster of dagdeel.

    Returns:
        Dict met slot-info of None als er geen beschikbaar slot is.
    """
    city = booker.config["location"]["city"]
    radius = str(booker.config["location"]["radius_km"])

    for day_offset in range(7):
        date = datetime.now() + timedelta(days=day_offset)
        date_str = date.strftime("%d-%m-%Y")

        page.goto(SEARCH_URL, wait_until="load", timeout=60000)
        page.wait_for_timeout(1500)
        booker._accept_cookies(page)

        # Sport: Padel
        page.locator("select#sportId").select_option("2")
        page.wait_for_timeout(1500)

        # Locatie
        loc_input = page.locator("input#location")
        loc_input.fill(city)
        loc_input.blur()
        page.wait_for_timeout(2500)

        # Afstand
        page.locator("select#distance").select_option(radius)
        page.wait_for_timeout(1500)

        # Daktype: binnen
        page.locator("select#indoor").select_option("INDOOR")
        page.wait_for_timeout(1500)

        # Datum instellen via Livewire
        lw_match = re.search(
            r"window\.Livewire\.find\('([^']+)'\)\.set\('date'", page.content()
        )
        if lw_match:
            page.evaluate(
                f"window.Livewire.find('{lw_match.group(1)}').set('date', '{date_str}')"
            )
            page.wait_for_timeout(2000)

        # Clubs ophalen
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

            # Navigeer naar clubpagina
            page.goto(club["url"], wait_until="load", timeout=60000)
            page.wait_for_timeout(1500)
            booker._accept_cookies(page)

            # Sport: Padel
            sport_select = page.locator("select#sportId")
            if sport_select.count() > 0:
                sport_select.select_option("2")
                page.wait_for_timeout(1500)

            # Daktype: binnen
            indoor_select = page.locator("select#indoor")
            if indoor_select.count() > 0:
                indoor_select.select_option("INDOOR")
                page.wait_for_timeout(1500)

            # Datum instellen via Livewire
            lw_match = re.search(
                r"window\.Livewire\.find\('([^']+)'\)\.set\('date'", page.content()
            )
            if lw_match:
                page.evaluate(
                    f"window.Livewire.find('{lw_match.group(1)}').set('date', '{date_str}')"
                )
                page.wait_for_timeout(2000)

            # Haal tijdsloten op
            slots = page.locator(".timeslot-container a.timeslot")

            for i in range(slots.count()):
                slot = slots.nth(i)

                # Skip buiten-banen
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


def _reach_checkout(page: Page, booker: PadelBooker, slot_info: dict) -> str | None:
    """
    Klik het tijdslot aan, voeg toe aan winkelwagen en klik Afrekenen.
    Betaling wordt NIET afgerond — geeft de checkout-URL terug zonder verder te navigeren.

    Returns:
        De checkout-URL als string, of None als de flow mislukt.
    """
    slot_id = slot_info["slot_id"]

    # Navigeer terug naar de clubpagina met het tijdslot
    page.goto(slot_info["club_url"], wait_until="load", timeout=60000)
    page.wait_for_timeout(1500)
    booker._accept_cookies(page)

    # Datum instellen
    lw_match = re.search(
        r"window\.Livewire\.find\('([^']+)'\)\.set\('date'", page.content()
    )
    if lw_match:
        page.evaluate(
            f"window.Livewire.find('{lw_match.group(1)}').set('date', '{slot_info['date_str']}')"
        )
        page.wait_for_timeout(2000)

    # Stap 1: klik op het tijdslot
    slot_anchor = page.locator(f"a.timeslot[id='{slot_id}']")
    if slot_anchor.count() == 0:
        slot_anchor = page.locator(f"a[id='{slot_id}']")
    if slot_anchor.count() == 0:
        return None

    slot_anchor.first.click()
    page.wait_for_timeout(2000)

    # Stap 2: klik "Toevoegen" / "Reserveren" / "Boeken"
    add_btn = page.locator('button:has-text("Toevoegen")')
    if add_btn.count() == 0:
        add_btn = page.locator('button:has-text("Reserveren")')
    if add_btn.count() == 0:
        add_btn = page.locator('button:has-text("Boeken")')
    if add_btn.count() == 0:
        return None

    add_btn.first.click()
    page.wait_for_timeout(2500)

    # Stap 3: klik "Afrekenen"
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

    # Wacht tot Livewire asynchroon navigeert naar de checkout-pagina
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

    # Controleer of we echt op de checkout-pagina zijn beland
    on_checkout = any(kw in checkout_url.lower() for kw in checkout_keywords) or \
                  "Winkelwagen" in page.content()
    if not on_checkout:
        return None

    # Zoek de betaalknop en lees de href uit — klik NIET
    pay_selectors = [
        'a:has-text("Betalen")',
        'a:has-text("Nu betalen")',
        'a[href*="betalen"]',
        'a[href*="payment"]',
        'a:has-text("Afrekenen")',
    ]
    for _ in range(6):  # maximaal ~12 seconden wachten
        for sel in pay_selectors:
            candidate = page.locator(sel)
            if candidate.count() > 0 and candidate.first.is_visible():
                href = candidate.first.get_attribute("href") or ""
                if href:
                    return href
        page.wait_for_timeout(2000)

    # Betaalknop is een button (geen href) — geef checkout-URL terug als fallback
    return checkout_url


def test_full_booking_flow(headed):
    """
    Integratietest die de volledige flow doorloopt tot de checkout-pagina.
    De betaling wordt NIET afgerond — er wordt geen echte boeking gemaakt.

    Geslaagd als: een geldige checkout-URL wordt teruggegeven.
    """
    import os

    email = os.getenv("KNLTB_EMAIL", "").strip()
    password = os.getenv("KNLTB_PASSWORD", "").strip()

    if not email or not password:
        pytest.skip(
            "KNLTB_EMAIL en/of KNLTB_PASSWORD niet gevonden in omgeving of .env — test overgeslagen"
        )

    booker = PadelBooker("config.yaml")
    browser = None
    context = None

    with sync_playwright() as pw:
        booker._playwright = pw
        browser = pw.chromium.launch(headless=not headed)
        context = booker._make_context(browser, headed)
        context = booker._ensure_logged_in(browser, context, headed)

        try:
            page = context.new_page()

            # Stap 1: clubs zoeken
            clubs = booker._search_clubs(page)
            assert len(clubs) > 0, (
                "Geen clubs gevonden — controleer config.yaml (locatie/filters)"
            )

            # Stap 2: beschikbaar slot vinden
            slot_info = _find_any_available_slot(page, booker)
            assert slot_info is not None, (
                "Geen enkel beschikbaar indoor slot gevonden in de komende 7 dagen"
            )

            # Stap 3: flow tot checkout — betaling NIET afronden
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
