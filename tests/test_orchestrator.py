"""
Unit tests voor orchestrator.py — geen browser of credentials nodig.
"""

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

import orchestrator
from orchestrator import (
    _remaining_target,
    build_provider_request,
    enabled_providers,
    load_config,
    main_async,
    open_dates,
    run_all_providers,
    run_provider,
    save_booking_state,
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
# Al geboekte datums / open datums
# ---------------------------------------------------------------------------

class TestOpenDates:
    NOW = datetime(2026, 10, 14, 12, 0)  # woensdag

    def test_een_dag(self):
        cfg = {"day": "thursday", "time_start": "19:30", "weeks_ahead": 3}
        assert open_dates(cfg, set(), self.NOW) == ["2026-10-15", "2026-10-22", "2026-10-29"]

    def test_meerdere_dagen_chronologisch(self):
        cfg = {"days": ["thursday", "tuesday"], "time_start": "19:30", "weeks_ahead": 2}
        assert open_dates(cfg, set(), self.NOW) == ["2026-10-15", "2026-10-20", "2026-10-22", "2026-10-27"]

    def test_geboekte_datums_vallen_af(self):
        cfg = {"days": ["thursday"], "time_start": "19:30", "weeks_ahead": 3}
        assert open_dates(cfg, {"2026-10-22"}, self.NOW) == ["2026-10-15", "2026-10-29"]


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
        assert br["days"] == ["thursday"]
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

class _FakeProc:
    """Subprocess-nabootsing: stdout in één keer, stderr regel voor regel (zoals asyncio.StreamReader)."""

    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0):
        self.returncode = None
        self._final_returncode = returncode
        self.stdin = MagicMock()
        self.stdin.drain = AsyncMock()
        self.stdout = MagicMock()
        self.stdout.read = AsyncMock(return_value=stdout)
        lines = [line + b"\n" for line in stderr.splitlines()] + [b""]
        self.stderr = MagicMock()
        self.stderr.readline = AsyncMock(side_effect=lines)
        self.stderr.read = AsyncMock(return_value=b"")

    async def wait(self):
        self.returncode = self._final_returncode
        return self.returncode

    def kill(self):
        self.returncode = -9


class TestRunProvider:
    def _make_mock_proc(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0):
        return _FakeProc(stdout, stderr, returncode)

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


class TestProviderLogLive:
    """
    Provider-logregels moeten verschijnen terwijl de provider draait, met het juiste level.
    Vroeger werd stderr pas na afloop gelogd (alles met de eindtijd, bijv. 00:11:20 voor een slot
    dat om 00:04 gevonden was) en werd elke WARNING/ERROR een INFO.
    """

    @pytest.mark.asyncio
    async def test_level_van_provider_blijft_behouden(self, caplog):
        import logging
        stderr = (
            b"2026-11-12 00:04:10  INFO      [meetandplay]  'Betaling starten' klikken (poging 1/4)...\n"
            b"2026-11-12 00:04:12  WARNING   [meetandplay]  reCAPTCHA afgewezen bij 'Betaling starten' (poging 1/4)\n"
            b"2026-11-12 00:04:40  ERROR     [meetandplay]  Onverwachte fout\n"
            b"Traceback (most recent call last):\n"
            b"RuntimeError: kapot\n"
        )
        result_json = json.dumps({"success": False, "provider": "meetandplay", "error": "x"})
        with patch("asyncio.create_subprocess_exec", return_value=_FakeProc(result_json.encode(), stderr)):
            with caplog.at_level(logging.DEBUG, logger="orchestrator"):
                await run_provider("meetandplay", {}, debug=False)

        levels = {r.getMessage(): r.levelname for r in caplog.records}
        assert levels["[meetandplay]  'Betaling starten' klikken (poging 1/4)..."] == "INFO"
        assert levels["[meetandplay]  reCAPTCHA afgewezen bij 'Betaling starten' (poging 1/4)"] == "WARNING"
        assert levels["[meetandplay]  Onverwachte fout"] == "ERROR"
        # Traceback-regels (zonder eigen prefix) horen bij de ERROR erboven
        assert levels["RuntimeError: kapot"] == "ERROR"

    @pytest.mark.asyncio
    async def test_regels_worden_gelogd_terwijl_de_provider_draait(self, caplog):
        """Echte subprocess: de eerste regel moet gelogd zijn vóórdat de provider klaar is."""
        import logging
        import sys
        import time

        script = (
            "import json, sys, time\n"
            "print('2026-11-12 00:04:00  INFO      [x]  slot gevonden', file=sys.stderr, flush=True)\n"
            "time.sleep(1.0)\n"
            "print('2026-11-12 00:11:20  INFO      [x]  klaar', file=sys.stderr, flush=True)\n"
            "json.load(sys.stdin)\n"
            "print(json.dumps({'success': False, 'provider': 'x', 'error': 'geen slot'}))\n"
        )
        real_exec = asyncio.create_subprocess_exec

        async def exec_script(*cmd, **kwargs):
            return await real_exec(sys.executable, "-c", script, **kwargs)

        start = time.time()
        with patch("asyncio.create_subprocess_exec", side_effect=exec_script):
            with caplog.at_level(logging.INFO, logger="orchestrator"):
                result = await run_provider("x", {"k": "v"}, debug=False)

        assert result["error"] == "geen slot"
        times = {r.getMessage(): r.created for r in caplog.records}
        found, done = times["[x]  slot gevonden"], times["[x]  klaar"]
        assert found - start < 0.8, "eerste regel pas na afloop gelogd"
        assert done - found >= 0.8

    @pytest.mark.asyncio
    async def test_zeer_lange_regel_breekt_niet(self, caplog):
        long_line = b"2026-11-12 00:04:00  INFO      [x]  " + b"a" * (orchestrator.PROVIDER_STREAM_LIMIT + 10)
        proc = _FakeProc(b'{"success": false, "provider": "x"}')
        proc.stderr.readline = AsyncMock(side_effect=[ValueError("limit"), b""])
        proc.stderr.read = AsyncMock(return_value=long_line)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await run_provider("x", {}, debug=False)
        assert result["success"] is False


class TestGemisteSlots:
    """Slot gevonden maar betaalstap mislukt → ERROR in de log + pushmelding om het zelf te boeken."""

    MISSED = {"date": "2026-11-12", "club_name": "Hal 0172", "club_url": "https://meetandplay.nl/club/88191",
              "court_name": "Padelbaan 4", "time_range": "21:00 - 22:30 90 minuten", "reason": "reCAPTCHA afgewezen"}

    @pytest.mark.asyncio
    async def test_run_all_providers_verzamelt_gemiste_slots(self):
        async def fake_run_provider(name, request, debug):
            return {"success": False, "provider": name, "error": "geen", "missed_slots": [self.MISSED]}

        missed = []
        with patch("orchestrator.run_provider", side_effect=fake_run_provider):
            assert await run_all_providers([("meetandplay", {})], debug=False, missed=missed) is None
        assert missed == [{**self.MISSED, "provider": "meetandplay"}]

    def test_melding_en_error_log(self, monkeypatch, caplog):
        sent = []
        monkeypatch.setattr(orchestrator, "notify_slot_missed", lambda *a: sent.append(a))
        with caplog.at_level("ERROR"):
            orchestrator.report_missed_slots([self.MISSED, dict(self.MISSED)], booked_dates=set())

        assert sent == [("Hal 0172", "2026-11-12", "21:00 - 22:30 90 minuten", "Padelbaan 4",
                         "reCAPTCHA afgewezen", "https://meetandplay.nl/club/88191")]   # één keer, niet dubbel
        assert [r.getMessage() for r in caplog.records] == [
            "Slot gevonden maar niet geboekt: Hal 0172 op 2026-11-12, Padelbaan 4 21:00 - 22:30 90 minuten "
            "(reCAPTCHA afgewezen)"
        ]

    def test_geen_melding_als_de_datum_alsnog_geboekt_is(self, monkeypatch):
        sent = []
        monkeypatch.setattr(orchestrator, "notify_slot_missed", lambda *a: sent.append(a))
        orchestrator.report_missed_slots([self.MISSED], booked_dates={"2026-11-12"})
        assert sent == []

    @pytest.mark.asyncio
    async def test_main_async_meldt_gemist_slot_bij_mislukte_run(self, tmp_path, monkeypatch):
        config = _minimal_config()
        config["state"] = {k: str(tmp_path / f"{k}.json") for k in
                           ("booking_state_file", "history_file", "last_run_file", "pending_payments_file")}
        config["fetch_bookings"] = {"skip_booked_dates": True, "output_file": str(tmp_path / "future.json")}
        monkeypatch.setattr(orchestrator, "load_config", lambda: config)
        monkeypatch.setattr(orchestrator, "load_dotenv", lambda: None)
        monkeypatch.setattr(orchestrator, "open_dates", lambda cfg, skip: ["2026-11-12"])
        sent = []
        monkeypatch.setattr(orchestrator, "notify_slot_missed", lambda *a: sent.append(a))

        async def fake_run_all(requests, debug, missed=None):
            missed.append(dict(self.MISSED, provider="meetandplay"))
            return None

        monkeypatch.setattr(orchestrator, "run_all_providers", fake_run_all)
        assert await main_async(debug=False, dry_run=False) == 1
        assert len(sent) == 1 and sent[0][0] == "Hal 0172"


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


# ---------------------------------------------------------------------------
# enabled_providers
# ---------------------------------------------------------------------------


class TestEnabledProviders:
    def test_standaard_alleen_meetandplay(self, monkeypatch):
        monkeypatch.setenv("KNLTB_EMAIL", "a@b.nl")
        result = enabled_providers({})
        assert [spec.name for spec, _, _ in result] == ["meetandplay"]
        _, credentials, provider_cfg = result[0]
        assert credentials["email"] == "a@b.nl"
        assert provider_cfg == {"cookies_file": ".meetandplay_cookies.json"}

    def test_eigen_sessiebestand_uit_config(self):
        config = {"providers": {"meetandplay": {"cookies_file": "/data/c.json"}}}
        result = enabled_providers(config)
        assert result[0][2] == {"cookies_file": "/data/c.json"}

    def test_playtomic_tijdelijk_uitgeschakeld_ook_als_config_hem_aanzet(self, caplog):
        config = {"providers": {"playtomic": {"enabled": True}}}
        with caplog.at_level("WARNING"):
            result = enabled_providers(config)
        assert [spec.name for spec, _, _ in result] == ["meetandplay"]
        assert "tijdelijk uitgeschakeld" in caplog.text

    def test_playtomic_uit_in_config_geeft_geen_waarschuwing(self, caplog):
        config = {"providers": {"playtomic": {"enabled": False}}}
        with caplog.at_level("INFO"):
            result = enabled_providers(config)
        assert [spec.name for spec, _, _ in result] == ["meetandplay"]
        assert "tijdelijk uitgeschakeld" not in caplog.text
        assert "Provider 'playtomic' uitgeschakeld" in caplog.text


# ---------------------------------------------------------------------------
# Gerichte runs (scheduler)
# ---------------------------------------------------------------------------


class TestTargetedRun:
    CONFIG = {
        "location": {"city": "Boskoop", "radius_km": 20},
        "booking": {"day": "thursday", "time_start": "19:30", "time_end": "21:00"},
    }
    TARGET = {"dates": ["2026-10-22", "2026-10-29"], "clubs": [{"name": "C", "url": "u", "address": "a"}]}

    def test_request_bevat_datums_en_clubs(self):
        req = build_provider_request(self.CONFIG, {}, {}, dry_run=False, target=self.TARGET)
        assert req["booking_request"]["only_dates"] == ["2026-10-22", "2026-10-29"]
        assert req["booking_request"]["clubs"] == self.TARGET["clubs"]

    def test_zonder_target_geen_beperking(self):
        req = build_provider_request(self.CONFIG, {}, {}, dry_run=False)
        assert "only_dates" not in req["booking_request"]
        assert "release_at" not in req["booking_request"]

    def test_vrijgavemoment_gaat_mee_naar_provider(self):
        target = {**self.TARGET, "release_at": "2026-10-15T20:00:00+02:00"}
        req = build_provider_request(self.CONFIG, {}, {}, dry_run=False, target=target)
        assert req["booking_request"]["release_at"] == "2026-10-15T20:00:00+02:00"

    def test_meerdere_dagen_in_request(self):
        config = {**self.CONFIG, "booking": {**self.CONFIG["booking"], "days": ["tuesday", "thursday"]}}
        req = build_provider_request(config, {}, {}, dry_run=False)
        assert req["booking_request"]["days"] == ["tuesday", "thursday"]

    def test_al_geboekte_datums_vallen_af(self):
        assert _remaining_target(self.TARGET, {"2026-10-22", "2026-10-29"}) is None

    def test_overgebleven_datums(self):
        remaining = _remaining_target(self.TARGET, {"2026-10-22"})
        assert remaining["dates"] == ["2026-10-29"]


# ---------------------------------------------------------------------------
# main_async — alle open datums boeken
# ---------------------------------------------------------------------------


class TestMainAsync:
    @pytest.fixture
    def setup(self, tmp_path, monkeypatch):
        config = _minimal_config()
        config["booking"]["weeks_ahead"] = 2
        config["state"] = {
            "booking_state_file": str(tmp_path / "state.json"),
            "history_file": str(tmp_path / "history.json"),
            "last_run_file": str(tmp_path / "last_run.json"),
            "pending_payments_file": str(tmp_path / "pending.json"),
        }
        config["fetch_bookings"] = {"skip_booked_dates": True, "output_file": str(tmp_path / "future.json")}
        monkeypatch.setattr(orchestrator, "load_config", lambda: config)
        monkeypatch.setattr(orchestrator, "load_dotenv", lambda: None)
        monkeypatch.setattr(orchestrator, "notify_booking_available", lambda *a: None)
        monkeypatch.setattr(orchestrator, "open_dates", lambda cfg, skip: [
            d for d in ["2026-10-15", "2026-10-22"] if d not in skip
        ])
        return tmp_path

    @staticmethod
    def _provider_that_books_first_open_date(seen_skips):
        async def fake_run_all(requests, debug, missed=None):
            skip = requests[0][1]["booking_request"]["skip_dates"]
            seen_skips.append(list(skip))
            free = [d for d in ["2026-10-15", "2026-10-22"] if d not in skip]
            if not free:
                return None
            return {"success": True, "provider": "meetandplay", "booked_date": free[0], "slot_info": _slot_info()}
        return fake_run_all

    @pytest.mark.asyncio
    async def test_boekt_alle_open_datums(self, setup, monkeypatch):
        seen = []
        monkeypatch.setattr(orchestrator, "run_all_providers", self._provider_that_books_first_open_date(seen))
        assert await main_async(debug=False, dry_run=False) == 0
        history = json.loads((setup / "history.json").read_text())
        assert sorted(e["booked_date"] for e in history) == ["2026-10-15", "2026-10-22"]
        assert seen == [[], ["2026-10-15"]]

    @pytest.mark.asyncio
    async def test_account_boekingen_worden_overgeslagen(self, setup, monkeypatch):
        (setup / "future.json").write_text(json.dumps([{"booked_date": "2026-10-15"}]))
        seen = []
        monkeypatch.setattr(orchestrator, "run_all_providers", self._provider_that_books_first_open_date(seen))
        assert await main_async(debug=False, dry_run=False) == 0
        assert seen == [["2026-10-15"]]

    @pytest.mark.asyncio
    async def test_alles_geboekt_start_geen_providers(self, setup, monkeypatch):
        (setup / "future.json").write_text(json.dumps([{"booked_date": "2026-10-15"}, {"booked_date": "2026-10-22"}]))
        run_all = AsyncMock()
        monkeypatch.setattr(orchestrator, "run_all_providers", run_all)
        assert await main_async(debug=False, dry_run=False) == 0
        run_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_boekingsgeschiedenis_telt_niet_als_geboekt(self, setup, monkeypatch):
        # Een eerder gevonden (onbetaald) slot staat niet in het account → datum blijft open
        (setup / "history.json").write_text(json.dumps([{"booked_date": "2026-10-15"}]))
        seen = []
        monkeypatch.setattr(orchestrator, "run_all_providers", self._provider_that_books_first_open_date(seen))
        assert await main_async(debug=False, dry_run=False) == 0
        assert seen == [[], ["2026-10-15"]]


# ---------------------------------------------------------------------------
# Betaalherinneringen
# ---------------------------------------------------------------------------

class TestBetaalherinnering:
    SENT = datetime.fromisoformat("2026-10-29T20:00:04+01:00")
    SLOT = {"club_name": "Hal 0172", "club_address": "Weg 1", "court_name": "Baan 1",
            "time_range": "20:00 - 21:30", "payment_url": "https://www.mollie.com/checkout/select-method/x"}

    def _config(self, tmp_path):
        return {
            "state": {"pending_payments_file": str(tmp_path / "pending.json")},
            "fetch_bookings": {"output_file": str(tmp_path / "future.json")},
        }

    def _add(self, tmp_path, booked_date="2026-11-12"):
        orchestrator.add_pending_payment(tmp_path / "pending.json", booked_date, self.SLOT, "meetandplay",
                                         self.SENT, self.SENT + timedelta(minutes=15))

    def _remind(self, tmp_path, monkeypatch, now, future=None):
        sent, refreshed = [], []
        monkeypatch.setattr(orchestrator, "notify_payment_reminder", lambda *a: sent.append(a))

        def refresh():
            refreshed.append(True)
            if future is not None:
                (tmp_path / "future.json").write_text(json.dumps(future))

        orchestrator.remind_unpaid(self._config(tmp_path), refresh, now=now)
        return sent, refreshed

    def test_herinnering_7_minuten_voor_deadline(self, tmp_path):
        self._add(tmp_path)  # deadline = SENT + 15 min
        assert orchestrator.next_reminder_at(tmp_path / "pending.json") == self.SENT + timedelta(minutes=8)

    def test_herinnering_zonder_deadline_8_minuten_na_versturen(self, tmp_path):
        orchestrator.add_pending_payment(tmp_path / "pending.json", "2026-11-12", self.SLOT, "meetandplay",
                                         self.SENT, None)
        assert orchestrator.next_reminder_at(tmp_path / "pending.json") == self.SENT + timedelta(minutes=8)

    def test_korte_vasthoudtijd_herinnering_minstens_1_minuut_later(self, tmp_path):
        orchestrator.add_pending_payment(tmp_path / "pending.json", "2026-11-12", self.SLOT, "meetandplay",
                                         self.SENT, self.SENT + timedelta(minutes=5))
        assert orchestrator.next_reminder_at(tmp_path / "pending.json") == self.SENT + timedelta(minutes=1)

    def test_deadline_uit_orderpagina_gaat_voor(self):
        slot = {**self.SLOT, "hold_until": "20:13"}
        assert orchestrator.payment_deadline_for(slot, self.SENT) == self.SENT.replace(minute=13, second=0)

    def test_deadline_over_middernacht(self):
        sent = datetime.fromisoformat("2026-10-29T23:58:00+01:00")
        deadline = orchestrator.payment_deadline_for({**self.SLOT, "hold_until": "00:13"}, sent)
        assert deadline == datetime.fromisoformat("2026-10-30T00:13:00+01:00")

    def test_zonder_orderpagina_ideal_termijn(self):
        assert orchestrator.payment_deadline_for(self.SLOT, self.SENT) == self.SENT + timedelta(minutes=15)

    def test_nog_niet_aan_de_beurt_haalt_niets_op(self, tmp_path, monkeypatch):
        self._add(tmp_path)
        sent, refreshed = self._remind(tmp_path, monkeypatch, self.SENT + timedelta(minutes=5))
        assert not sent and not refreshed
        assert orchestrator.next_reminder_at(tmp_path / "pending.json") is not None

    def test_onbetaald_krijgt_een_herinnering(self, tmp_path, monkeypatch):
        self._add(tmp_path)
        sent, refreshed = self._remind(tmp_path, monkeypatch, self.SENT + timedelta(minutes=8), future=[])
        assert refreshed and len(sent) == 1
        club, booked_date, _, url, deadline, _order = sent[0]
        assert (club, booked_date, url) == ("Hal 0172", "2026-11-12", self.SLOT["payment_url"])
        assert deadline == self.SENT + timedelta(minutes=15)
        assert orchestrator.next_reminder_at(tmp_path / "pending.json") is None  # maar één keer

    def test_betaald_geen_herinnering(self, tmp_path, monkeypatch):
        self._add(tmp_path)
        future = [{"booked_date": "2026-11-12", "status": "betaald"}]
        sent, _ = self._remind(tmp_path, monkeypatch, self.SENT + timedelta(minutes=8), future=future)
        assert not sent

    def test_andere_status_telt_niet_als_betaald(self, tmp_path, monkeypatch):
        self._add(tmp_path)
        future = [{"booked_date": "2026-11-12", "status": "openstaand"}]
        sent, _ = self._remind(tmp_path, monkeypatch, self.SENT + timedelta(minutes=8), future=future)
        assert len(sent) == 1

    def test_verlopen_termijn_geen_herinnering(self, tmp_path, monkeypatch):
        self._add(tmp_path)
        sent, _ = self._remind(tmp_path, monkeypatch, self.SENT + timedelta(minutes=20), future=[])
        assert not sent
        assert orchestrator.next_reminder_at(tmp_path / "pending.json") is None

    def test_alleen_verlopen_herinneringen_worden_verwerkt(self, tmp_path, monkeypatch):
        self._add(tmp_path, "2026-11-12")
        later = dict(self.SLOT)
        orchestrator.add_pending_payment(tmp_path / "pending.json", "2026-11-19", later, "meetandplay",
                                         self.SENT + timedelta(minutes=30), None)
        sent, _ = self._remind(tmp_path, monkeypatch, self.SENT + timedelta(minutes=8), future=[])
        assert [a[1] for a in sent] == ["2026-11-12"]
        assert orchestrator.next_reminder_at(tmp_path / "pending.json") == self.SENT + timedelta(minutes=38)

    def test_handle_success_plant_herinnering_en_stuurt_deadline(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(orchestrator, "notify_booking_available", lambda *a: calls.append(a))
        result = {"provider": "meetandplay", "booked_date": "2026-11-12", "slot_info": dict(self.SLOT)}
        orchestrator._handle_success(result, False, tmp_path / "s.json", tmp_path / "h.json", tmp_path / "pending.json")
        pending = json.loads((tmp_path / "pending.json").read_text())
        assert len(pending) == 1 and pending[0]["deadline"]
        assert calls[0][-1] is not None  # deadline voor een Mollie-link

    def test_dry_run_plant_geen_herinnering(self, tmp_path, monkeypatch):
        monkeypatch.setattr(orchestrator, "notify_booking_available", lambda *a: None)
        result = {"provider": "meetandplay", "booked_date": "2026-11-12", "slot_info": dict(self.SLOT)}
        orchestrator._handle_success(result, True, tmp_path / "s.json", tmp_path / "h.json", tmp_path / "pending.json")
        assert not (tmp_path / "pending.json").exists()
