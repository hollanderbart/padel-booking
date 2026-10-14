"""
Unit tests voor fetch_bookings.py — geen browser of netwerk nodig.
"""

from unittest.mock import MagicMock, patch

import pytest

from fetch_bookings import (
    _first_text,
    _parse_dutch_date,
    _playtomic_match_to_booking,
    fetch_playtomic_bookings,
)
from providers.playtomic.client import PlaytomicAuthError


class TestParseDutchDate:
    @pytest.mark.parametrize(("raw", "expected"), [
        ("2026-04-03", "2026-04-03"),
        ("3-4-2026", "2026-04-03"),
        ("03/04/2026", "2026-04-03"),
        ("donderdag 3 april 2026", "2026-04-03"),
        ("Vr 17 okt 2026", "2026-10-17"),
        ("3 smarch 2026", ""),
        ("geen datum", ""),
    ])
    def test_formaten(self, raw, expected):
        assert _parse_dutch_date(raw) == expected


class TestPlaytomicMatchToBooking:
    def test_volledige_match(self):
        booking = _playtomic_match_to_booking({
            "start_date": "2026-04-09T19:30:00",
            "duration": 90,
            "match_id": "m1",
            "tenant": {"tenant_name": "Club", "address": {"street": "Straat 1", "city": "Stad"}},
            "resource": {"resource_name": "Baan 2"},
        })
        assert booking["booked_date"] == "2026-04-09"
        assert booking["time_range"] == "19:30 - 21:00"
        assert booking["club_address"] == "Straat 1, Stad"
        assert booking["court_name"] == "Baan 2"
        assert booking["payment_url"] == "https://playtomic.io/booking/m1"

    def test_zonder_startdatum_geeft_none(self):
        assert _playtomic_match_to_booking({}) is None

    def test_standaardwaarden(self):
        booking = _playtomic_match_to_booking({"start_date": "2026-04-09T19:30:00"})
        assert booking["court_name"] == "Padelbaan"
        assert booking["payment_url"] == ""


class TestFetchPlaytomicBookings:
    def test_zet_matches_om_naar_boekingen(self, tmp_path):
        with patch("fetch_bookings.PlaytomicClient") as MockClient:
            MockClient.return_value.get_upcoming_matches.return_value = [
                {"start_date": "2026-04-09T19:30:00", "duration": 90},
                {"start_date": ""},
            ]
            bookings = fetch_playtomic_bookings("a@b.nl", "pw", str(tmp_path / "t.json"))
        assert [b["booked_date"] for b in bookings] == ["2026-04-09"]

    def test_auth_fout_geeft_lege_lijst(self, tmp_path):
        with patch("fetch_bookings.PlaytomicClient") as MockClient:
            MockClient.return_value.get_upcoming_matches.side_effect = PlaytomicAuthError("nee")
            assert fetch_playtomic_bookings("a@b.nl", "pw", str(tmp_path / "t.json")) == []

    def test_netwerkfout_geeft_lege_lijst(self, tmp_path):
        with patch("fetch_bookings.PlaytomicClient") as MockClient:
            MockClient.return_value.get_upcoming_matches.side_effect = OSError("timeout")
            assert fetch_playtomic_bookings("a@b.nl", "pw", str(tmp_path / "t.json")) == []


class TestFirstText:
    def test_eerste_niet_lege_selector_wint(self):
        def locator(selector):
            loc = MagicMock()
            loc.count.return_value = 1
            loc.first.inner_text.return_value = {".a": "  ", ".b": "Club X\nextra"}[selector]
            return loc

        card = MagicMock()
        card.locator.side_effect = locator
        assert _first_text(card, [".a", ".b"], first_line=True) == "Club X"
