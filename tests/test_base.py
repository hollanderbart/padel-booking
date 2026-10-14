"""
Unit tests voor providers/base.py — ProviderResult, read_request en gedeelde helpers.
"""

import io
import json
from datetime import datetime
from unittest.mock import patch

import pytest

from providers import base
from providers.base import (
    ProviderResult,
    SlotInfo,
    booking_days,
    hhmm_to_minutes,
    read_request,
    upcoming_booking_dates,
    without_skip_dates,
)


class TestProviderResult:
    def test_write_stdout_produceert_geldige_json(self, capsys):
        result = ProviderResult(success=True, provider="playtomic", booked_date="2026-04-10", slot_info={"club_name": "X"})
        result.write_stdout()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["success"] is True
        assert data["provider"] == "playtomic"
        assert data["booked_date"] == "2026-04-10"

    def test_write_stdout_none_velden_als_null(self, capsys):
        result = ProviderResult(success=False, provider="meetandplay")
        result.write_stdout()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["booked_date"] is None
        assert data["slot_info"] is None
        assert data["error"] is None

    def test_write_stdout_met_fout(self, capsys):
        result = ProviderResult(success=False, provider="playtomic", error="Geen slot gevonden")
        result.write_stdout()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["error"] == "Geen slot gevonden"

    def test_alle_velden_aanwezig_in_output(self, capsys):
        result = ProviderResult(success=True, provider="meetandplay", booked_date="2026-04-10", slot_info={}, error=None)
        result.write_stdout()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert set(data.keys()) == {"success", "provider", "booked_date", "slot_info", "error"}


class TestSlotInfo:
    def test_standaard_payment_url_is_leeg(self):
        s = SlotInfo(club_name="X", club_address="Y", court_name="Z", time_range="19:30")
        assert s.payment_url == ""

    def test_alle_velden_worden_gezet(self):
        s = SlotInfo(club_name="Club", club_address="Straat 1", court_name="Baan 2", time_range="19:30 - 21:00", payment_url="https://pay.nl")
        assert s.club_name == "Club"
        assert s.payment_url == "https://pay.nl"


class TestReadRequest:
    def test_leest_geldige_json_van_stdin(self):
        data = {"booking_request": {"day": "thursday"}, "dry_run": False}
        with patch("sys.stdin", io.StringIO(json.dumps(data))):
            result = read_request()
        assert result["booking_request"]["day"] == "thursday"
        assert result["dry_run"] is False

    def test_ongeldige_json_geeft_exception(self):
        with patch("sys.stdin", io.StringIO("GEEN_JSON")):
            with pytest.raises(json.JSONDecodeError):
                read_request()


# ---------------------------------------------------------------------------
# Gedeelde datum- en tijdhelpers
# ---------------------------------------------------------------------------

def _freeze_now(monkeypatch, frozen: datetime) -> None:
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(base, "datetime", FrozenDatetime)


class TestHhmmToMinutes:
    def test_uren_en_minuten(self):
        assert hhmm_to_minutes("19:30") == 19 * 60 + 30

    def test_seconden_worden_genegeerd(self):
        assert hhmm_to_minutes("08:05:59") == 8 * 60 + 5

    def test_ongeldige_invoer_gooit_valueerror(self):
        with pytest.raises(ValueError):
            hhmm_to_minutes("geen tijd")


class TestUpcomingBookingDates:
    # 2026-04-09 is een donderdag
    def test_vandaag_telt_mee_als_starttijd_nog_niet_verstreken(self, monkeypatch):
        _freeze_now(monkeypatch, datetime(2026, 4, 9, 0, 1))
        dates = upcoming_booking_dates("thursday", "19:30", count=2)
        assert [d.date().isoformat() for d in dates] == ["2026-04-09", "2026-04-16"]

    def test_vandaag_telt_niet_mee_na_starttijd(self, monkeypatch):
        _freeze_now(monkeypatch, datetime(2026, 4, 9, 20, 0))
        dates = upcoming_booking_dates("thursday", "19:30", count=1)
        assert dates[0].date().isoformat() == "2026-04-16"

    def test_andere_weekdag(self, monkeypatch):
        _freeze_now(monkeypatch, datetime(2026, 4, 9, 12, 0))
        dates = upcoming_booking_dates("maandag", "19:30", count=1)
        assert dates[0].date().isoformat() == "2026-04-13"

    def test_ongeldige_dag_gooit_valueerror(self):
        with pytest.raises(ValueError, match="Ongeldige dag"):
            upcoming_booking_dates("quatember")

    def test_meerdere_dagen_chronologisch(self):
        now = datetime(2026, 4, 9, 12, 0)
        dates = upcoming_booking_dates(["thursday", "tuesday"], "19:30", count=2, now=now)
        assert [d.date().isoformat() for d in dates] == ["2026-04-09", "2026-04-14", "2026-04-16", "2026-04-21"]


class TestBookingDays:
    def test_lijst_wordt_gesorteerd_en_ontdubbeld(self):
        assert booking_days({"days": ["Thursday", "tuesday", "thursday"]}) == ["tuesday", "thursday"]

    def test_oude_enkele_dag(self):
        assert booking_days({"day": "thursday"}) == ["thursday"]

    def test_days_wint_van_day(self):
        assert booking_days({"days": ["monday"], "day": "thursday"}) == ["monday"]

    def test_standaard_donderdag(self):
        assert booking_days({}) == ["thursday"]

    def test_ongeldige_dag(self):
        with pytest.raises(ValueError, match="Ongeldige dag"):
            booking_days({"days": ["thursday", "quatember"]})


class TestWithoutSkipDates:
    def test_filtert_geboekte_datums(self):
        dates = [datetime(2026, 4, 9), datetime(2026, 4, 16)]
        assert without_skip_dates(dates, ["2026-04-09"]) == [datetime(2026, 4, 16)]

    def test_lege_skiplijst_geeft_alles_terug(self):
        dates = [datetime(2026, 4, 9)]
        assert without_skip_dates(dates, []) == dates


class TestRunProviderCli:
    def test_schrijft_resultaat_van_booker(self, capsys):
        booker = type("B", (), {"run": lambda self: ProviderResult(success=True, provider="x")})
        with patch("sys.stdin", io.StringIO("{}")):
            base.run_provider_cli("x", lambda request: booker())
        assert json.loads(capsys.readouterr().out)["success"] is True

    def test_exception_wordt_foutresultaat(self, capsys):
        def factory(request):
            raise RuntimeError("kapot")

        with patch("sys.stdin", io.StringIO("{}")):
            base.run_provider_cli("x", factory)
        data = json.loads(capsys.readouterr().out)
        assert data == {"success": False, "provider": "x", "booked_date": None, "slot_info": None, "error": "kapot"}
