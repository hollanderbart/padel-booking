"""
Unit tests voor scheduler.py — planning van gerichte runs op vrijgavemomenten.
"""

import json
from datetime import date, datetime, timedelta

import pytest

import scheduler
from scheduler import (
    DAILY,
    RELEASE_DELAY,
    ROLLING,
    TZ,
    PlannedRun,
    load_nearby_schedule,
    load_schedule,
    next_due,
    next_nightly,
    plan_runs,
    release_moments,
    within_radius,
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

    def test_doeldag_die_later_binnen_bereik_komt_wordt_gepland(self):
        # weeks_ahead=1: nu valt alleen 15-10 binnen bereik. Vanaf do 15-10 19:30 is dat 22-10,
        # precies wanneer de rollende club (7 d) 22-10 19:30 vrijgeeft.
        booking = {**BOOKING, "weeks_ahead": 1}
        runs = [r for r in plan_runs({"clubs": [ROLLING_CLUB]}, booking, NOW, NOW + timedelta(days=2)) if r.dates]
        assert runs and all(r.dates == ("2026-10-22",) for r in runs)

    def test_vrijgave_voordat_doeldag_binnen_bereik_valt_wordt_niet_gepland(self):
        club = {**ROLLING_CLUB, "window_days": 30}
        booking = {**BOOKING, "weeks_ahead": 1}
        assert not [r for r in plan_runs({"clubs": [club]}, booking, NOW, NOW + timedelta(days=7)) if r.dates]

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


class TestNextDue:
    TARGETED = PlannedRun(NOW, ("2026-10-22",), (("Rol", "https://r", "A"),), "vrijgave Rol")
    OTHER = PlannedRun(NOW + timedelta(minutes=1), ("2026-10-29",), (("Dag", "https://d", "B"),), "vrijgave Dag")
    NIGHTLY = PlannedRun(NOW + timedelta(minutes=2), reason="nachtelijke volledige run")

    def test_niets_aan_de_beurt(self):
        assert next_due([self.TARGETED], NOW - timedelta(seconds=1)) == (None, [])

    def test_een_run_aan_de_beurt_blijft_ongewijzigd(self):
        assert next_due([self.TARGETED, self.OTHER], NOW) == (self.TARGETED, [self.TARGETED])

    def test_gemiste_runs_met_volledige_run_worden_een_volledige_run(self):
        later = NOW + timedelta(minutes=6)
        run, covered = next_due([self.TARGETED, self.NIGHTLY], later)
        assert covered == [self.TARGETED, self.NIGHTLY]
        assert run.at == later and not run.dates
        assert "ingehaalde volledige run" in run.reason

    def test_gemiste_gerichte_runs_worden_samengevoegd(self):
        later = NOW + timedelta(minutes=6)
        run, covered = next_due([self.TARGETED, self.OTHER], later)
        assert covered == [self.TARGETED, self.OTHER]
        assert run.dates == ("2026-10-22", "2026-10-29")
        assert {name for name, _, _ in run.clubs} == {"Rol", "Dag"}


class _StopLoop(Exception):
    pass


class TestRunForever:
    def _simulate(self, monkeypatch, start: datetime, until: datetime, durations: dict[str, timedelta]):
        """Draai run_forever met een nepklok; execute duurt per soort run `durations` lang."""
        clock = [start]
        executed: list[tuple[datetime, PlannedRun]] = []

        class FakeDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock[0]

        def fake_execute(run, config, debug):
            executed.append((clock[0], run))
            clock[0] += durations["gericht" if run.dates else "volledig"]

        def fake_sleep(seconds):
            clock[0] += timedelta(seconds=max(seconds, 1))
            if clock[0] > until:
                raise _StopLoop

        monkeypatch.setattr(scheduler, "datetime", FakeDatetime)
        monkeypatch.setattr(scheduler, "execute", fake_execute)
        monkeypatch.setattr(scheduler.time_mod, "sleep", fake_sleep)
        monkeypatch.setattr(scheduler, "load_schedule", lambda *a, **kw: {"clubs": [DAILY_CLUB]})
        monkeypatch.setattr(scheduler, "describe_schedule", lambda schedule: None)
        with pytest.raises(_StopLoop):
            scheduler.run_forever({"booking": BOOKING}, {}, None, debug=False)
        return executed

    def test_nachtrun_wordt_ingehaald_na_gerichte_run_met_retries(self, monkeypatch):
        # Dag-club geeft donderdag 22-10 vrij op 14-10 00:00. De gerichte run van 00:00:05 duurt
        # met retries ~6 min; vroeger viel de nachtrun van 00:01 daardoor stil weg.
        executed = self._simulate(
            monkeypatch,
            start=datetime(2026, 10, 13, 23, 50, tzinfo=TZ),
            until=datetime(2026, 10, 14, 0, 30, tzinfo=TZ),
            durations={"gericht": timedelta(minutes=6), "volledig": timedelta(minutes=2)},
        )
        reasons = [run.reason for _, run in executed]
        assert reasons[0] == "startrun"
        assert reasons[1].startswith("vrijgave Dag")
        assert reasons[2] == "nachtelijke volledige run"
        started, nightly = executed[2]
        assert started == datetime(2026, 10, 14, 0, 6, 5, tzinfo=TZ)
        assert nightly.at == started  # retries/logging tellen vanaf het inhaalmoment
        assert len(executed) == 3

    def test_runs_tijdens_lange_startrun_worden_samengevoegd_ingehaald(self, monkeypatch):
        # Startrun om 23:55 duurt 10 min: de gerichte run (00:00:05) én de nachtrun (00:01) vallen
        # erin en worden samen één ingehaalde volledige run.
        executed = self._simulate(
            monkeypatch,
            start=datetime(2026, 10, 13, 23, 55, tzinfo=TZ),
            until=datetime(2026, 10, 14, 0, 30, tzinfo=TZ),
            durations={"gericht": timedelta(minutes=1), "volledig": timedelta(minutes=10)},
        )
        assert [run.reason for _, run in executed] == [
            "startrun", "ingehaalde volledige run (gemist: 00:00, 00:01)",
        ]


class TestWithinRadius:
    # Clubcoördinaten (openbare clublocaties) zoals de provider ze cachet: {slug: [lat, lon]}
    COORDS = {"88191": [52.16026, 4.777723], "88018": [52.2166339, 4.5069429]}
    HAL = {**ROLLING_CLUB, "club_name": "Hal 0172", "club_url": "https://meetandplay.nl/club/88191"}
    TERWEGEN = {**DAILY_CLUB, "club_name": "TERWEGEN INDOOR", "club_url": "https://meetandplay.nl/club/88018"}
    ONBEKEND = {**DAILY_CLUB, "club_name": "Onbekend", "club_url": "https://meetandplay.nl/club/123"}
    LOCATION = {"city": "Boskoop", "radius_km": 14, "latitude": 52.07, "longitude": 4.66}

    def test_club_buiten_straal_valt_weg_onbekende_blijft(self):
        schedule = {"generated_at": "x", "clubs": [self.HAL, self.TERWEGEN, self.ONBEKEND]}
        result = within_radius(schedule, self.LOCATION, self.COORDS)
        assert [c["club_name"] for c in result["clubs"]] == ["Hal 0172", "Onbekend"]
        assert result["generated_at"] == "x"

    def test_zonder_coordinaten_of_straal_geen_filter(self):
        schedule = {"clubs": [self.HAL, self.TERWEGEN]}
        assert within_radius(schedule, {"radius_km": 14}, self.COORDS) == schedule
        assert within_radius(schedule, {"latitude": 52.07, "longitude": 4.66}, self.COORDS) == schedule

    def test_gefilterde_club_krijgt_geen_gerichte_runs(self):
        schedule = within_radius({"clubs": [self.TERWEGEN]}, self.LOCATION, self.COORDS)
        assert [r for r in plan_runs(schedule, BOOKING, NOW, NOW + timedelta(days=14)) if r.dates] == []

    def test_load_nearby_schedule_leest_coordinaten_naast_cookiebestand(self, tmp_path):
        (tmp_path / ".meetandplay_club_coords.json").write_text(json.dumps(self.COORDS))
        local = tmp_path / "schedule.json"
        local.write_text(json.dumps({"clubs": [self.HAL, self.TERWEGEN]}))
        config = {
            "location": self.LOCATION,
            "providers": {"meetandplay": {"cookies_file": str(tmp_path / ".meetandplay_cookies.json")}},
        }
        schedule = load_nearby_schedule(config, "http://127.0.0.1:9/ongebruikt", tmp_path / "cache.json", local)
        assert [c["club_name"] for c in schedule["clubs"]] == ["Hal 0172"]


class TestLoadSchedule:
    def test_valt_terug_op_cache_als_url_faalt(self, tmp_path):
        cache = tmp_path / "cache.json"
        cache.write_text(json.dumps({"clubs": [ROLLING_CLUB]}))
        schedule = load_schedule("http://127.0.0.1:9/geen-server", cache)
        assert schedule["clubs"][0]["club_name"] == "Rol"

    def test_leeg_schema_zonder_bronnen(self, tmp_path):
        assert load_schedule("http://127.0.0.1:9/geen-server", tmp_path / "geen.json") == {"clubs": []}
