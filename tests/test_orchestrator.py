"""
Unit tests voor orchestrator.py — geen browser of credentials nodig.
"""

import asyncio
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from orchestrator import (
    _get_furthest_target_date,
    append_booking_history,
    build_provider_request,
    is_already_booked,
    load_config,
    run_all_providers,
    run_provider,
    save_booking_state,
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


def _minimal_config() -> dict:
    return {
        "location": {"city": "Boskoop", "radius_km": 20, "latitude": 52.07, "longitude": 4.65},
        "booking": {
            "day": "thursday",
            "time_start": "19:30",
            "time_end": "21:00",
            "duration_minutes": 90,
            "court_type": "indoor",
            "game_type": "double",
            "weeks_ahead": 4,
        },
        "providers": {
            "meetandplay": {"enabled": True},
            "playtomic": {"enabled": False},
        },
        "state": {
            "booking_state_file": ".booking_state.json",
            "history_file": "booking_history.json",
            "last_run_file": "last_run.json",
        },
    }


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_laadt_geldig_yaml_bestand(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("booking:\n  day: thursday\n")
        result = load_config(str(cfg))
        assert result["booking"]["day"] == "thursday"

    def test_ontbrekend_bestand_geeft_filenotfounderror(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(str(tmp_path / "bestaat_niet.yaml"))

    def test_leest_alle_secties(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump(_minimal_config()))
        result = load_config(str(cfg))
        assert "location" in result
        assert "booking" in result
        assert "providers" in result


# ---------------------------------------------------------------------------
# is_already_booked
# ---------------------------------------------------------------------------

class TestIsAlreadyBooked:
    # booking_cfg met een dag ver in de toekomst zodat furthest_target_date altijd
    # minstens weeks_ahead weken vooruit ligt — onafhankelijk van de dag waarop de test draait.
    BOOKING_CFG = {"day": "thursday", "weeks_ahead": 4}

    def test_geen_bestand_retourneert_false(self, tmp_path):
        assert is_already_booked(tmp_path / "geen.json", self.BOOKING_CFG) is False

    def test_verste_doeldatum_geboekt_retourneert_true(self, tmp_path):
        """Als de boekingsdatum >= de verste doeldatum is, overslaan."""
        from orchestrator import _get_furthest_target_date
        furthest = _get_furthest_target_date(self.BOOKING_CFG)
        f = tmp_path / "state.json"
        f.write_text(json.dumps({"booked_date": furthest.isoformat()}))
        assert is_already_booked(f, self.BOOKING_CFG) is True

    def test_datum_voorbij_verste_doeldatum_retourneert_true(self, tmp_path):
        from orchestrator import _get_furthest_target_date
        furthest = _get_furthest_target_date(self.BOOKING_CFG)
        ver_weg = (furthest + timedelta(weeks=2)).isoformat()
        f = tmp_path / "state.json"
        f.write_text(json.dumps({"booked_date": ver_weg}))
        assert is_already_booked(f, self.BOOKING_CFG) is True

    def test_datum_voor_verste_doeldatum_retourneert_false(self, tmp_path):
        """Als er nog latere data in het zoekvenster zitten, doorgaan met zoeken."""
        from orchestrator import _get_furthest_target_date
        furthest = _get_furthest_target_date(self.BOOKING_CFG)
        eerder = (furthest - timedelta(weeks=1)).isoformat()
        f = tmp_path / "state.json"
        f.write_text(json.dumps({"booked_date": eerder}))
        assert is_already_booked(f, self.BOOKING_CFG) is False

    def test_verleden_datum_retourneert_false(self, tmp_path):
        f = tmp_path / "state.json"
        past = (date.today() - timedelta(days=1)).isoformat()
        f.write_text(json.dumps({"booked_date": past}))
        assert is_already_booked(f, self.BOOKING_CFG) is False

    def test_corrupt_json_retourneert_false(self, tmp_path):
        f = tmp_path / "state.json"
        f.write_text("GEEN_JSON{{{")
        assert is_already_booked(f, self.BOOKING_CFG) is False

    def test_ontbrekend_booked_date_veld_retourneert_false(self, tmp_path):
        f = tmp_path / "state.json"
        f.write_text(json.dumps({"provider": "meetandplay"}))
        assert is_already_booked(f, self.BOOKING_CFG) is False


# ---------------------------------------------------------------------------
# save_booking_state
# ---------------------------------------------------------------------------

class TestSaveBookingState:
    def test_schrijft_alle_velden(self, tmp_path):
        f = tmp_path / "state.json"
        save_booking_state(f, "2026-04-10", _slot_info(), "meetandplay")
        data = json.loads(f.read_text())
        assert data["booked_date"] == "2026-04-10"
        assert data["provider"] == "meetandplay"
        assert data["slot_info"]["club_name"] == "Testclub"
        assert data["slot_info"]["court_name"] == "Baan 1"
        assert data["slot_info"]["time_range"] == "19:30 - 21:00 90 minuten"
        assert "booked_at" in data

    def test_overschrijft_bestaand_bestand(self, tmp_path):
        f = tmp_path / "state.json"
        save_booking_state(f, "2026-04-03", _slot_info(club_name="Oud"), "meetandplay")
        save_booking_state(f, "2026-04-10", _slot_info(club_name="Nieuw"), "playtomic")
        data = json.loads(f.read_text())
        assert data["booked_date"] == "2026-04-10"
        assert data["slot_info"]["club_name"] == "Nieuw"

    def test_stille_fout_bij_ontbrekende_directory(self, tmp_path):
        f = tmp_path / "bestaat_niet" / "state.json"
        save_booking_state(f, "2026-04-10", _slot_info(), "meetandplay")
        assert not f.exists()


# ---------------------------------------------------------------------------
# build_provider_request
# ---------------------------------------------------------------------------

class TestBuildProviderRequest:
    def test_bevat_alle_verplichte_velden(self):
        config = _minimal_config()
        req = build_provider_request(
            config,
            credentials={"email": "test@test.nl", "password": "pw"},
            provider_config={"cookies_file": ".cookies.json"},
            dry_run=False,
        )
        br = req["booking_request"]
        assert br["day"] == "thursday"
        assert br["time_start"] == "19:30"
        assert br["time_end"] == "21:00"
        assert br["duration_minutes"] == 90
        assert br["court_type"] == "indoor"
        assert br["game_type"] == "double"
        assert br["weeks_ahead"] == 4
        assert req["credentials"]["email"] == "test@test.nl"
        assert req["dry_run"] is False

    def test_standaard_waarden_bij_ontbrekende_opties(self):
        config = _minimal_config()
        del config["booking"]["duration_minutes"]
        del config["booking"]["court_type"]
        del config["booking"]["game_type"]
        del config["booking"]["weeks_ahead"]
        req = build_provider_request(config, {}, {}, False)
        br = req["booking_request"]
        assert br["duration_minutes"] == 90
        assert br["court_type"] == "indoor"
        assert br["game_type"] == "double"
        assert br["weeks_ahead"] == 4

    def test_dry_run_true_doorgegeven(self):
        req = build_provider_request(_minimal_config(), {}, {}, dry_run=True)
        assert req["dry_run"] is True

    def test_locatie_doorgegeven(self):
        req = build_provider_request(_minimal_config(), {}, {}, False)
        loc = req["booking_request"]["location"]
        assert loc["city"] == "Boskoop"
        assert loc["latitude"] == 52.07
        assert loc["longitude"] == 4.65


# ---------------------------------------------------------------------------
# run_provider (async)
# ---------------------------------------------------------------------------

class TestRunProvider:
    def _make_mock_proc(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0):
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(stdout, stderr))
        proc.returncode = returncode
        return proc

    @pytest.mark.asyncio
    async def test_succesvolle_provider_retourneert_resultaat(self):
        result_json = json.dumps({"success": True, "provider": "playtomic", "booked_date": "2026-04-10", "slot_info": {}, "error": None})
        proc = self._make_mock_proc(result_json.encode())

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await run_provider("playtomic", {"key": "val"}, debug=False)

        assert result["success"] is True
        assert result["provider"] == "playtomic"

    @pytest.mark.asyncio
    async def test_lege_stdout_retourneert_fout_dict(self):
        proc = self._make_mock_proc(b"", returncode=1)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await run_provider("meetandplay", {}, debug=False)

        assert result["success"] is False
        assert "geen output" in result["error"]

    @pytest.mark.asyncio
    async def test_ongeldige_json_retourneert_fout_dict(self):
        proc = self._make_mock_proc(b"GEEN_JSON")

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await run_provider("meetandplay", {}, debug=False)

        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_subprocess_exception_retourneert_fout_dict(self):
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("not found")):
            result = await run_provider("meetandplay", {}, debug=False)

        assert result["success"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_stderr_wordt_gelogd(self, caplog):
        import logging
        result_json = json.dumps({"success": False, "provider": "test", "error": "nee", "booked_date": None, "slot_info": None})
        proc = self._make_mock_proc(result_json.encode(), stderr=b"2026-04-01 10:00:00  INFO  Provider log regel")

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with caplog.at_level(logging.INFO):
                await run_provider("test", {}, debug=False)

        # De timestamp+level prefix moet worden gestript
        assert any("Provider log regel" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# run_all_providers (async)
# ---------------------------------------------------------------------------

class TestRunAllProviders:
    @pytest.mark.asyncio
    async def test_lege_lijst_retourneert_none(self):
        result = await run_all_providers([], debug=False)
        assert result is None

    @pytest.mark.asyncio
    async def test_eerste_succes_wint(self):
        success = {"success": True, "provider": "playtomic", "booked_date": "2026-04-10", "slot_info": {}, "error": None}
        failure = {"success": False, "provider": "meetandplay", "error": "nee", "booked_date": None, "slot_info": None}

        call_count = {"n": 0}

        async def fake_run(name, req, debug):
            call_count["n"] += 1
            if name == "playtomic":
                return success
            await asyncio.sleep(10)  # wordt geannuleerd
            return failure

        with patch("orchestrator.run_provider", side_effect=fake_run):
            result = await run_all_providers(
                [("playtomic", {}), ("meetandplay", {})], debug=False
            )

        assert result is not None
        assert result["success"] is True
        assert result["provider"] == "playtomic"

    @pytest.mark.asyncio
    async def test_alle_falen_retourneert_none(self):
        failure = {"success": False, "provider": "x", "error": "nee", "booked_date": None, "slot_info": None}

        async def fake_run(name, req, debug):
            return failure

        with patch("orchestrator.run_provider", side_effect=fake_run):
            result = await run_all_providers([("meetandplay", {}), ("playtomic", {})], debug=False)

        assert result is None
