"""
Unit tests voor providers/meetandplay/booking.py — geen browser nodig.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from providers.meetandplay.booking import MeetAndPlayBooker, WEEKDAYS
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
        assert dates[0] > datetime.now()

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

    def test_slot_precies_op_eindtijd_wordt_overgeslagen(self):
        # 21:00 is exclusief
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00", duration=90))
        slot = self._make_slot_mock(time_text="21:00\n90 min")
        page = self._make_page_with_slots([slot])
        club = {"name": "TestClub", "url": "https://example.com", "address": "Straat 1"}

        result = booker._find_timeslot(page, club)
        assert result is None

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
