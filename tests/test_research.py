"""
Unit tests voor research/booking_windows.py — gesimuleerde clubs, geen browser of netwerk.
"""

from datetime import date, datetime, time, timedelta

import pytest

from research.booking_windows import (
    BLOCK,
    DAILY,
    INSUFFICIENT,
    NONE_SEEN,
    ROLLING,
    TZ,
    classify_observation,
    infer_all,
    infer_strategy,
    load_observations,
    render_report,
    save_observations,
    scan_days,
    summarize_scan,
)

SLOT_HOURS = range(9, 23)  # club heeft elk uur een slot van 09:00 t/m 22:00
# Zelfde ritme als .github/workflows/booking-research.yml (zomertijd)
RUN_TIMES = [time(0, 15), time(7, 15), time(11, 15), time(15, 15), time(19, 15), time(22, 15)]
START = datetime(2026, 4, 6, tzinfo=TZ)  # maandag


def _is_open(strategy: str, slot: datetime, now: datetime, window: int, hour: int = 0) -> bool:
    """Is `slot` op moment `now` boekbaar volgens de gesimuleerde release-strategie?"""
    if slot <= now:
        return False
    if strategy == ROLLING:
        return slot - timedelta(days=window) <= now
    if strategy == DAILY:
        released_until = now.date() + timedelta(days=window if now.hour >= hour else window - 1)
        return slot.date() <= released_until
    if strategy == BLOCK:
        # Elke maandag om 09:00 komt de volgende week (t/m zondag) vrij
        monday = now.date() - timedelta(days=now.weekday())
        released_monday = monday if (now.weekday(), now.hour) >= (0, 9) else monday - timedelta(days=7)
        return slot.date() <= released_monday + timedelta(days=13)
    return False


def _observe(strategy: str, now: datetime, window: int = 7, hour: int = 0) -> dict:
    def probe_day(offset: int) -> list[str]:
        day = now.date() + timedelta(days=offset)
        return [
            f"{h:02d}:00" for h in SLOT_HOURS
            if _is_open(strategy, datetime.combine(day, time(h), tzinfo=TZ), now, window, hour)
        ]

    return {"club_name": "C", "club_address": "A", "club_url": "u", **summarize_scan(now, scan_days(probe_day))}


def _observations(strategy: str, days: int = 7, **kwargs) -> list[dict]:
    return [
        _observe(strategy, datetime.combine(START.date() + timedelta(days=d), t, tzinfo=TZ), **kwargs)
        for d in range(days) for t in RUN_TIMES
    ]


# ---------------------------------------------------------------------------
# Momentopname
# ---------------------------------------------------------------------------

class TestScanDays:
    def test_stopt_na_lege_dagen_voorbij_horizon(self):
        calls = []

        def probe(offset):
            calls.append(offset)
            return ["10:00"] if offset <= 5 else []

        scan_days(probe)
        assert max(calls) == 5 + 4

    def test_geeft_op_als_eerste_dagen_leeg_zijn(self):
        calls = []
        scan_days(lambda offset: calls.append(offset) or [])
        assert len(calls) == 8


class TestSummarizeScan:
    def test_horizon_en_tijden(self):
        now = datetime(2026, 4, 9, 13, 15, tzinfo=TZ)
        summary = summarize_scan(now, {0: ["20:00"], 1: [], 2: ["09:00", "12:30"]})
        assert summary["horizon_days"] == 2
        assert summary["horizon_date"] == "2026-04-11"
        assert (summary["first_slot"], summary["last_slot"]) == ("09:00", "12:30")

    def test_geen_slots(self):
        summary = summarize_scan(datetime(2026, 4, 9, 13, 15, tzinfo=TZ), {0: [], 1: []})
        assert summary["horizon_days"] is None


class TestClassifyObservation:
    def _obs(self, observed, last_slot, distinct=5):
        return {"observed_at": observed, "last_slot": last_slot, "distinct_start_times": distinct}

    def test_laatste_slot_vlak_voor_nu_is_cutoff(self):
        assert classify_observation(self._obs("2026-04-09T15:15+02:00", "15:00")) == "cutoff"

    def test_slots_tot_de_avond_is_full_day(self):
        assert classify_observation(self._obs("2026-04-09T15:15+02:00", "22:00")) == "full_day"

    def test_een_enkele_starttijd_is_niet_informatief(self):
        """De pagina toonde alleen slots van één starttijd — zegt niets over het verloop over de dag."""
        assert classify_observation(self._obs("2026-04-09T10:55+02:00", "09:00", distinct=1)) is None

    def test_oude_observatie_zonder_veld_is_niet_informatief(self):
        assert classify_observation({"observed_at": "2026-04-09T15:15+02:00", "last_slot": "15:00"}) is None

    def test_nacht_observatie_niet_informatief(self):
        assert classify_observation(self._obs("2026-04-09T00:15+02:00", "22:00")) is None


# ---------------------------------------------------------------------------
# Strategie herkennen in gesimuleerde clubs
# ---------------------------------------------------------------------------

class TestInferStrategy:
    def test_rollend_venster(self):
        result = infer_strategy(_observations(ROLLING, window=7))
        assert result["strategy"] == ROLLING
        assert result["window_days"] == 7

    @pytest.mark.parametrize("hour", [0, 7, 20])
    def test_dagelijks_vast_uur(self, hour):
        result = infer_strategy(_observations(DAILY, window=14, hour=hour))
        assert result["strategy"] == DAILY
        assert result["window_days"] == 14
        assert f"{hour:02d}:00" in result["release"]

    def test_dagelijks_om_middernacht_exact(self):
        assert "om 00:00" in infer_strategy(_observations(DAILY, window=14, hour=0))["release"]

    def test_wekelijks_blok(self):
        result = infer_strategy(_observations(BLOCK, days=14))
        assert result["strategy"] == BLOCK

    def test_rollend_ondanks_ruis(self):
        """Een deel van de observaties is 'vervuild' (bijv. door annuleringen laat op de avond)."""
        obs = _observations(ROLLING, window=7)
        for o in obs[::6]:
            o["last_slot"] = "22:00"
        assert infer_strategy(obs)["strategy"] == ROLLING

    def test_geen_beschikbaarheid(self):
        obs = [{"observed_at": "2026-04-09T15:15+02:00", "horizon_days": None, "last_slot": None}]
        assert infer_strategy(obs)["strategy"] == NONE_SEEN

    def test_alleen_nachtobservaties_is_onvoldoende(self):
        obs = [_observe(ROLLING, datetime(2026, 4, 9, 0, 15, tzinfo=TZ))] * 3
        assert infer_strategy(obs)["strategy"] == INSUFFICIENT


# ---------------------------------------------------------------------------
# Opslag en rapport
# ---------------------------------------------------------------------------

class TestStorageAndReport:
    def test_oude_observaties_worden_opgeruimd(self, tmp_path):
        now = datetime(2026, 7, 1, tzinfo=TZ)
        old = {"observed_at": "2026-01-01T12:00+01:00"}
        new = {"observed_at": "2026-06-30T12:00+02:00"}
        path = tmp_path / "obs.jsonl"
        save_observations(path, [old, new], now)
        assert load_observations(path) == [new]

    def test_rapport_bevat_club_en_strategie(self):
        obs = _observations(ROLLING, window=7)
        rows = infer_all(obs, now=datetime.combine(date(2026, 4, 13), time(12), tzinfo=TZ))
        report = render_report(rows, datetime(2026, 4, 13, 12, tzinfo=TZ))
        assert "| [C](u) | A | 7 d | rollend |" in report

    def test_mislukte_scans_tellen_niet_mee(self):
        now = datetime(2026, 4, 9, 16, tzinfo=TZ)
        failed = {"club_name": "X", "club_address": "", "club_url": "x", "observed_at": now.isoformat(), "error": "boom"}
        assert infer_all([failed], now) == []
