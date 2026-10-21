#!/usr/bin/env python3
"""
Feitenoverzicht van een snapshot gemaakt door collect.sh.

Usage: python3 -I analyze.py <snapshot_dir>

Print een compact markdown-overzicht (geen oordeel — dat doet de lezer):
addon-status, logvenster, herstarts, runs per dag, boekingen vs. account,
dekking van de doeldatums, gegroepeerde waarschuwingen/fouten, sessies, state-bestanden.
Alleen stdlib, zodat het zonder venv draait.
"""

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+([A-Z]+)\s+(.*)$")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

RUN_START = re.compile(r"^Run gestart: (.*)$")
ORCHESTRATOR_START = "Padel Booking Orchestrator gestart"
NIGHTLY_REASONS = ("nachtelijke volledige run", "nightly-modus/handmatig")
OUTCOMES = [
    (re.compile(r"^(\d+) boeking\(en\) gevonden"), "geboekt"),
    (re.compile(r"^Geen boeking gemaakt door alle providers"), "niets vrij"),
    (re.compile(r"^Gerichte run overgeslagen"), "overgeslagen (al geboekt)"),
    (re.compile(r"^Alle doeldatums zijn al geboekt"), "overgeslagen (al geboekt)"),
    (re.compile(r"^Run duurde langer dan"), "TIMEOUT"),
    (re.compile(r"^Run mislukt"), "CRASH"),
    (re.compile(r"^Geen providers ingeschakeld"), "geen providers"),
]
CLAIM = re.compile(r"^Provider '(\w+)' meldt succes: (.*) op (\d{4}-\d{2}-\d{2})")
ACCOUNT_SKIPPED = re.compile(r"^Reservering op (\d{4}-\d{2}-\d{2}) overgeslagen \(status: (.*)\)$")
LOGIN = re.compile(r"inloggen (gelukt|mislukt)|Automatisch inloggen mislukt|sessie verlopen", re.I)


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def normalize(msg: str) -> str:
    """Maak varianten van dezelfde melding gelijk (datums, getallen, URL's)."""
    msg = re.sub(r"https?://\S+", "<url>", msg)
    msg = re.sub(r"\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4}", "<datum>", msg)
    msg = re.sub(r"\d+(\.\d+)?", "N", msg)
    return msg[:160]


def parse_log(path: Path):
    entries, tracebacks = [], []
    current_tb = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE_RE.match(raw)
        if m:
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            msg = m.group(3).strip()
            entries.append((ts, m.group(2), msg))
            if current_tb:
                tracebacks.append(current_tb)
                current_tb = None
        elif raw.startswith("Traceback"):
            current_tb = {"after": entries[-1][0] if entries else None, "lines": [raw]}
        elif current_tb is not None:
            current_tb["lines"].append(raw)
    if current_tb:
        tracebacks.append(current_tb)
    return entries, tracebacks


def ago(then: datetime, now: datetime) -> str:
    delta = now - then
    if delta.total_seconds() < 0:
        return "in de toekomst"
    hours = delta.total_seconds() / 3600
    return f"{hours:.1f} u geleden" if hours < 48 else f"{hours / 24:.1f} d geleden"


def target_dates(options: dict, today: date) -> list[str]:
    """Benadering van providers.base.upcoming_booking_dates (weeks_ahead datums per weekdag, vanaf vandaag)."""
    days = options.get("booking_days") or ["thursday"]
    weeks = int(options.get("weeks_ahead", 3))
    out = []
    for day in days:
        first = today + timedelta(days=(WEEKDAYS.index(day) - today.weekday()) % 7)
        out += [(first + timedelta(weeks=i)).isoformat() for i in range(weeks)]
    return sorted(out)


def report_addon(info: dict) -> None:
    options = info.get("options", {})
    print("## Addon")
    print(f"- state: {info.get('state')}  boot: {info.get('boot')}  watchdog: {info.get('watchdog')}")
    print(f"- versie: {info.get('version')} (latest: {info.get('version_latest')}, "
          f"update_available: {info.get('update_available')})")
    keys = ["schedule_mode", "booking_days", "booking_time_start", "booking_time_end", "duration_minutes",
            "court_type", "game_type", "weeks_ahead", "location_city", "location_radius_km",
            "meetandplay_enabled", "playtomic_enabled", "fetch_bookings_enabled", "skip_booked_dates"]
    print("- opties: " + ", ".join(f"{k}={options.get(k)}" for k in keys if k in options))
    print(f"- ha_notify_device_id gezet: {bool(options.get('ha_notify_device_id'))}\n")


def report_window(entries: list, now: datetime) -> list[datetime]:
    """Logvenster en herstarts; geeft de herstart-momenten terug."""
    first, last = entries[0][0], entries[-1][0]
    restarts = [ts for ts, _, msg in entries if msg == "Run gestart: startrun"]
    print("## Logvenster")
    print(f"- {first:%Y-%m-%d %H:%M} → {last:%Y-%m-%d %H:%M} ({len(entries)} regels), "
          f"laatste regel {ago(last, now)}")
    print(f"- addon (her)starts in venster: {len(restarts)}"
          + (f" — laatste {restarts[-1]:%Y-%m-%d %H:%M}" if restarts else ""))
    configs = Counter(msg for _, _, msg in entries if msg.startswith("Config geladen:"))
    if len(configs) > 1:
        print(f"- config is binnen het venster gewijzigd: {dict(configs)}")
    print()
    return restarts


def parse_runs(entries: list) -> list[dict]:
    """
    Eén run = één orchestrator-start (beide modi). De reden komt van de scheduler-regel
    "Run gestart: …" ervoor; ontbreekt die, dan is het schedule_mode nightly of handmatig.
    """
    runs = []
    pending_reason = None
    for ts, _, msg in entries:
        if m := RUN_START.match(msg):
            pending_reason = m.group(1)
        elif msg == ORCHESTRATOR_START:
            runs.append({"start": ts, "reason": pending_reason or "nightly-modus/handmatig",
                         "outcome": None, "end": None})
            pending_reason = None
        elif runs and runs[-1]["outcome"] is None:
            for rx, label in OUTCOMES:
                if rx.match(msg):
                    runs[-1]["outcome"], runs[-1]["end"] = label, ts
                    break
    return runs


def report_runs(entries: list) -> None:
    runs = parse_runs(entries)
    print("## Runs per dag")
    print("| dag | runs | volledig | gericht | uitkomsten | nachtrun 00:01 |")
    print("|---|---|---|---|---|---|")
    by_day = defaultdict(list)
    for r in runs:
        by_day[r["start"].date()].append(r)
    day, last_day = entries[0][0].date(), entries[-1][0].date()
    while day <= last_day:
        rs = by_day.get(day, [])
        full = [r for r in rs if not r["reason"].startswith("vrijgave")]
        nightly = any(r["reason"] in NIGHTLY_REASONS and r["start"].hour == 0 for r in rs)
        outcomes = Counter(r["outcome"] or "onbekend/lopend" for r in rs)
        print(f"| {day} {day:%a} | {len(rs)} | {len(full)} | {len(rs) - len(full)} | "
              f"{', '.join(f'{k}: {v}' for k, v in outcomes.most_common())} | {'ja' if nightly else 'NEE'} |")
        day += timedelta(days=1)
    durations = sorted((r["end"] - r["start"]).total_seconds() / 60 for r in runs if r["end"])
    if durations:
        print(f"\nRunduur: mediaan {durations[len(durations) // 2]:.1f} min, max {durations[-1]:.1f} min")
    open_runs = [r for r in runs[:-1] if r["outcome"] is None]
    if open_runs:
        print("Runs zonder herkenbare uitkomst (afgebroken?): "
              + ", ".join(f"{r['start']:%m-%d %H:%M}" for r in open_runs[:10]))
    print()


def claim_status(booked: str, future_dates: dict, account_skipped: dict, today: str) -> str:
    if booked in future_dates:
        return f"in account ({future_dates[booked].get('status')})"
    if booked in account_skipped:
        return f"NIET in account — status '{account_skipped[booked]}'"
    if booked < today:
        return "datum verstreken"
    return "NIET in account (nog niet betaald / nog niet opgehaald?)"


def report_bookings(entries: list, snap: Path, options: dict, now: datetime) -> None:
    claims = [(ts, *m.groups()) for ts, _, msg in entries if (m := CLAIM.match(msg))]
    account_skipped = {}  # laatste vermelding per datum wint
    for _, _, msg in entries:
        if m := ACCOUNT_SKIPPED.match(msg):
            account_skipped[m.group(1)] = m.group(2)
    future = load_json(snap / "future_bookings.json") or []
    future_dates = {b.get("booked_date"): b for b in future}

    print("## Boekingen")
    print("Gemelde successen (provider → betaallink; telt pas als boeking na betaling):")
    for ts, provider, club, booked in claims:
        status = claim_status(booked, future_dates, account_skipped, now.date().isoformat())
        print(f"- {ts:%Y-%m-%d %H:%M} {provider}: {club} op {booked} → {status}")
    if not claims:
        print("- geen in dit logvenster")
    print("\nAccount (future_bookings.json):")
    for b in future:
        print(f"- {b.get('booked_date')} {b.get('club_name')} {b.get('time_range')} — {b.get('status')}")
    if not future:
        print("- leeg")
    if account_skipped:
        print("Overgeslagen accountreserveringen (laatste status): "
              + ", ".join(f"{d}: {s}" for d, s in sorted(account_skipped.items())))

    if options:
        targets = target_dates(options, now.date())
        missing = [d for d in targets if d not in future_dates]
        covered = [d for d in targets if d in future_dates]
        print(f"\nDoeldatums ({options.get('weeks_ahead')} wk vooruit): {len(covered)}/{len(targets)} gedekt")
        print(f"- gedekt: {', '.join(covered) or '—'}")
        print(f"- open: {', '.join(missing) or '—'}")
    print()


def report_warnings(entries: list, tracebacks: list, since: datetime) -> None:
    print("## Waarschuwingen / fouten (gegroepeerd)")
    groups = {}
    for ts, level, msg in entries:
        if level in ("WARNING", "ERROR", "CRITICAL"):
            g = groups.setdefault((level, normalize(msg)), {"n": 0, "first": ts, "example": msg})
            g["n"] += 1
            g["last"] = ts
    if groups:
        print("| n | level | eerst | laatst | sinds laatste herstart | melding |")
        print("|---|---|---|---|---|---|")
        for (level, _), g in sorted(groups.items(), key=lambda kv: -kv[1]["n"]):
            current = "ja" if g["last"] >= since else "nee"
            print(f"| {g['n']} | {level} | {g['first']:%m-%d %H:%M} | {g['last']:%m-%d %H:%M} | {current} | "
                  f"{g['example'][:140].replace('|', '/')} |")
    else:
        print("- geen")
    if tracebacks:
        print(f"\n{len(tracebacks)} traceback(s); laatste (na {tracebacks[-1]['after']}):")
        print("```\n" + "\n".join(tracebacks[-1]["lines"][-15:]) + "\n```")
    print()


def report_sessions(entries: list, snap: Path, now: datetime) -> None:
    print("## Sessies / login")
    logins = Counter()
    last_fail = None
    for ts, _, msg in entries:
        if m := LOGIN.search(msg):
            word = m.group(0).lower()
            kind = "gelukt" if "gelukt" in word else ("verlopen" if "verlopen" in word else "mislukt")
            logins[kind] += 1
            if kind == "mislukt":
                last_fail = ts
    print(f"- inlogpogingen: {dict(logins) or 'geen'}" + (f", laatste mislukking {last_fail}" if last_fail else ""))
    sessions = load_json(snap / "sessions.json") or {}
    for c in sessions.get("meetandplay_session") or []:
        exp = datetime.fromtimestamp(c["expires"]) if c.get("expires", -1) > 0 else None
        print(f"- meetandplay_session ({c.get('domain')}): verloopt {exp or 'einde browsersessie'}"
              + (" (verlopen — normaal, bot logt opnieuw in)" if exp and exp < now else ""))
    if pt := sessions.get("playtomic_token"):
        print(f"- playtomic token expiry: {pt.get('expiry')}")
    print()


def report_files(snap: Path, now: datetime) -> None:
    print("## State-bestanden (/config/padel)")
    if (snap / "files.tsv").exists():
        for line in (snap / "files.tsv").read_text().splitlines():
            mtime, size, name = line.split("\t", 2)
            changed = datetime.fromtimestamp(int(mtime))
            print(f"- {name}: {int(size):,} B, gewijzigd {changed:%Y-%m-%d %H:%M} ({ago(changed, now)})")
    if lr := load_json(snap / "last_run.json"):
        print(f"- last_run.json: {lr}")
    if st := load_json(snap / "booking_state.json"):
        print(f"- .booking_state.json: {st.get('booked_date')} {st.get('slot_info', {}).get('club_name')} "
              f"(geboekt {st.get('booked_at')})")
    if cs := load_json(snap / "club_schedule.json"):
        strategies = Counter(c.get("strategy") for c in cs.get("clubs", []))
        print(f"- club_schedule.json: generated_at {cs.get('generated_at')}, {dict(strategies)}")


def main(snap: Path) -> None:
    info = load_json(snap / "info.json") or {}
    host = (snap / "host.txt").read_text().split("\n") if (snap / "host.txt").exists() else []
    now = datetime.fromtimestamp(int(host[0])) if host and host[0].strip() else datetime.now()
    entries, tracebacks = parse_log(snap / "addon.log")

    print(f"# Padel Booking snapshot — {host[1] if len(host) > 1 else now}\n")
    report_addon(info)
    if not entries:
        print("## Log\n- GEEN parseerbare logregels\n")
        report_files(snap, now)
        return
    restarts = report_window(entries, now)
    report_runs(entries)
    report_bookings(entries, snap, info.get("options", {}), now)
    report_warnings(entries, tracebacks, restarts[-1] if restarts else entries[0][0])
    report_sessions(entries, snap, now)
    report_files(snap, now)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
