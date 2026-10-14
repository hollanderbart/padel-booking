"""
Slimme planning: start de booker op het moment dat een club nieuwe banen vrijgeeft.

Leest per club de release-strategie uit club_schedule.json (gegenereerd door de
booking-research workflow, zie research/booking_windows.py) en plant gerichte runs:

  rollend   — slot T komt vrij op T − N dagen → run op elke starttijd in het
              tijdvenster, N dagen vóór de doeldag
  dagelijks — doeldag D komt vrij op D − N dagen om uur H → run om H:00
  overig    — geen gerichte run; de nachtelijke volledige run (00:01) dekt deze clubs

Gebruik:
  python scheduler.py                              # draait continu (schedule_mode: smart)
  python scheduler.py --plan                       # toon de geplande runs voor 7 dagen en stop
  python scheduler.py --plan --schedule-file x.json
"""

import argparse
import json
import logging
import subprocess
import sys
import time as time_mod
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from orchestrator import load_config, setup_logging
from providers.base import hhmm_to_minutes, upcoming_booking_dates

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Amsterdam")
APP_DIR = Path(__file__).resolve().parent
DEFAULT_SCHEDULE_URL = (
    "https://raw.githubusercontent.com/hollanderbart/padel-booking/booking-research/club_schedule.json"
)
DEFAULT_CACHE_FILE = "club_schedule.json"
REQUEST_TIMEOUT = 30
RUN_TIMEOUT = 15 * 60                      # seconden per booking-run

NIGHTLY_RUN = time(0, 1)                   # volledige run, vangnet voor clubs zonder bekende strategie
FETCH_BOOKINGS_AT = time(3, 30)            # dagelijks future_bookings.json verversen
SCHEDULE_REFRESH = timedelta(hours=6)
RELEASE_DELAY = timedelta(seconds=5)       # kleine marge na het vrijgavemoment
RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5))
SLOT_STEP_MIN = 30                         # Meet & Play starttijden liggen op een half-uur-raster
MAX_SLEEP = 60                             # seconden; korte slaapstappen vangen klokwijzigingen op
DUE_GRACE = timedelta(minutes=2)           # een run die net gemist is, wordt alsnog gedaan

ROLLING = "rollend"
DAILY = "dagelijks"


@dataclass(frozen=True)
class PlannedRun:
    at: datetime
    dates: tuple[str, ...] = ()            # leeg = volledige run (alle datums, alle clubs)
    clubs: tuple[tuple[str, str, str], ...] = ()   # (naam, url, adres)
    reason: str = ""

    def target(self) -> dict | None:
        if not self.dates:
            return None
        return {
            "dates": list(self.dates),
            "clubs": [{"name": n, "url": u, "address": a} for n, u, a in self.clubs],
        }


# ---------------------------------------------------------------------------
# Planning (pure functies)
# ---------------------------------------------------------------------------

def release_moments(club: dict, target_date: date, booking_cfg: dict) -> list[datetime]:
    """Momenten waarop (een slot binnen het tijdvenster op) `target_date` bij deze club vrijkomt."""
    window = club.get("window_days")
    if window is None:
        return []
    release_day = target_date - timedelta(days=window)

    if club.get("strategy") == ROLLING:
        start = hhmm_to_minutes(booking_cfg["time_start"])
        end = hhmm_to_minutes(booking_cfg["time_end"])
        return [
            datetime.combine(release_day, time(m // 60, m % 60), TZ)
            for m in range(start, end, SLOT_STEP_MIN)
        ]
    if club.get("strategy") == DAILY:
        return [datetime.combine(release_day, time(hour), TZ) for hour in club.get("release_hours", [])]
    return []


def next_nightly(after: datetime) -> datetime:
    candidate = datetime.combine(after.date(), NIGHTLY_RUN, TZ)
    return candidate if candidate > after else candidate + timedelta(days=1)


def plan_runs(schedule: dict, booking_cfg: dict, since: datetime, until: datetime) -> list[PlannedRun]:
    """
    Alle runs met `since < at <= until`, gesorteerd. Gerichte runs op hetzelfde moment
    worden samengevoegd; daarnaast altijd de nachtelijke volledige run.
    """
    targets = [
        d.date() for d in upcoming_booking_dates(
            booking_cfg["day"], booking_cfg["time_start"], booking_cfg.get("weeks_ahead", 4), now=since,
        )
    ]

    grouped: dict[datetime, dict] = {}
    for club in schedule.get("clubs", []):
        for target_date in targets:
            for moment in release_moments(club, target_date, booking_cfg):
                at = moment + RELEASE_DELAY
                if since < at <= until:
                    group = grouped.setdefault(at, {"dates": set(), "clubs": set(), "reasons": set()})
                    group["dates"].add(target_date.isoformat())
                    group["clubs"].add((club["club_name"], club["club_url"], club.get("club_address", "")))
                    group["reasons"].add(f"{club['club_name']} ({club['strategy']}, {club['window_days']} d)")

    runs = [
        PlannedRun(at, tuple(sorted(g["dates"])), tuple(sorted(g["clubs"])), "vrijgave " + "; ".join(sorted(g["reasons"])))
        for at, g in grouped.items()
    ]
    nightly = next_nightly(since)
    if nightly <= until:
        runs.append(PlannedRun(nightly, reason="nachtelijke volledige run"))
    return sorted(runs, key=lambda r: r.at)


# ---------------------------------------------------------------------------
# Club-schema laden
# ---------------------------------------------------------------------------

def load_schedule(url: str, cache_file: Path, local_file: Path | None = None) -> dict:
    """Haal club_schedule.json op (lokaal bestand > URL > cache). Leeg schema bij geen enkele bron."""
    if local_file:
        return json.loads(local_file.read_text(encoding="utf-8"))
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        schedule = resp.json()
        cache_file.write_text(json.dumps(schedule, indent=2, ensure_ascii=False), encoding="utf-8")
        return schedule
    except (requests.RequestException, ValueError, OSError) as e:
        logger.warning("Club-schema ophalen mislukt (%s) — cache gebruiken", e)
    try:
        return json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Geen club-schema beschikbaar — alleen de nachtelijke run wordt gepland")
        return {"clubs": []}


def describe_schedule(schedule: dict) -> None:
    clubs = schedule.get("clubs", [])
    timed = [c for c in clubs if c.get("strategy") in (ROLLING, DAILY)]
    logger.info(
        "Club-schema (%s): %d club(s), %d met bekend vrijgavemoment",
        schedule.get("generated_at", "?"), len(clubs), len(timed),
    )
    for club in timed:
        hours = ", ".join(f"{h:02d}:00" for h in club.get("release_hours", []))
        logger.info("  %s — %s, %d dagen vooruit%s", club["club_name"], club["strategy"], club["window_days"],
                    f", om {hours}" if hours else "")


# ---------------------------------------------------------------------------
# Uitvoeren
# ---------------------------------------------------------------------------

def run_booking(run: PlannedRun, debug: bool) -> int:
    cmd = [sys.executable, str(APP_DIR / "orchestrator.py")]
    if debug:
        cmd.append("--debug")
    target = run.target()
    if target:
        cmd += ["--target", json.dumps(target, ensure_ascii=False)]
    logger.info("Run gestart: %s", run.reason or "volledige run")
    try:
        return subprocess.run(cmd, cwd=APP_DIR, timeout=RUN_TIMEOUT, check=False).returncode
    except subprocess.TimeoutExpired:
        logger.warning("Run duurde langer dan %d minuten en is afgebroken", RUN_TIMEOUT // 60)
        return 1


def run_fetch_bookings(config: dict) -> None:
    fetch_cfg = config.get("fetch_bookings", {})
    if not fetch_cfg.get("enabled", True):
        return
    cmd = [sys.executable, str(APP_DIR / "fetch_bookings.py"),
           "--output", fetch_cfg.get("output_file", "future_bookings.json")]
    try:
        subprocess.run(cmd, cwd=APP_DIR, timeout=RUN_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        logger.warning("Toekomstige boekingen ophalen duurde te lang en is afgebroken")


def _sleep_until(moment: datetime) -> None:
    while (remaining := (moment - datetime.now(TZ)).total_seconds()) > 0:
        time_mod.sleep(min(MAX_SLEEP, remaining))


def execute(run: PlannedRun, config: dict, debug: bool) -> None:
    """Voer een run uit; een gerichte run wordt herhaald als er (nog) niets vrij was."""
    code = run_booking(run, debug)
    if run.dates:
        for delay in RETRY_DELAYS:
            if code == 0:
                break
            _sleep_until(run.at + delay)
            code = run_booking(run, debug)
    if code == 0:
        # Werk future_bookings.json bij, zodat volgende runs deze datum overslaan
        run_fetch_bookings(config)


def run_forever(config: dict, schedule_cfg: dict, local_file: Path | None, debug: bool) -> None:
    url = schedule_cfg.get("url", DEFAULT_SCHEDULE_URL)
    cache_file = Path(schedule_cfg.get("cache_file", DEFAULT_CACHE_FILE))
    booking_cfg = config["booking"]

    schedule = load_schedule(url, cache_file, local_file)
    describe_schedule(schedule)
    loaded_at = datetime.now(TZ)
    last_fetch_day = None
    done: set[datetime] = set()

    # Zelfde gedrag als vroeger bij het starten van de addon: direct één volledige run
    execute(PlannedRun(datetime.now(TZ), reason="startrun"), config, debug)

    while True:
        now = datetime.now(TZ)
        if now - loaded_at >= SCHEDULE_REFRESH:
            schedule = load_schedule(url, cache_file, local_file)
            describe_schedule(schedule)
            loaded_at = now
        if now.time() >= FETCH_BOOKINGS_AT and last_fetch_day != now.date():
            run_fetch_bookings(config)
            last_fetch_day = now.date()

        due = [r for r in plan_runs(schedule, booking_cfg, now - DUE_GRACE, now + timedelta(days=1)) if r.at not in done]
        if not due:
            time_mod.sleep(MAX_SLEEP)
            continue
        nxt = due[0]
        if nxt.at > now:
            time_mod.sleep(min(MAX_SLEEP, (nxt.at - now).total_seconds()))
            continue

        try:
            execute(nxt, config, debug)
        except Exception:  # noqa: BLE001 — de planner moet blijven draaien
            logger.exception("Run mislukt")
        done.add(nxt.at)
        done = {at for at in done if at > now - timedelta(days=1)}


def print_plan(config: dict, schedule: dict, days: int = 7) -> None:
    now = datetime.now(TZ)
    for run in plan_runs(schedule, config["booking"], now, now + timedelta(days=days)):
        dates = ", ".join(run.dates) or "alle datums"
        clubs = ", ".join(name for name, _, _ in run.clubs) or "alle clubs"
        print(f"{run.at:%a %Y-%m-%d %H:%M:%S}  {dates:24}  {clubs}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan booking-runs op de vrijgavemomenten van clubs")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--schedule-file", type=Path, default=None, help="Lokaal club_schedule.json i.p.v. de URL")
    parser.add_argument("--plan", action="store_true", help="Toon de geplande runs voor 7 dagen en stop")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    setup_logging(args.debug)

    config = load_config(args.config)
    schedule_cfg = config.get("schedule", {})
    if args.plan:
        schedule = load_schedule(schedule_cfg.get("url", DEFAULT_SCHEDULE_URL),
                                 Path(schedule_cfg.get("cache_file", DEFAULT_CACHE_FILE)), args.schedule_file)
        describe_schedule(schedule)
        print_plan(config, schedule)
        return
    run_forever(config, schedule_cfg, args.schedule_file, args.debug)


if __name__ == "__main__":
    main()
