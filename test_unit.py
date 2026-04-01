"""
Unit tests voor Padel Booking Orchestrator — geen browser of credentials nodig.

Uitvoeren:
  pytest test_unit.py -v
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from orchestrator import (
    append_booking_history,
    write_last_run,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slot_info(**overrides) -> dict:
    base = {
        "club_name": "Testclub",
        "club_address": "Teststraat 1, Teststad",
        "court_name": "Baan 1",
        "time_range": "19:30 - 21:00 90 minuten",
        "payment_url": "https://example.com/pay/123",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# write_last_run
# ---------------------------------------------------------------------------

class TestWriteLastRun:
    def test_schrijft_bestand_bij_success(self, tmp_path):
        f = tmp_path / "last_run.json"
        write_last_run(f, success=True)

        data = json.loads(f.read_text())
        assert data["success"] is True
        assert "last_run" in data
        datetime.fromisoformat(data["last_run"])

    def test_schrijft_bestand_bij_failure(self, tmp_path):
        f = tmp_path / "last_run.json"
        write_last_run(f, success=False)

        data = json.loads(f.read_text())
        assert data["success"] is False

    def test_overschrijft_vorig_bestand(self, tmp_path):
        f = tmp_path / "last_run.json"
        write_last_run(f, success=False)
        write_last_run(f, success=True)

        data = json.loads(f.read_text())
        assert data["success"] is True

    def test_schrijft_provider_veld(self, tmp_path):
        f = tmp_path / "last_run.json"
        write_last_run(f, success=True, provider="playtomic")

        data = json.loads(f.read_text())
        assert data["winning_provider"] == "playtomic"

    def test_stille_fout_bij_ontbrekende_directory(self, tmp_path):
        f = tmp_path / "bestaat_niet" / "last_run.json"
        write_last_run(f, success=True)
        assert not f.exists()


# ---------------------------------------------------------------------------
# append_booking_history
# ---------------------------------------------------------------------------

class TestAppendBookingHistory:
    def test_schrijft_eerste_entry(self, tmp_path):
        f = tmp_path / "booking_history.json"
        append_booking_history(f, "2026-04-03", _slot_info(), "meetandplay")

        history = json.loads(f.read_text())
        assert len(history) == 1
        entry = history[0]
        assert entry["booked_date"] == "2026-04-03"
        assert entry["club_name"] == "Testclub"
        assert entry["court_name"] == "Baan 1"
        assert entry["time_range"] == "19:30 - 21:00 90 minuten"
        assert entry["payment_url"] == "https://example.com/pay/123"
        assert entry["provider"] == "meetandplay"

    def test_nieuwste_entry_staat_bovenaan(self, tmp_path):
        f = tmp_path / "booking_history.json"
        append_booking_history(f, "2026-04-03", _slot_info(club_name="Eerste"), "meetandplay")
        append_booking_history(f, "2026-04-10", _slot_info(club_name="Tweede"), "playtomic")

        history = json.loads(f.read_text())
        assert history[0]["club_name"] == "Tweede"
        assert history[1]["club_name"] == "Eerste"

    def test_maximaal_20_entries(self, tmp_path):
        f = tmp_path / "booking_history.json"
        for i in range(25):
            append_booking_history(f, "2026-04-01", _slot_info(club_name=f"Club {i}"), "meetandplay")

        history = json.loads(f.read_text())
        assert len(history) == 20

    def test_voegt_toe_aan_bestaand_bestand(self, tmp_path):
        f = tmp_path / "booking_history.json"
        existing = [{"booked_date": "2026-03-01", "club_name": "Oud"}]
        f.write_text(json.dumps(existing))

        append_booking_history(f, "2026-04-03", _slot_info(), "meetandplay")

        history = json.loads(f.read_text())
        assert len(history) == 2
        assert history[0]["club_name"] == "Testclub"
        assert history[1]["club_name"] == "Oud"

    def test_stille_fout_bij_ontbrekende_directory(self, tmp_path):
        f = tmp_path / "bestaat_niet" / "booking_history.json"
        append_booking_history(f, "2026-04-03", _slot_info(), "meetandplay")
        assert not f.exists()
