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

import contextlib
from datetime import datetime, timedelta

import pytest
from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright

from providers.meetandplay.booking import SEARCH_URL, SLOT_LABEL_JS, MeetAndPlayBooker

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
        date_str = (datetime.now() + timedelta(days=day_offset)).strftime("%d-%m-%Y")

        page.goto(SEARCH_URL, wait_until="load", timeout=60000)
        page.wait_for_timeout(1500)
        booker._accept_cookies(page)
        booker._select_padel(page)

        loc_input = page.locator("input#location")
        loc_input.fill(city)
        loc_input.blur()
        page.wait_for_timeout(2500)

        page.locator("select#distance").select_option(radius)
        page.wait_for_timeout(1500)

        page.locator("select#indoor").select_option("INDOOR")
        page.wait_for_timeout(1500)

        booker._set_livewire_date(page, date_str)

        cards = page.locator(".c-club-card.mp-club-card")
        clubs = []
        for club_idx in range(cards.count()):
            card = cards.nth(club_idx)
            with contextlib.suppress(Exception):
                clubs.append({
                    "name": card.locator("h3").first.inner_text().strip(),
                    "address": card.locator(".c-club-card__address").first.inner_text().strip(),
                    "url": card.locator("a.mp-cta-link").first.get_attribute("href") or "",
                })

        for club in (c for c in clubs if c["url"]):
            page.goto(club["url"], wait_until="load", timeout=60000)
            page.wait_for_timeout(1500)
            booker._accept_cookies(page)
            booker._select_padel(page)

            indoor_select = page.locator("select#indoor")
            if indoor_select.count() > 0:
                indoor_select.select_option("INDOOR")
                page.wait_for_timeout(1500)

            booker._set_livewire_date(page, date_str)

            for slot in page.locator(".timeslot-container a.timeslot").all():
                try:
                    label = slot.evaluate(SLOT_LABEL_JS).lower()
                except Exception:
                    label = ""
                if "buiten" in label:
                    continue

                try:
                    time_text = slot.locator(".timeslot-time").first.inner_text().strip()
                except Exception:
                    time_text = "onbekend"

                try:
                    court_name = slot.locator(".timeslot-name").first.inner_text().strip().split("\n")[0].strip()
                except Exception:
                    court_name = "Onbekende baan"

                return {
                    "slot_id": slot.get_attribute("id") or "",
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
    Doorloop de productie-flow (winkelwagen → afrekenen → betaalprovider).
    Betaling wordt NIET afgerond — geeft de betaal-/checkout-URL terug.
    """
    page.goto(slot_info["club_url"], wait_until="load", timeout=60000)
    page.wait_for_timeout(1500)
    booker._accept_cookies(page)
    booker._set_livewire_date(page, slot_info["date_str"])

    if not booker._book_timeslot(page, slot_info):
        return None
    return slot_info["payment_url"]


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
        browser = pw.chromium.launch(headless=not headed)
        context = booker._ensure_logged_in(browser, booker.session_manager.new_context(browser))

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
            for resource in (context, browser):
                if resource:
                    with contextlib.suppress(Exception):
                        resource.close()
