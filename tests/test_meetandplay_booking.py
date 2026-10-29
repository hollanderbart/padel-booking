"""
Unit tests voor providers/meetandplay/booking.py — geen browser nodig.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from providers.meetandplay.booking import RELEASE_POLL, MeetAndPlayBooker, PendingReservationError
from providers.base import ProviderResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(
    day="thursday",
    time_start="19:30",
    time_end="21:00",
    duration=90,
    court_type="indoor",
    game_type="double",
    weeks_ahead=4,
    dry_run=False,
) -> dict:
    return {
        "booking_request": {
            "location": {"city": "Boskoop", "radius_km": 20},
            "day": day,
            "time_start": time_start,
            "time_end": time_end,
            "duration_minutes": duration,
            "court_type": court_type,
            "game_type": game_type,
            "weeks_ahead": weeks_ahead,
        },
        "credentials": {"email": "test@test.nl", "password": "pw"},
        "provider_config": {"cookies_file": "/tmp/.test_cookies.json"},
        "dry_run": dry_run,
    }


def _make_booker(request: dict = None) -> MeetAndPlayBooker:
    req = request or _make_request()
    with patch("providers.meetandplay.booking.SessionManager"):
        return MeetAndPlayBooker(req)


# ---------------------------------------------------------------------------
# _get_upcoming_booking_dates
# ---------------------------------------------------------------------------

class TestGetUpcomingBookingDates:
    def test_retourneert_gevraagd_aantal_datums(self):
        booker = _make_booker()
        dates = booker._get_upcoming_booking_dates(count=4)
        assert len(dates) == 4

    def test_alle_datums_op_de_juiste_weekdag(self):
        # thursday = weekdag 3
        booker = _make_booker(_make_request(day="thursday"))
        dates = booker._get_upcoming_booking_dates(count=3)
        for d in dates:
            assert d.weekday() == 3

    def test_datums_een_week_uit_elkaar(self):
        booker = _make_booker()
        dates = booker._get_upcoming_booking_dates(count=3)
        assert (dates[1] - dates[0]).days == 7
        assert (dates[2] - dates[1]).days == 7

    def test_eerste_datum_in_de_toekomst(self):
        booker = _make_booker()
        dates = booker._get_upcoming_booking_dates(count=1)
        # Op de boekingsdag zelf vóór time_start (19:30) is vandaag de eerste datum; de datum draagt dan
        # het huidige tijdstip, dus vergelijk het slot (datum + time_start), niet de datum zelf
        slot = dates[0].replace(hour=19, minute=30, second=0, microsecond=0)
        assert slot > datetime.now()

    def test_ongeldige_dag_gooit_valueerror(self):
        booker = _make_booker()
        booker._booking["day"] = "quatember"
        with pytest.raises(ValueError, match="Ongeldige dag"):
            booker._get_upcoming_booking_dates()

    def test_nederlandse_dagnamen_werken(self):
        for nl_name, weekday_num in [("maandag", 0), ("dinsdag", 1), ("woensdag", 2),
                                      ("donderdag", 3), ("vrijdag", 4), ("zaterdag", 5), ("zondag", 6)]:
            booker = _make_booker(_make_request(day=nl_name))
            dates = booker._get_upcoming_booking_dates(count=1)
            assert dates[0].weekday() == weekday_num


# ---------------------------------------------------------------------------
# _accept_cookies
# ---------------------------------------------------------------------------

class TestAcceptCookies:
    def test_klikt_alles_toestaan_als_aanwezig(self):
        booker = _make_booker()
        page = MagicMock()
        btn = MagicMock()
        btn.count.return_value = 1
        page.locator.return_value = btn

        booker._accept_cookies(page)

        btn.first.click.assert_called_once()

    def test_geen_actie_als_knop_niet_aanwezig(self):
        booker = _make_booker()
        page = MagicMock()
        btn = MagicMock()
        btn.count.return_value = 0
        page.locator.return_value = btn

        booker._accept_cookies(page)  # mag niet crashen

        btn.first.click.assert_not_called()

    def test_exception_wordt_genegeerd(self):
        booker = _make_booker()
        page = MagicMock()
        page.locator.side_effect = Exception("page crash")

        booker._accept_cookies(page)  # moet stil falen


# ---------------------------------------------------------------------------
# _ensure_logged_in
# ---------------------------------------------------------------------------

class TestEnsureLoggedIn:
    def test_al_ingelogd_retourneert_zelfde_context(self):
        booker = _make_booker()
        browser = MagicMock()
        context = MagicMock()
        page = MagicMock()
        context.new_page.return_value = page
        booker.session_manager.is_logged_in.return_value = True

        result = booker._ensure_logged_in(browser, context)
        assert result is context

    def test_niet_ingelogd_met_credentials_logt_automatisch_in(self):
        booker = _make_booker()
        browser = MagicMock()
        context = MagicMock()
        new_context = MagicMock()
        page = MagicMock()
        context.new_page.return_value = page
        booker.session_manager.is_logged_in.return_value = False
        booker.session_manager.auto_login.return_value = (new_context, True)

        result = booker._ensure_logged_in(browser, context)
        assert result is new_context

    def test_niet_ingelogd_login_mislukt_gooit_runtimeerror(self):
        booker = _make_booker()
        browser = MagicMock()
        context = MagicMock()
        page = MagicMock()
        context.new_page.return_value = page
        booker.session_manager.is_logged_in.return_value = False
        booker.session_manager.auto_login.return_value = (None, False)

        with pytest.raises(RuntimeError, match="Automatisch inloggen mislukt"):
            booker._ensure_logged_in(browser, context)

    def test_geen_credentials_gooit_runtimeerror(self):
        booker = _make_booker(_make_request())
        booker._credentials = {"email": "", "password": ""}
        browser = MagicMock()
        context = MagicMock()
        page = MagicMock()
        context.new_page.return_value = page
        booker.session_manager.is_logged_in.return_value = False

        with pytest.raises(RuntimeError, match="Geen credentials"):
            booker._ensure_logged_in(browser, context)


# ---------------------------------------------------------------------------
# _find_timeslot — filter logica
# ---------------------------------------------------------------------------

class TestFindTimeslotFilters:
    def _make_slot_mock(self, time_text="19:30\n90 min", court_label="", slot_id="slot-1", court_name="Baan 1"):
        slot = MagicMock()
        slot.evaluate.return_value = court_label.lower()
        slot.locator.return_value.first.inner_text.return_value = time_text
        slot.locator.return_value.first.inner_text.side_effect = None
        slot.get_attribute.return_value = slot_id

        # Maak specifieke locators voor timeslot-time en timeslot-name
        def locator_factory(selector):
            m = MagicMock()
            if "timeslot-time" in selector:
                m.first.inner_text.return_value = time_text
            elif "timeslot-name" in selector:
                m.first.inner_text.return_value = court_name
            return m

        slot.locator.side_effect = locator_factory
        return slot

    def _make_page_with_slots(self, slots_list, html=""):
        page = MagicMock()
        page.goto.return_value = None
        page.wait_for_timeout.return_value = None
        page.content.return_value = html

        # Selectoren voor filters (sport, indoor, daypart, duration) — lege mock
        sport_select = MagicMock()
        sport_select.count.return_value = 0
        page.locator.return_value = sport_select

        # Overrride de timeslot locator
        slots_mock = MagicMock()
        slots_mock.count.return_value = len(slots_list)
        slots_mock.nth.side_effect = lambda i: slots_list[i]

        def locator_dispatch(selector):
            if "timeslot-container" in selector:
                return slots_mock
            m = MagicMock()
            m.count.return_value = 0
            return m

        page.locator.side_effect = locator_dispatch
        return page

    def test_slot_in_tijdvenster_wordt_gevonden(self):
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00", duration=90))
        slot = self._make_slot_mock(time_text="19:30\n– 21:00\n90 min")
        page = self._make_page_with_slots([slot])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        result = booker._find_timeslot(page, club)
        assert result is not None

    def test_buiten_label_wordt_overgeslagen_voor_indoor(self):
        booker = _make_booker(_make_request(court_type="indoor"))
        slot = self._make_slot_mock(court_label="Buitenbaan", time_text="19:30\n90 min")
        page = self._make_page_with_slots([slot])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        result = booker._find_timeslot(page, club)
        assert result is None

    def test_buiten_label_logt_reden(self, caplog):
        import logging
        booker = _make_booker(_make_request(court_type="indoor"))
        slot = self._make_slot_mock(court_label="Buitenbaan dubbelspel", time_text="19:30\n90 min")
        page = self._make_page_with_slots([slot])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        with caplog.at_level(logging.INFO):
            booker._find_timeslot(page, club)

        assert any("buitenbaan" in r.message.lower() for r in caplog.records)
        assert any("buitenbaan dubbelspel" in r.message.lower() for r in caplog.records)

    def test_enkelspel_wordt_overgeslagen_voor_dubbelspel(self):
        booker = _make_booker(_make_request(game_type="double"))
        slot = self._make_slot_mock(court_label="Enkelspel", time_text="19:30\n90 min")
        page = self._make_page_with_slots([slot])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        result = booker._find_timeslot(page, club)
        assert result is None

    def test_enkelspel_logt_reden(self, caplog):
        import logging
        booker = _make_booker(_make_request(game_type="double"))
        slot = self._make_slot_mock(court_label="Enkelspel binnenbaan", time_text="19:30\n90 min")
        page = self._make_page_with_slots([slot])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        with caplog.at_level(logging.INFO):
            booker._find_timeslot(page, club)

        assert any("enkelspel" in r.message.lower() for r in caplog.records)

    @pytest.mark.parametrize("court_name", [
        "Padelhal | Baan 5 (SINGLE | max. 2 spelers)",
        "Baan 3 (max. 2 spelers)",
    ])
    def test_enkelspel_in_baannaam_wordt_overgeslagen_voor_dubbelspel(self, court_name):
        booker = _make_booker(_make_request(game_type="double"))
        slot = self._make_slot_mock(court_label="binnen • dubbelspel", time_text="19:30\n90 min", court_name=court_name)
        page = self._make_page_with_slots([slot])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        assert booker._find_timeslot(page, club) is None

    def test_dubbelspel_wordt_overgeslagen_voor_enkelspel(self):
        booker = _make_booker(_make_request(game_type="single"))
        slot = self._make_slot_mock(court_label="Dubbelspel", time_text="19:30\n90 min")
        page = self._make_page_with_slots([slot])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        result = booker._find_timeslot(page, club)
        assert result is None

    def test_dubbelspel_logt_reden(self, caplog):
        import logging
        booker = _make_booker(_make_request(game_type="single"))
        slot = self._make_slot_mock(court_label="Dubbelspel binnenbaan", time_text="19:30\n90 min")
        page = self._make_page_with_slots([slot])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        with caplog.at_level(logging.INFO):
            booker._find_timeslot(page, club)

        assert any("dubbelspel" in r.message.lower() for r in caplog.records)

    def test_slot_buiten_tijdvenster_wordt_overgeslagen(self):
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00"))
        slot = self._make_slot_mock(time_text="21:30\n90 min")
        page = self._make_page_with_slots([slot])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        result = booker._find_timeslot(page, club)
        assert result is None

    def test_slot_buiten_tijdvenster_logt_tijd_en_venster(self, caplog):
        import logging
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00"))
        slot = self._make_slot_mock(time_text="21:30\n90 min")
        page = self._make_page_with_slots([slot])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        with caplog.at_level(logging.INFO):
            booker._find_timeslot(page, club)

        assert any(
            "21:30" in r.message and "19:30" in r.message and "21:00" in r.message
            for r in caplog.records
        )

    def test_slot_voor_venster_wordt_overgeslagen(self):
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00"))
        slot = self._make_slot_mock(time_text="18:00\n90 min")
        page = self._make_page_with_slots([slot])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        result = booker._find_timeslot(page, club)
        assert result is None

    def test_slot_precies_op_eindtijd_wordt_geboekt(self):
        # time_end is de laatste starttijd (inclusief)
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00", duration=90))
        slot = self._make_slot_mock(time_text="21:00\n90 min")
        page = self._make_page_with_slots([slot])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        result = booker._find_timeslot(page, club)
        assert result is not None

    def test_verkeerde_duur_wordt_overgeslagen(self):
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00", duration=90))
        slot = self._make_slot_mock(time_text="19:30\n60 min")  # 60, niet 90
        page = self._make_page_with_slots([slot])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        result = booker._find_timeslot(page, club)
        assert result is None

    def test_verkeerde_duur_logt_gevonden_en_gewenste_duur(self, caplog):
        import logging
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00", duration=90))
        slot = self._make_slot_mock(time_text="19:30\n60 min")
        page = self._make_page_with_slots([slot])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        with caplog.at_level(logging.INFO):
            booker._find_timeslot(page, club)

        assert any("60" in r.message and "90" in r.message for r in caplog.records)

    def test_logberichten_bevatten_slotnummer(self, caplog):
        import logging
        booker = _make_booker(_make_request(court_type="indoor"))
        slot = self._make_slot_mock(court_label="Buitenbaan", time_text="19:30\n90 min")
        page = self._make_page_with_slots([slot])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        with caplog.at_level(logging.INFO):
            booker._find_timeslot(page, club)

        # Slotnummer 0 moet in het bericht staan
        assert any("0" in r.message for r in caplog.records if "overgeslagen" in r.message.lower())

    def test_geen_slots_retourneert_none(self):
        booker = _make_booker()
        page = self._make_page_with_slots([])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        result = booker._find_timeslot(page, club)
        assert result is None

    def test_meerdere_slots_eerste_geldig_wordt_gekozen(self):
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00", duration=90))
        # Slot 0: buitenbaan — wordt overgeslagen
        # Slot 1: juiste tijd + duur — wordt gevonden
        slot_buiten = self._make_slot_mock(court_label="Buitenbaan", time_text="19:30\n90 min", slot_id="skip")
        slot_ok = self._make_slot_mock(court_label="Binnenbaan", time_text="19:30\n– 21:00\n90 min", slot_id="found-1")
        page = self._make_page_with_slots([slot_buiten, slot_ok])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        result = booker._find_timeslot(page, club)
        assert result is not None
        assert result["slot_id"] == "found-1"


# ---------------------------------------------------------------------------
# run() high-level
# ---------------------------------------------------------------------------

class TestMeetAndPlayBookerRun:
    def _mock_successful_run(self, booker):
        """Patch sync_playwright en laat een succesvolle boeking simuleren."""
        mock_result = ProviderResult(
            success=True,
            provider="meetandplay",
            booked_date="2026-04-10",
            slot_info={
                "club_name": "Club A",
                "club_address": "Straat 1",
                "court_name": "Baan 1",
                "time_range": "19:30 - 21:00 90 minuten",
                "payment_url": "https://pay.nl",
            },
        )
        return mock_result

    def test_dry_run_retourneert_geen_succes(self):
        booker = _make_booker(_make_request(dry_run=True, weeks_ahead=1))

        mock_pw = MagicMock()
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()

        mock_pw.__enter__ = MagicMock(return_value=mock_pw)
        mock_pw.__exit__ = MagicMock(return_value=False)
        mock_pw.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        booker.session_manager.cookies_exist.return_value = False
        booker.session_manager.is_logged_in.return_value = True

        slot = {
            "slot_id": "slot-1",
            "court_name": "Baan 1",
            "time_range": "19:30 - 21:00",
            "club_name": "Club A",
            "club_address": "Straat 1",
        }

        with patch("providers.meetandplay.booking.sync_playwright", return_value=mock_pw):
            with patch.object(booker, "_search_clubs", return_value=[{"name": "Club A", "url": "http://x", "address": "Straat"}]):
                with patch.object(booker, "_find_timeslot", return_value=slot):
                    result = booker.run()

        assert result.success is False
        assert "dry_run" in result.error

    def test_geen_clubs_retourneert_fout(self):
        booker = _make_booker(_make_request(weeks_ahead=1))

        mock_pw = MagicMock()
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_pw.__enter__ = MagicMock(return_value=mock_pw)
        mock_pw.__exit__ = MagicMock(return_value=False)
        mock_pw.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        booker.session_manager.cookies_exist.return_value = False
        booker.session_manager.is_logged_in.return_value = True

        with patch("providers.meetandplay.booking.sync_playwright", return_value=mock_pw):
            with patch.object(booker, "_search_clubs", return_value=[]):
                result = booker.run()

        assert result.success is False
        assert "Geen clubs" in result.error

    def test_exception_retourneert_fout_result(self):
        booker = _make_booker(_make_request(weeks_ahead=1))

        mock_pw = MagicMock()
        mock_pw.__enter__ = MagicMock(return_value=mock_pw)
        mock_pw.__exit__ = MagicMock(return_value=False)
        mock_pw.chromium.launch.side_effect = Exception("Chromium niet gevonden")

        with patch("providers.meetandplay.booking.sync_playwright", return_value=mock_pw):
            result = booker.run()

        assert result.success is False
        assert "Chromium niet gevonden" in result.error

    def test_site_onbereikbaar_geeft_duidelijke_fout_zonder_traceback(self, caplog):
        from providers.meetandplay.session import SiteUnreachableError

        booker = _make_booker(_make_request(weeks_ahead=1))
        mock_pw = MagicMock()
        mock_pw.__enter__ = MagicMock(return_value=mock_pw)
        mock_pw.__exit__ = MagicMock(return_value=False)
        error = SiteUnreachableError("Meet & Play niet bereikbaar (net::ERR_CONNECTION_REFUSED)")

        with patch("providers.meetandplay.booking.sync_playwright", return_value=mock_pw), \
                patch.object(booker, "_ensure_logged_in", side_effect=error):
            result = booker.run()

        assert result.success is False
        assert "niet bereikbaar" in result.error
        assert "KNLTB_PASSWORD" not in result.error
        assert not any(r.exc_info for r in caplog.records)


# ---------------------------------------------------------------------------
# _book_timeslot — winkelwagen → afrekenen → betaallink
# ---------------------------------------------------------------------------

def _locator(count=1, visible=True, checked=False, text="Toevoegen", inner=None):
    loc = MagicMock()
    loc.count.return_value = count
    loc.first.is_visible.return_value = visible
    loc.first.inner_text.return_value = text
    loc.is_checked.return_value = checked
    # Locators binnen dit element (bijv. de eigen 'Toevoegen'-knop van een slot); standaard geen
    if inner is None:
        inner = MagicMock()
        inner.count.return_value = 0
    loc.first.locator.return_value = inner
    return loc


class TestBookTimeslot:
    SLOT = {"slot_id": "slot-1", "time_range": "19:30", "club_name": "Club A"}

    def _make_page(self, present: dict, urls: list[str], html="Winkelwagen"):
        """`present` mapt een substring van de selector op een locator; de rest ontbreekt."""
        page = MagicMock()
        page.content.return_value = html

        def locator(selector):
            for key, loc in present.items():
                if key in selector:
                    return loc
            return _locator(count=0)

        page.locator.side_effect = locator
        url_iter = iter(urls)
        current = {"url": next(url_iter)}
        type(page).url = property(lambda self: current["url"])

        def advance(*args, **kwargs):
            current["url"] = next(url_iter, current["url"])

        page.wait_for_url.side_effect = advance
        return page

    def test_ontbrekend_anker_geeft_false(self):
        booker = _make_booker()
        page = self._make_page({}, ["https://www.meetandplay.nl/club"])
        assert booker._book_timeslot(page, dict(self.SLOT)) is False

    def test_volledige_flow_zet_betaallink(self):
        booker = _make_booker()
        page = self._make_page(
            {
                "a.timeslot[id='slot-1']": _locator(),
                "Toevoegen": _locator(),
                "checkout(slot-1)": _locator(),
                "input#tos": _locator(),
                "Betaling starten": _locator(),
            },
            ["https://www.meetandplay.nl/club", "https://www.meetandplay.nl/winkelwagen", "https://pay.example/abc"],
        )
        slot = dict(self.SLOT)
        assert booker._book_timeslot(page, slot) is True
        assert slot["payment_url"] == "https://pay.example/abc"

    def test_redirect_naar_login_geeft_false(self):
        booker = _make_booker()
        page = self._make_page(
            {"a.timeslot[id='slot-1']": _locator(), "Toevoegen": _locator(), "checkout(slot-1)": _locator()},
            ["https://www.meetandplay.nl/club", "https://www.meetandplay.nl/inloggen"],
        )
        assert booker._book_timeslot(page, dict(self.SLOT)) is False

    @patch("providers.meetandplay.booking.save_debug_page")
    def test_zonder_betaalknop_telt_niet_als_geboekt(self, save_debug):
        booker = _make_booker()
        page = self._make_page(
            {"a.timeslot[id='slot-1']": _locator(), "Toevoegen": _locator(), "checkout(slot-1)": _locator()},
            ["https://www.meetandplay.nl/club", "https://www.meetandplay.nl/winkelwagen"],
        )
        assert booker._book_timeslot(page, dict(self.SLOT)) is False
        save_debug.assert_called_once_with(page, "debug_payment_failed")

    @patch("providers.meetandplay.booking.save_debug_page")
    def test_betaalpagina_niet_bereikt_probeert_opnieuw_en_telt_niet(self, save_debug):
        booker = _make_booker()
        pay_btn = _locator(text="Betaling starten")
        page = self._make_page(
            {
                "a.timeslot[id='slot-1']": _locator(),
                "Toevoegen": _locator(),
                "checkout(slot-1)": _locator(),
                "input#tos": _locator(),
                "Betaling starten": pay_btn,
            },
            ["https://www.meetandplay.nl/club", "https://meetandplay.nl/club/88018/reserveren"],
        )
        assert booker._book_timeslot(page, dict(self.SLOT)) is False
        assert pay_btn.first.click.call_count == 2
        save_debug.assert_called_once()

    def test_betaalpagina_in_nieuw_tabblad(self):
        booker = _make_booker()
        page = self._make_page(
            {
                "a.timeslot[id='slot-1']": _locator(),
                "Toevoegen": _locator(),
                "checkout(slot-1)": _locator(),
                "input#tos": _locator(),
                "Betaling starten": _locator(),
            },
            ["https://www.meetandplay.nl/club", "https://meetandplay.nl/club/88018/reserveren"],
        )
        popup = MagicMock()
        popup.url = "https://pay.ideal.nl/transactions/abc"
        page.context.pages = [page, popup]
        slot = dict(self.SLOT)
        assert booker._book_timeslot(page, slot) is True
        assert slot["payment_url"] == "https://pay.ideal.nl/transactions/abc"


class TestIsPaymentUrl:
    @pytest.mark.parametrize("url,expected", [
        ("https://pay.ideal.nl/transactions/x", True),
        ("https://boeken.terwegen.nl/nl/cart/abc/checkout", True),
        ("https://meetandplay.nl/club/88018/reserveren", False),
        ("https://www.meetandplay.nl/winkelwagen", False),
        ("", False),
    ])
    def test_herkent_betaalpagina(self, url, expected):
        from providers.meetandplay.booking import _is_payment_url
        assert _is_payment_url(url) is expected


class TestAddToCartEigenKnop:
    def test_klikt_de_knop_van_het_gekozen_slot(self):
        booker = _make_booker()
        own_button = _locator()
        page_button = _locator()
        page = MagicMock()
        page.locator.side_effect = lambda sel: (
            _locator(inner=own_button) if "slot-1" in sel
            else _locator(count=0) if "erase" in sel
            else page_button
        )

        assert booker._add_to_cart(page, "slot-1") is True
        own_button.first.click.assert_called_once()
        page_button.first.click.assert_not_called()


class TestOpenstaandeReservering:
    """Pop-up "Je reservering bij …": niet 'Doorgaan' (laat de andere reservering vervallen) maar annuleren."""

    SLOT = {"slot_id": "slot-1", "time_range": "19:30", "club_name": "Club A"}

    def _page_with_popup(self):
        erase, hide = _locator(), _locator()
        page = MagicMock()
        page.locator.side_effect = lambda sel: (
            erase if "erase" in sel
            else hide if "hide" in sel
            else _locator(inner=_locator()) if "slot-1" in sel
            else _locator()
        )
        return page, erase, hide

    def test_pop_up_wordt_geannuleerd_en_gemeld(self):
        booker = _make_booker()
        page, erase, hide = self._page_with_popup()
        with pytest.raises(PendingReservationError):
            booker._add_to_cart(page, "slot-1")
        hide.first.click.assert_called_once()
        erase.first.click.assert_not_called()

    def test_nieuwe_sessie_en_opnieuw_boeken(self):
        booker = _make_booker()
        club = {"name": "Club A", "url": "http://x", "address": "Straat"}
        slot = {**self.SLOT, "court_name": "Baan 1", "club_address": "Straat", "payment_url": "https://pay"}
        fresh_page = MagicMock()

        with patch.object(booker, "_find_timeslot", return_value=dict(slot)), \
             patch.object(booker, "_book_timeslot", side_effect=[PendingReservationError(), True]) as book, \
             patch.object(booker, "_fresh_session_page", return_value=fresh_page) as fresh:
            result = booker._try_dates_and_clubs(MagicMock(), [datetime(2026, 10, 29)], [club])

        assert result.success is True
        assert result.booked_date == "2026-10-29"
        fresh.assert_called_once()
        assert book.call_args.args[0] is fresh_page


# ---------------------------------------------------------------------------
# Huidige paginalayout: Tijdstip-kiezer en duur-knoppen
# ---------------------------------------------------------------------------

class TestTijdstipKiezer:
    """De slotlijst toont alleen de geselecteerde starttijd; de kiezer bevat alle beschikbare tijden."""

    def _slot(self, start, slot_id):
        slot = MagicMock()
        slot.evaluate.return_value = "binnen • dubbelspel"
        slot.get_attribute.return_value = slot_id
        texts = {"timeslot-time": f"{start} - 21:00 90 minuten", "timeslot-name": "Padelbaan 1"}
        slot.locator.side_effect = lambda sel: MagicMock(
            first=MagicMock(inner_text=MagicMock(return_value=next(v for k, v in texts.items() if k in sel)))
        )
        return slot

    def _page(self, times):
        state = {"selected": times[0], "clicked": []}
        page = MagicMock()
        page.content.return_value = ""

        def locator(sel):
            loc = MagicMock()
            if sel == 'input[name="time"]':
                loc.count.return_value = len(times)
                loc.evaluate_all.return_value = times
            elif sel.startswith('label[for="time-'):
                value = sel.split("time-")[1].split('"')[0]

                def click():
                    state["selected"] = value
                    state["clicked"].append(value)

                loc.click.side_effect = click
            elif "timeslot-container" in sel:
                loc.count.side_effect = lambda: 1
                loc.nth.side_effect = lambda i: self._slot(state["selected"], f"id-{state['selected']}")
            else:
                loc.count.return_value = 0
            return loc

        page.locator.side_effect = locator
        return page, state

    def test_kiest_starttijd_binnen_venster_ook_als_eerste_tijd_ervoor_ligt(self):
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00"))
        page, state = self._page(["18:00", "19:30", "20:00"])

        result = booker._find_timeslot(page, {"name": "Club", "url": "https://x", "address": "Straat"})

        assert result["slot_id"] == "id-19:30"
        assert state["clicked"] == ["19:30"]

    def test_geen_starttijd_in_venster(self):
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00"))
        page, state = self._page(["09:00", "18:00"])

        assert booker._find_timeslot(page, {"name": "Club", "url": "https://x", "address": "Straat"}) is None
        assert state["clicked"] == []


class TestSelectDuration:
    def _page(self, pill_class):
        pill = _locator()
        pill.first.get_attribute.return_value = pill_class
        page = MagicMock()
        page.locator.side_effect = lambda sel: pill if "toggleDuration(90)" in sel else _locator(count=0)
        return page, pill

    def test_klikt_inactieve_duurknop(self):
        page, pill = self._page("badge rounded-pill pill inactive ")
        MeetAndPlayBooker._select_duration(page, 90)
        pill.first.click.assert_called_once()

    def test_actieve_duurknop_niet_nogmaals_klikken(self):
        page, pill = self._page("badge rounded-pill pill active")
        MeetAndPlayBooker._select_duration(page, 90)
        pill.first.click.assert_not_called()


class TestGerichteRun:
    def test_opgegeven_datums_worden_gebruikt(self):
        req = _make_request()
        req["booking_request"]["only_dates"] = ["2026-10-22"]
        booker = _make_booker(req)
        assert [d.date().isoformat() for d in booker._candidate_dates()] == ["2026-10-22"]

    def test_opgegeven_clubs_slaan_zoekpagina_over(self):
        req = _make_request(weeks_ahead=1)
        req["booking_request"]["clubs"] = [{"name": "Club A", "url": "http://x", "address": "Straat"}]
        booker = _make_booker(req)
        mock_pw = MagicMock()
        mock_pw.__enter__ = MagicMock(return_value=mock_pw)
        mock_pw.__exit__ = MagicMock(return_value=False)
        booker.session_manager.is_logged_in.return_value = True

        with patch("providers.meetandplay.booking.sync_playwright", return_value=mock_pw), \
             patch.object(booker, "_search_clubs") as search, \
             patch.object(booker, "_find_timeslot", return_value=None) as find:
            booker.run()

        search.assert_not_called()
        assert find.call_args[0][1]["name"] == "Club A"


class TestBookAtRelease:
    """Gerichte run op een vrijgavemoment: vooraf klaarzetten, daarna verversen tot er een slot is."""

    CLUB = {"name": "Hal", "url": "https://meetandplay.nl/club/1", "address": "Weg 1"}
    DATE = datetime(2026, 10, 29)
    SLOT = {"slot_id": "s1", "club_name": "Hal", "club_address": "Weg 1", "court_name": "Baan 1", "time_range": "20:00"}

    @pytest.fixture
    def clock(self, monkeypatch):
        """Nepklok: `sleep` schuift de tijd op; begint 60 s vóór de vrijgave."""
        from datetime import timedelta
        from zoneinfo import ZoneInfo

        import providers.meetandplay.booking as booking_mod

        release = datetime(2026, 10, 15, 20, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
        state = {"now": release - timedelta(seconds=60), "sleeps": []}

        class FakeDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return state["now"]

        def fake_sleep(seconds):
            state["sleeps"].append(seconds)
            state["now"] += timedelta(seconds=seconds)

        monkeypatch.setattr(booking_mod, "datetime", FakeDatetime)
        monkeypatch.setattr(booking_mod.time_mod, "sleep", fake_sleep)
        state["release"] = release
        return state

    def _booker(self, picks, component_id="lw1"):
        booker = _make_booker()
        booker._context = MagicMock()
        booker._open_club_day = MagicMock()
        booker._livewire_date_id = MagicMock(return_value=component_id)
        booker._refresh_livewire = MagicMock()
        booker._pick_slot = MagicMock(side_effect=picks)
        booker._book_found_slot = MagicMock(
            return_value=ProviderResult(success=True, provider="meetandplay", booked_date="2026-10-29"))
        return booker

    def test_wacht_tot_vlak_voor_vrijgave_en_boekt_zodra_slot_verschijnt(self, clock):
        booker = self._booker([None, None, dict(self.SLOT)])
        page = MagicMock()

        result = booker._book_at_release(page, [self.DATE], [self.CLUB], clock["release"])

        assert result.success
        booker._open_club_day.assert_called_once_with(page, self.CLUB, self.DATE)
        assert clock["sleeps"][0] == 57  # tot RELEASE_LEAD (3 s) vóór het moment
        assert booker._refresh_livewire.call_count == 3
        booker._book_found_slot.assert_called_once_with(page, self.CLUB, self.DATE, self.SLOT)

    def test_geen_slot_stopt_na_pollvenster(self, clock):
        booker = self._booker(lambda page, club, verbose: None)

        result = booker._book_at_release(MagicMock(), [self.DATE], [self.CLUB], clock["release"])

        assert not result.success
        assert clock["now"] >= clock["release"] + RELEASE_POLL
        verbose = [c.kwargs["verbose"] for c in booker._pick_slot.call_args_list]
        assert verbose[0] and verbose[-1] and not any(verbose[1:-1])  # alleen eerste en laatste ronde info
        booker._book_found_slot.assert_not_called()

    def test_vrijgave_al_voorbij_een_ronde_zonder_wachten(self, clock):
        clock["now"] = clock["release"] + RELEASE_POLL
        booker = self._booker([None])

        result = booker._book_at_release(MagicMock(), [self.DATE], [self.CLUB], clock["release"])

        assert not result.success
        assert booker._refresh_livewire.call_count == 1
        assert clock["sleeps"] == []

    def test_elke_clubdag_een_eigen_tabblad(self, clock):
        other = {**self.CLUB, "name": "Lake", "url": "https://meetandplay.nl/club/2"}
        booker = self._booker([None, dict(self.SLOT, club_name="Lake")])
        page = MagicMock()

        booker._book_at_release(page, [self.DATE], [self.CLUB, other], clock["release"])

        tabs = [c.args[0] for c in booker._open_club_day.call_args_list]
        assert tabs[0] is page and tabs[1] is booker._context.new_page.return_value
        assert booker._book_found_slot.call_args.args[1] == other

    def test_zonder_livewire_component_gewone_zoektocht(self, clock):
        booker = self._booker([None], component_id=None)
        booker._try_dates_and_clubs = MagicMock(return_value=ProviderResult(success=False, provider="meetandplay"))

        booker._book_at_release(MagicMock(), [self.DATE], [self.CLUB], clock["release"])

        booker._try_dates_and_clubs.assert_called_once()
        booker._refresh_livewire.assert_not_called()

    def test_mislukte_verversing_laat_tabblad_vallen(self, clock):
        booker = self._booker([None])
        booker._refresh_livewire.side_effect = Exception("Target closed")

        result = booker._book_at_release(MagicMock(), [self.DATE], [self.CLUB], clock["release"])

        assert not result.success
        assert booker._refresh_livewire.call_count == 1

    def test_mislukte_boeking_opent_dag_opnieuw_en_ververst_verder(self, clock):
        booker = self._booker([dict(self.SLOT), dict(self.SLOT)])
        booker._book_found_slot.side_effect = [
            None, ProviderResult(success=True, provider="meetandplay", booked_date="2026-10-29")]

        result = booker._book_at_release(MagicMock(), [self.DATE], [self.CLUB], clock["release"])

        assert result.success
        assert booker._open_club_day.call_count == 2

    def test_run_gebruikt_vrijgaveflow_bij_release_at(self):
        req = _make_request(weeks_ahead=1)
        req["booking_request"]["clubs"] = [self.CLUB]
        req["booking_request"]["only_dates"] = ["2026-10-29"]
        req["booking_request"]["release_at"] = "2026-10-15T20:00:00+02:00"
        booker = _make_booker(req)
        mock_pw = MagicMock()
        mock_pw.__enter__ = MagicMock(return_value=mock_pw)
        mock_pw.__exit__ = MagicMock(return_value=False)
        booker.session_manager.is_logged_in.return_value = True

        with patch("providers.meetandplay.booking.sync_playwright", return_value=mock_pw), \
             patch.object(booker, "_within_radius", side_effect=lambda clubs, **kw: clubs), \
             patch.object(booker, "_book_at_release",
                          return_value=ProviderResult(success=False, provider="meetandplay")) as release:
            booker.run()

        assert release.call_args.args[3].isoformat() == "2026-10-15T20:00:00+02:00"


class TestPendingOrder:
    """Na de betaallink: de reservering in je account opzoeken (orderpagina + vasthoudtijd)."""

    def _page(self, cards, order_text="De reservering wordt tot 13:09 vastgehouden, daarna wordt de reservering geannuleerd"):
        page = MagicMock()
        card_mocks = []
        for text, href in cards:
            card = MagicMock()
            card.inner_text.return_value = text
            card.get_attribute.return_value = href
            card_mocks.append(card)
        page.locator.return_value.all.return_value = card_mocks
        page.inner_text.return_value = order_text
        return page

    def _booker(self, page):
        booker = _make_booker()
        booker._context = MagicMock()
        booker._context.new_page.return_value = page
        return booker

    SLOT = {"club_name": "Hal 0172", "time_range": "10:00 - 11:30 90 minuten"}

    def test_vindt_openstaande_order_en_vasthoudtijd(self):
        page = self._page([
            ("22 OKT 20:00 - 21:30 Padelbaan 4 - Tennis en Padel Meerzicht Betaald", "/mijn-aankopen/order/1/a"),
            ("11 NOV 10:00 - 11:30 90 minuten Padelbaan 1 - Hal 0172 In afwachting van betaling", "/mijn-aankopen/order/88191/b"),
        ])
        result = self._booker(page)._pending_order(datetime(2026, 11, 11), self.SLOT)
        assert result == {"order_url": "https://meetandplay.nl/mijn-aankopen/order/88191/b", "hold_until": "13:09"}
        page.close.assert_called_once()

    def test_betaalde_of_andere_order_telt_niet(self):
        page = self._page([("11 NOV 10:00 - 11:30 Padelbaan 1 - Hal 0172 Betaald", "/mijn-aankopen/order/88191/b")])
        assert self._booker(page)._pending_order(datetime(2026, 11, 11), self.SLOT) == {}

    def test_andere_starttijd_telt_niet(self):
        page = self._page([("11 NOV 12:00 - 13:30 Padelbaan 1 - Hal 0172 In afwachting van betaling", "/x")])
        assert self._booker(page)._pending_order(datetime(2026, 11, 11), self.SLOT) == {}

    def test_zonder_vasthoudtekst_wel_orderlink(self):
        page = self._page([("11 NOV 10:00 - 11:30 Hal 0172 In afwachting van betaling", "https://meetandplay.nl/o/1")],
                          order_text="Reservering")
        assert self._booker(page)._pending_order(datetime(2026, 11, 11), self.SLOT) == {
            "order_url": "https://meetandplay.nl/o/1", "hold_until": ""}

    def test_fout_geeft_leeg_resultaat(self):
        page = self._page([])
        page.goto.side_effect = Exception("timeout")
        assert self._booker(page)._pending_order(datetime(2026, 11, 11), self.SLOT) == {}

    def test_boekingsresultaat_bevat_order(self):
        booker = _make_booker()
        booker._book_timeslot = MagicMock(return_value=True)
        booker._pending_order = MagicMock(return_value={"order_url": "https://o", "hold_until": "13:09"})
        slot = {"club_name": "Hal 0172", "club_address": "", "court_name": "Baan 1", "time_range": "10:00",
                "payment_url": "https://www.mollie.com/x"}
        result = booker._book_found_slot(MagicMock(), {"name": "Hal 0172"}, datetime(2026, 11, 11), slot)
        assert result.slot_info["order_url"] == "https://o" and result.slot_info["hold_until"] == "13:09"


class TestDistanceOption:
    @pytest.mark.parametrize("radius,expected", [
        (10, "10"), (20, "20"), (15, "20"), (3, "5"), (75, "100"), ("20", "20"), (1000, "400"),
    ])
    def test_kiest_kleinste_dekkende_straal(self, radius, expected):
        from providers.meetandplay.booking import _distance_option
        assert _distance_option(radius) == expected


BOSKOOP = [52.07, 4.66]
SEARCH_HTML = (
    '<div wire:snapshot="{&quot;clubs&quot;:[{&quot;id&quot;:1,&quot;slug&quot;:&quot;88191&quot;,'
    '&quot;name&quot;:&quot;Hal 0172&quot;,&quot;latitude&quot;:&quot;52.1550&quot;,&quot;longitude&quot;:&quot;4.7650&quot;},'
    '{&quot;id&quot;:2,&quot;slug&quot;:&quot;88018&quot;,&quot;name&quot;:&quot;TERWEGEN \\&quot;INDOOR\\&quot;&quot;,'
    '&quot;latitude&quot;:&quot;52.2580&quot;,&quot;longitude&quot;:&quot;4.5260&quot;},'
    '{&quot;id&quot;:3,&quot;slug&quot;:&quot;99&quot;,&quot;name&quot;:&quot;Zonder&quot;,&quot;latitude&quot;:null,&quot;longitude&quot;:null}]}">'
)


class TestClubAfstand:
    def test_coordinaten_uit_zoekpagina(self):
        from providers.meetandplay.booking import parse_club_coordinates
        coords = parse_club_coordinates(SEARCH_HTML)
        assert coords == {"88191": [52.155, 4.765], "88018": [52.258, 4.526]}

    def test_afstand_boskoop_nieuwkoop(self):
        from providers.meetandplay.booking import distance_km
        assert 11 < distance_km(BOSKOOP, [52.155, 4.765]) < 13

    def _booker(self, tmp_path, radius=15, lat=BOSKOOP[0], lon=BOSKOOP[1]):
        request = _make_request()
        request["booking_request"]["location"] = {
            "city": "Boskoop", "radius_km": radius, "latitude": lat, "longitude": lon,
        }
        request["provider_config"] = {"cookies_file": str(tmp_path / ".cookies.json")}
        return _make_booker(request)

    CLUBS = [
        {"name": "Hal 0172", "url": "https://meetandplay.nl/club/88191", "address": ""},
        {"name": "TERWEGEN INDOOR", "url": "https://meetandplay.nl/club/88018", "address": ""},
        {"name": "Onbekend", "url": "https://meetandplay.nl/club/12345", "address": ""},
    ]

    def test_clubs_buiten_straal_vallen_af(self, tmp_path):
        from providers.meetandplay.booking import parse_club_coordinates
        booker = self._booker(tmp_path)
        kept = booker._within_radius(self.CLUBS, parse_club_coordinates(SEARCH_HTML))
        assert [c["name"] for c in kept] == ["Hal 0172", "Onbekend"]

    def test_gerichte_run_gebruikt_bewaarde_coordinaten(self, tmp_path):
        from providers.meetandplay.booking import parse_club_coordinates
        booker = self._booker(tmp_path)
        booker._save_club_coords(parse_club_coordinates(SEARCH_HTML))
        assert [c["name"] for c in booker._within_radius(self.CLUBS)] == ["Hal 0172", "Onbekend"]

    def test_zonder_eigen_locatie_geen_filter(self, tmp_path):
        booker = self._booker(tmp_path, lat=None, lon=None)
        assert booker._within_radius(self.CLUBS, {"88018": [52.258, 4.526]}) == self.CLUBS

    def test_onbekende_club_via_clubpagina(self, tmp_path):
        booker = self._booker(tmp_path)
        page = MagicMock()
        page.content.return_value = "initMap([...], 10, {latitude: 51.9109862, longitude: 4.616995}, false)"
        clubs = [{"name": "My Padel Club Krimpen", "url": "https://meetandplay.nl/club/88115", "address": ""}]
        assert booker._within_radius(clubs, {}, page) == []
        assert booker._load_club_coords() == {"88115": [51.9109862, 4.616995]}
