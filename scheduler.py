"""
Slimme planning: start de booker op het moment dat een club nieuwe banen vrijgeeft.

Leest per club de release-strategie uit club_schedule.json (gegenereerd door de
booking-research workflow, zie research/booking_windows.py) en plant gerichte runs:

  rollend   — slot T komt vrij op T − N dagen → run op elke starttijd in het
              tijdvenster, N dagen vóór de doeldag
  dagelijks — doeldag D komt vrij op D − N dagen om uur H → run om H:00
  overig    — geen gerichte run; de nachtelijke volledige run (00:01) dekt deze clubs

Een gerichte run start PREPARE_AHEAD vóór het vrijgavemoment: de provider logt dan al in, opent
de clubdagen en ververst ze vanaf het moment zelf (zie MeetAndPlayBooker._book_at_release).

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
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from orchestrator import load_config, setup_logging
from providers.base import booking_days, hhmm_to_minutes, upcoming_booking_dates
from providers.meetandplay.coords import club_distance_km, club_slug, coords_file, load_club_coords

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
SCHEDULE_REFRESH = timedelta(hours=6)
PREPARE_AHEAD = timedelta(minutes=2)       # gerichte run start eerder: inloggen en clubdagen openen
RETRY_DELAYS = (timedelta(minutes=5),)     # na het vrijgavemoment; de eerste run ververst zelf al 90 s
SLOT_STEP_MIN = 30                         # Meet & Play starttijden liggen op een half-uur-raster
MAX_SLEEP = 60                             # seconden; korte slaapstappen vangen klokwijzigingen op
LATE_AFTER = timedelta(minutes=2)          # later gestart = ingehaald: gelogd, retries tellen vanaf nu

ROLLING = "rollend"
DAILY = "dagelijks"


@dataclass(frozen=True)
class PlannedRun:
    at: datetime
    dates: tuple[str, ...] = ()            # leeg = volledige run (alle datums, alle clubs)
    clubs: tuple[tuple[str, str, str], ...] = ()   # (naam, url, adres)
    reason: str = ""
    release_at: datetime | None = None     # vrijgavemoment van een gerichte run (die start eerder)

    @property
    def moment(self) -> datetime:
        """Het moment waar de run om draait: de vrijgave, of de starttijd bij een volledige run."""
        return self.release_at or self.at

    def target(self) -> dict | None:
        if not self.dates:
            return None
        target = {
            "dates": list(self.dates),
            "clubs": [{"name": n, "url": u, "address": a} for n, u, a in self.clubs],
        }
        if self.release_at:
            target["release_at"] = self.release_at.isoformat()
        return target


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
            for m in range(start, end + 1, SLOT_STEP_MIN)   # time_end is de laatste starttijd
        ]
    if club.get("strategy") == DAILY:
        return [datetime.combine(release_day, time(hour), TZ) for hour in club.get("release_hours", [])]
    return []


def next_nightly(after: datetime) -> datetime:
    candidate = datetime.combine(after.date(), NIGHTLY_RUN, TZ)
    return candidate if candidate > after else candidate + timedelta(days=1)


def plan_runs(schedule: dict, booking_cfg: dict, since: datetime, until: datetime) -> list[PlannedRun]:
    """
    Alle runs met `since < at <= until`, gesorteerd. Gerichte runs voor hetzelfde vrijgavemoment
    worden samengevoegd en starten PREPARE_AHEAD eerder; daarnaast altijd de nachtelijke volledige run.
    """
    def targets_at(moment: datetime, extra_weeks: int = 0) -> list[date]:
        return [
            d.date() for d in upcoming_booking_dates(
                booking_days(booking_cfg), booking_cfg["time_start"],
                booking_cfg.get("weeks_ahead", 4) + extra_weeks, now=moment,
            )
        ]

    # Doeldagen die vóór `until` binnen het zoekbereik (weeks_ahead) komen; per vrijgavemoment
    # wordt gecontroleerd of de doeldag op dát moment binnen het bereik valt.
    candidates = targets_at(since, extra_weeks=(until - since).days // 7 + 1)

    grouped: dict[datetime, dict] = {}
    for club in schedule.get("clubs", []):
        for target_date in candidates:
            for moment in release_moments(club, target_date, booking_cfg):
                if since < moment - PREPARE_AHEAD <= until and target_date in targets_at(moment):
                    group = grouped.setdefault(moment, {"dates": set(), "clubs": set(), "reasons": set()})
                    group["dates"].add(target_date.isoformat())
                    group["clubs"].add((club["club_name"], club["club_url"], club.get("club_address", "")))
                    group["reasons"].add(f"{club['club_name']} ({club['strategy']}, {club['window_days']} d)")

    runs = [
        PlannedRun(moment - PREPARE_AHEAD, tuple(sorted(g["dates"])), tuple(sorted(g["clubs"])),
                   "vrijgave " + "; ".join(sorted(g["reasons"])), release_at=moment)
        for moment, g in grouped.items()
    ]
    nightly = next_nightly(since)
    if nightly <= until:
        runs.append(PlannedRun(nightly, reason="nachtelijke volledige run"))
    return sorted(runs, key=lambda r: r.at)


def next_due(planned: list[PlannedRun], now: datetime) -> tuple[PlannedRun | None, list[PlannedRun]]:
    """
    Kies de run die nu moet draaien uit `planned` (gesorteerd, nog niet uitgevoerd).

    Geeft (run, gedekte runs) terug. Zijn er meerdere runs aan de beurt — bijvoorbeeld omdat
    ze vielen terwijl een eerdere run (met retries) nog liep — dan worden ze samengevoegd tot
    één run nu: een volledige run als er één tussen zit (die dekt alle datums en clubs),
    anders één gerichte run over alle datums en clubs samen.
    """
    due = [r for r in planned if r.at <= now]
    if len(due) <= 1:
        return (due[0] if due else None), due
    missed = ", ".join(f"{r.moment:%H:%M}" for r in due)
    if any(not r.dates for r in due):
        return PlannedRun(now, reason=f"ingehaalde volledige run (gemist: {missed})"), due
    dates = tuple(sorted({d for r in due for d in r.dates}))
    clubs = tuple(sorted({c for r in due for c in r.clubs}))
    release_at = min(r.release_at for r in due if r.release_at) if any(r.release_at for r in due) else None
    return PlannedRun(now, dates, clubs, f"ingehaald ({missed}): " + "; ".join(r.reason for r in due),
                      release_at=release_at), due


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


def within_radius(schedule: dict, location: dict, coords: dict[str, list[float]]) -> dict:
    """
    Schema zonder clubs die hemelsbreed verder liggen dan `location.radius_km` — de provider slaat
    die toch over, dus een gerichte run voor alleen zulke clubs doet niets (en kan de nachtrun
    ophouden). Clubs zonder bekende locatie blijven staan, net als in de provider.
    """
    if location.get("radius_km") is None:
        return schedule
    radius = float(location["radius_km"])
    kept = []
    for club in schedule.get("clubs", []):
        km = club_distance_km(location, coords.get(club_slug(club.get("club_url", "")) or ""))
        if km is not None and km > radius:
            logger.info("%s buiten straal: %.1f km (straal %g km) — niet gepland", club["club_name"], km, radius)
            continue
        kept.append(club)
    return {**schedule, "clubs": kept}


def load_nearby_schedule(config: dict, url: str, cache_file: Path, local_file: Path | None = None) -> dict:
    """Club-schema laden en clubs buiten de straal weglaten (coördinaten uit de cache van de provider)."""
    schedule = load_schedule(url, cache_file, local_file)
    cookies_file = config.get("providers", {}).get("meetandplay", {}).get("cookies_file", ".meetandplay_cookies.json")
    return within_radius(schedule, config.get("location", {}), load_club_coords(coords_file(cookies_file)))


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
    logger.info("Run gestart: %s%s", run.reason or "volledige run",
                f" — vrijgave om {run.release_at:%H:%M:%S}" if run.release_at else "")
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
    if not run.dates:
        # Volledige run: eerst de reserveringen uit je account ophalen, zodat geboekte datums
        # overgeslagen worden. Gerichte runs niet — die moeten direct op het vrijgavemoment starten.
        run_fetch_bookings(config)
    code = run_booking(run, debug)
    if run.dates:
        for delay in RETRY_DELAYS:
            if code == 0:
                break
            _sleep_until(max(run.moment, run.at) + delay)   # ingehaalde run: vanaf de inhaalstart
            code = run_booking(run, debug)


def run_forever(config: dict, schedule_cfg: dict, local_file: Path | None, debug: bool) -> None:
    url = schedule_cfg.get("url", DEFAULT_SCHEDULE_URL)
    cache_file = Path(schedule_cfg.get("cache_file", DEFAULT_CACHE_FILE))
    booking_cfg = config["booking"]

    schedule = load_nearby_schedule(config, url, cache_file, local_file)
    describe_schedule(schedule)
    loaded_at = datetime.now(TZ)
    # Alle geplande runs tot en met `checked_until` zijn uitgevoerd (of gedekt door de startrun).
    # Pas als er niets meer openstaat schuift dit op, zodat runs die vallen terwijl een andere
    # run nog bezig is daarna alsnog gedaan worden in plaats van stil te verdwijnen.
    checked_until = datetime.now(TZ)
    done: set[datetime] = set()

    # Zelfde gedrag als vroeger bij het starten van de addon: direct één volledige run
    execute(PlannedRun(checked_until, reason="startrun"), config, debug)

    while True:
        now = datetime.now(TZ)
        if now - loaded_at >= SCHEDULE_REFRESH:
            schedule = load_nearby_schedule(config, url, cache_file, local_file)
            describe_schedule(schedule)
            loaded_at = now

        planned = [r for r in plan_runs(schedule, booking_cfg, checked_until, now + timedelta(days=1))
                   if r.at not in done]
        run, covered = next_due(planned, now)
        if run is None:
            checked_until = now
            done = {at for at in done if at > checked_until}
            wait = (planned[0].at - now).total_seconds() if planned else MAX_SLEEP
            time_mod.sleep(min(MAX_SLEEP, wait))
            continue

        late = now - covered[0].at
        if late > LATE_AFTER:
            logger.info("Run van %s wordt ingehaald (%d min te laat)",
                        ", ".join(f"{r.moment:%H:%M}" for r in covered), late.total_seconds() // 60)
            run = replace(run, at=now)
        try:
            execute(run, config, debug)
        except Exception:  # noqa: BLE001 — de planner moet blijven draaien
            logger.exception("Run mislukt")
        done.update(r.at for r in covered)


def print_plan(config: dict, schedule: dict, days: int = 7) -> None:
    now = datetime.now(TZ)
    for run in plan_runs(schedule, config["booking"], now, now + timedelta(days=days)):
        dates = ", ".join(run.dates) or "alle datums"
        clubs = ", ".join(name for name, _, _ in run.clubs) or "alle clubs"
        print(f"{run.moment:%a %Y-%m-%d %H:%M:%S}  {dates:24}  {clubs}")


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
        schedule = load_nearby_schedule(config, schedule_cfg.get("url", DEFAULT_SCHEDULE_URL),
                                        Path(schedule_cfg.get("cache_file", DEFAULT_CACHE_FILE)), args.schedule_file)
        describe_schedule(schedule)
        print_plan(config, schedule)
        return
    run_forever(config, schedule_cfg, args.schedule_file, args.debug)


if __name__ == "__main__":
    main()
