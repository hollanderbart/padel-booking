"""
Unit tests voor providers/base.py — ProviderResult en read_request.
"""

import io
import json
import sys
from unittest.mock import patch

import pytest

from providers.base import ProviderResult, SlotInfo, read_request


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
