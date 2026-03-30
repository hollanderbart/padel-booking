"""
Unit tests voor KNLTB Padel Booking — geen browser of credentials nodig.

Uitvoeren:
  pytest test_unit.py -v
"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from booking import PadelBooker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_booker(tmp_path: Path) -> PadelBooker:
    """Maak een PadelBooker aan met een minimale config in tmp_path."""
    config = {
        "location": {"city": "Teststad", "radius_km": 10},
        "booking": {
            "day": "thursday",
            "time_start": "19:30",
            "time_end": "21:00",
            "duration_minutes": 90,
            "court_type": "indoor",
            "game_type": "double",
        },
        "session": {
            "cookies_file": str(tmp_path / ".session_cookies.json"),
            "state_file": str(tmp_path / ".booking_state.json"),
            "history_file": str(tmp_path / "booking_history.json"),
            "last_run_file": str(tmp_path / "last_run.json"),
        },
    }
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(config))
    return PadelBooker(config_path=str(config_file))


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
# _write_last_run
# ---------------------------------------------------------------------------

class TestWriteLastRun:
    def test_schrijft_bestand_bij_success(self, tmp_path):
        booker = _make_booker(tmp_path)
        booker._write_last_run(success=True)

        data = json.loads(booker.last_run_file.read_text())
        assert data["success"] is True
        assert "last_run" in data
        # ISO-formaat: "2026-03-30T14:40:30"
        datetime.fromisoformat(data["last_run"])

    def test_schrijft_bestand_bij_failure(self, tmp_path):
        booker = _make_booker(tmp_path)
        booker._write_last_run(success=False)

        data = json.loads(booker.last_run_file.read_text())
        assert data["success"] is False

    def test_overschrijft_vorig_bestand(self, tmp_path):
        booker = _make_booker(tmp_path)
        booker._write_last_run(success=False)
        booker._write_last_run(success=True)

        data = json.loads(booker.last_run_file.read_text())
        assert data["success"] is True

    def test_stille_fout_bij_ontbrekende_directory(self, tmp_path):
        booker = _make_booker(tmp_path)
        booker.last_run_file = tmp_path / "bestaat_niet" / "last_run.json"
        # Mag geen exception gooien — wordt stil gelogd
        booker._write_last_run(success=True)
        assert not booker.last_run_file.exists()


# ---------------------------------------------------------------------------
# _append_booking_history
# ---------------------------------------------------------------------------

class TestAppendBookingHistory:
    def test_schrijft_eerste_entry(self, tmp_path):
        booker = _make_booker(tmp_path)
        booked_date = datetime(2026, 4, 3)
        booker._append_booking_history(booked_date, _slot_info())

        history = json.loads(booker.history_file.read_text())
        assert len(history) == 1
        entry = history[0]
        assert entry["booked_date"] == "2026-04-03"
        assert entry["club_name"] == "Testclub"
        assert entry["court_name"] == "Baan 1"
        assert entry["time_range"] == "19:30 - 21:00 90 minuten"
        assert entry["payment_url"] == "https://example.com/pay/123"

    def test_nieuwste_entry_staat_bovenaan(self, tmp_path):
        booker = _make_booker(tmp_path)
        booker._append_booking_history(datetime(2026, 4, 3), _slot_info(club_name="Eerste"))
        booker._append_booking_history(datetime(2026, 4, 10), _slot_info(club_name="Tweede"))

        history = json.loads(booker.history_file.read_text())
        assert history[0]["club_name"] == "Tweede"
        assert history[1]["club_name"] == "Eerste"

    def test_maximaal_20_entries(self, tmp_path):
        booker = _make_booker(tmp_path)
        for i in range(25):
            booker._append_booking_history(
                datetime(2026, 4, 1),
                _slot_info(club_name=f"Club {i}"),
            )

        history = json.loads(booker.history_file.read_text())
        assert len(history) == 20

    def test_voegt_toe_aan_bestaand_bestand(self, tmp_path):
        booker = _make_booker(tmp_path)
        existing = [{"booked_date": "2026-03-01", "club_name": "Oud"}]
        booker.history_file.write_text(json.dumps(existing))

        booker._append_booking_history(datetime(2026, 4, 3), _slot_info())

        history = json.loads(booker.history_file.read_text())
        assert len(history) == 2
        assert history[0]["club_name"] == "Testclub"
        assert history[1]["club_name"] == "Oud"

    def test_stille_fout_bij_ontbrekende_directory(self, tmp_path):
        booker = _make_booker(tmp_path)
        booker.history_file = tmp_path / "bestaat_niet" / "booking_history.json"
        booker._append_booking_history(datetime(2026, 4, 3), _slot_info())
        assert not booker.history_file.exists()
