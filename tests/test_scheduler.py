"""
Unit tests voor scheduler.py — planning van gerichte runs op vrijgavemomenten.
"""

import json
from datetime import date, datetime, timedelta

from scheduler import (
    DAILY,
    RELEASE_DELAY,
    ROLLING,
    TZ,
    PlannedRun,
    load_schedule,
    next_nightly,
    plan_runs,
    release_moments,
)

BOOKING = {"day": "thursday", "time_start": "19:30", "time_end": "21:00", "weeks_ahead": 4}
ROLLING_CLUB = {"club_name": "Rol", "club_url": "https://r", "club_address": "A", "strategy": ROLLING, "window_days": 7, "release_hours": []}
DAILY_CLUB = {"club_name": "Dag", "club_url": "https://d", "club_address": "B", "strategy": DAILY, "window_days": 8, "release_hours": [0]}
UNKNOWN_CLUB = {"club_name": "?", "club_url": "https://x", "club_address": "", "strategy": "onvoldoende data", "window_days": 13, "release_hours": []}
THURSDAY = date(2026, 10, 22)
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=TZ)  # woensdag


class TestReleaseMoments:
    def test_rollend_elke_starttijd_in_venster_n_dagen_vooraf(self):
        moments = release_moments(ROLLING_CLUB, THURSDAY, BOOKING)
        assert [m.strftime("%Y-%m-%d %H:%M") for m in moments] == [
            "2026-10-15 19:30", "2026-10-15 20:00", "2026-10-15 20:30",
        ]

    def test_dagelijks_op_vrijgave_uur(self):
        assert release_moments(DAILY_CLUB, THURSDAY, BOOKING) == [datetime(2026, 10, 14, 0, 0, tzinfo=TZ)]

    def test_onbekende_strategie_geen_momenten(self):
        assert release_moments(UNKNOWN_CLUB, THURSDAY, BOOKING) == []


class TestPlanRuns:
    def test_plant_rollende_vrijgave_en_nachtelijke_run(self):
        runs = plan_runs({"clubs": [ROLLING_CLUB, UNKNOWN_CLUB]}, BOOKING, NOW, NOW + timedelta(days=2))
        targeted = [r for r in runs if r.dates]
        assert [r.at for r in targeted] == [
            datetime(2026, 10, 15, h, m, tzinfo=TZ) + RELEASE_DELAY for h, m in [(19, 30), (20, 0), (20, 30)]
        ]
        assert all(r.dates == ("2026-10-22",) and r.clubs == (("Rol", "https://r", "A"),) for r in targeted)
        assert any(not r.dates and r.at == datetime(2026, 10, 15, 0, 1, tzinfo=TZ) for r in runs)

    def test_runs_op_zelfde_moment_worden_samengevoegd(self):
        other = {**ROLLING_CLUB, "club_name": "Rol2", "club_url": "https://r2"}
        runs = [r for r in plan_runs({"clubs": [ROLLING_CLUB, other]}, BOOKING, NOW, NOW + timedelta(days=2)) if r.dates]
        assert len(runs) == 3
        assert {name for name, _, _ in runs[0].clubs} == {"Rol", "Rol2"}

    def test_vrijgave_in_het_verleden_wordt_niet_gepland(self):
        # Dag-club: donderdag 22-10 kwam al om 00:00 vandaag vrij; volgende is 29-10 op 21-10 00:00
        runs = [r for r in plan_runs({"clubs": [DAILY_CLUB]}, BOOKING, NOW, NOW + timedelta(days=8)) if r.dates]
        assert [(r.at.date(), r.dates) for r in runs] == [(date(2026, 10, 21), ("2026-10-29",))]

    def test_leeg_schema_alleen_nachtelijke_run(self):
        runs = plan_runs({"clubs": []}, BOOKING, NOW, NOW + timedelta(days=1))
        assert len(runs) == 1 and not runs[0].dates


class TestPlannedRun:
    def test_target_bevat_datums_en_clubs(self):
        run = PlannedRun(NOW, ("2026-10-22",), (("Rol", "https://r", "A"),))
        assert run.target() == {"dates": ["2026-10-22"], "clubs": [{"name": "Rol", "url": "https://r", "address": "A"}]}

    def test_volledige_run_heeft_geen_target(self):
        assert PlannedRun(NOW).target() is None


class TestNextNightly:
    def test_vandaag_als_nog_niet_geweest(self):
        assert next_nightly(datetime(2026, 10, 14, 0, 0, tzinfo=TZ)) == datetime(2026, 10, 14, 0, 1, tzinfo=TZ)

    def test_morgen_als_al_geweest(self):
        assert next_nightly(NOW) == datetime(2026, 10, 15, 0, 1, tzinfo=TZ)


class TestLoadSchedule:
    def test_valt_terug_op_cache_als_url_faalt(self, tmp_path):
        cache = tmp_path / "cache.json"
        cache.write_text(json.dumps({"clubs": [ROLLING_CLUB]}))
        schedule = load_schedule("http://127.0.0.1:9/geen-server", cache)
        assert schedule["clubs"][0]["club_name"] == "Rol"

    def test_leeg_schema_zonder_bronnen(self, tmp_path):
        assert load_schedule("http://127.0.0.1:9/geen-server", tmp_path / "geen.json") == {"clubs": []}
