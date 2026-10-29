"""
Unit tests voor fetch_bookings.py — geen browser of netwerk nodig.
"""

from datetime import date
from unittest.mock import patch

from fetch_bookings import (
    _infer_date,
    _parse_order_card,
    _playtomic_match_to_booking,
    fetch_playtomic_bookings,
)
from providers.playtomic.client import PlaytomicAuthError


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
            assert fetch_playtomic_bookings("a@b.nl", "pw", str(tmp_path / "t.json")) is None

    def test_netwerkfout_geeft_lege_lijst(self, tmp_path):
        with patch("fetch_bookings.PlaytomicClient") as MockClient:
            MockClient.return_value.get_upcoming_matches.side_effect = OSError("timeout")
            assert fetch_playtomic_bookings("a@b.nl", "pw", str(tmp_path / "t.json")) is None


class _FakeLocator:
    """Minimale Playwright-locator: een lijst teksten per selector."""

    def __init__(self, texts: list[str]):
        self._texts = texts
        self.first = self._Item(texts[0] if texts else "")
        self.last = self._Item(texts[-1] if texts else "")

    class _Item:
        def __init__(self, text):
            self._text = text

        def inner_text(self):
            return self._text

    def count(self):
        return len(self._texts)


class _FakeCard:
    def __init__(self, fields: dict[str, list[str]], href: str = ""):
        self._fields = fields
        self._href = href

    def locator(self, selector):
        return _FakeLocator(self._fields.get(selector, []))

    def get_attribute(self, name):
        return self._href if name == "href" else None

    def inner_text(self):
        return " ".join(t for texts in self._fields.values() for t in texts)


def _order_card(day="22", month="okt", status="Betaald"):
    """Opbouw van een kaart op meetandplay.nl/mijn-aankopen (oktober 2026)."""
    return _FakeCard({
        ".timeslot-date-day": [day],
        ".timeslot-date-month": [month],
        ".timeslot-time": ["20:00 - 21:30\n\n 90 minuten"],
        ".timeslot-name": ["Padelbaan 4 - Tennis en Padel Meerzicht\n4 spelers - Tennis en Padel Meerzicht", status],
    }, href="https://meetandplay.nl/mijn-aankopen/order/28541/abc")


class TestInferDate:
    def test_later_dit_jaar(self):
        assert _infer_date(22, 10, date(2026, 10, 14)) == date(2026, 10, 22)

    def test_vandaag_telt_mee(self):
        assert _infer_date(14, 10, date(2026, 10, 14)) == date(2026, 10, 14)

    def test_jaarwisseling(self):
        assert _infer_date(8, 1, date(2026, 12, 20)) == date(2027, 1, 8)

    def test_net_verstreken_blijft_dit_jaar(self):
        # 'Aankomend' toonde op 23 okt nog de reservering van 22 okt — dat is niet 2027
        assert _infer_date(22, 10, date(2026, 10, 23)) == date(2026, 10, 22)

    def test_verstreken_rond_jaarwisseling(self):
        assert _infer_date(30, 12, date(2027, 1, 2)) == date(2026, 12, 30)

    def test_29_februari(self):
        assert _infer_date(29, 2, date(2027, 12, 1)) == date(2028, 2, 29)


class TestParseOrderCard:
    TODAY = date(2026, 10, 14)

    def test_verstreken_reservering_wordt_overgeslagen(self):
        assert _parse_order_card(_order_card(), date(2026, 10, 23)) is None

    def test_betaalde_reservering(self):
        booking = _parse_order_card(_order_card(), self.TODAY)
        assert booking == {
            "booked_date": "2026-10-22",
            "provider": "meetandplay",
            "club_name": "Tennis en Padel Meerzicht",
            "club_address": "",
            "court_name": "Padelbaan 4",
            "time_range": "20:00 - 21:30",
            "payment_url": "https://meetandplay.nl/mijn-aankopen/order/28541/abc",
            "status": "betaald",
        }

    def test_geannuleerde_reservering_telt_niet(self):
        assert _parse_order_card(_order_card(status="Geannuleerd"), self.TODAY) is None

    def test_onbekende_maand_geeft_none(self):
        assert _parse_order_card(_order_card(month="xyz"), self.TODAY) is None

    def test_maand_met_punt(self):
        assert _parse_order_card(_order_card(month="Nov."), self.TODAY)["booked_date"] == "2026-11-22"
