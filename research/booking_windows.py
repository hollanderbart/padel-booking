"""
Onderzoek per club wanneer nieuwe padelbanen vrijgegeven worden (release-strategie).

Elke run maakt per Meet & Play club een momentopname van de "horizon": de verste
dag waarop nog een boekbaar padel-tijdslot staat, plus de laatste starttijd op
die dag. Over meerdere runs (verspreid over de dag en de week) wordt daaruit
afgeleid welke strategie een club gebruikt:

  rollend      — slot T komt vrij op T − N dagen (op het exacte tijdstip)
  dagelijks    — elke dag om vast uur H komt de dag vandaag + N in één keer vrij
  blok         — een hele periode (bijv. een week) komt in één keer vrij
  geen         — geen enkel boekbaar slot gezien (bijv. alleen voor leden)

Gebruik:
  python -m research.booking_windows --data-dir research-data
  python -m research.booking_windows --data-dir research-data --report-only
"""

import argparse
import contextlib
import json
import logging
import re
import sys
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from orchestrator import build_provider_request, load_config, setup_logging

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Amsterdam")
OBSERVATIONS_FILE = "observations.jsonl"
REPORT_FILE = "BOOKING_WINDOWS.md"
SCHEDULE_FILE = "club_schedule.json"   # machine-leesbaar; gebruikt door scheduler.py in de addon

MAX_DAYS = 35                    # verder kijken heeft geen zin
STOP_AFTER_EMPTY_DAYS = 4        # zoveel lege dagen na de laatste dag met slots → horizon gevonden
GIVE_UP_AFTER_EMPTY_DAYS = 8     # de eerste N dagen leeg → club geeft (ons) geen beschikbaarheid
RETENTION_DAYS = 90
INFERENCE_DAYS = 30

# Een observatie zegt alleen iets over "rollend vs. hele dag" als het tijdstip
# ruim binnen de speeluren valt; vroeg in de ochtend is de horizon-dag bij een
# rollend venster nog leeg en lijkt die op een release van een hele dag.
INFORMATIVE_HOURS = range(10, 21)
CUTOFF_TOLERANCE_MIN = 120       # laatste slot ligt hooguit zoveel vóór het observatietijdstip
FULL_DAY_MARGIN_MIN = 60         # laatste slot ligt minstens zoveel ná het observatietijdstip
MIN_INFORMATIVE = 2
AGREEMENT = 0.75                 # aandeel observaties dat het eens moet zijn
BLOCK_HORIZON_SPREAD = 4         # horizon (in dagen) varieert minstens zoveel → blok-release

ROLLING = "rollend"
DAILY = "dagelijks"
BLOCK = "blok"
NONE_SEEN = "geen"
INSUFFICIENT = "onvoldoende data"

TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")


# ---------------------------------------------------------------------------
# Momentopname per club (pure functies)
# ---------------------------------------------------------------------------

def scan_days(probe_day: Callable[[int], list[str]], max_days: int = MAX_DAYS) -> dict[int, list[str]]:
    """
    Roep `probe_day(offset)` aan voor offset 0, 1, 2, ... en geef {offset: starttijden} terug.
    Stopt zodra de horizon duidelijk is, zodat een run niet elke club 35 dagen afloopt.
    """
    results: dict[int, list[str]] = {}
    last_with_slots = None
    for offset in range(max_days + 1):
        results[offset] = probe_day(offset)
        if results[offset]:
            last_with_slots = offset
        if last_with_slots is None and offset + 1 >= GIVE_UP_AFTER_EMPTY_DAYS:
            break
        if last_with_slots is not None and offset - last_with_slots >= STOP_AFTER_EMPTY_DAYS:
            break
    return results


def _minutes(hhmm: str) -> int:
    match = TIME_RE.search(hhmm)
    if not match:
        raise ValueError(f"Geen tijd in {hhmm!r}")
    return int(match.group(1)) * 60 + int(match.group(2))


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def summarize_scan(observed_at: datetime, day_slots: dict[int, list[str]]) -> dict:
    """Vat een scan samen tot de horizon: verste dag met slots en de tijden op die dag."""
    days_with_slots = [offset for offset, slots in day_slots.items() if slots]
    summary = {
        "observed_at": observed_at.isoformat(timespec="minutes"),
        "days_scanned": len(day_slots),
        "slots_per_day": {str(offset): len(slots) for offset, slots in sorted(day_slots.items())},
        "horizon_days": None,
        "horizon_date": None,
        "first_slot": None,
        "last_slot": None,
        "distinct_start_times": 0,
    }
    if not days_with_slots:
        return summary

    horizon = max(days_with_slots)
    starts = sorted(_minutes(s) for s in day_slots[horizon])
    summary.update({
        "horizon_days": horizon,
        "horizon_date": (observed_at.date() + timedelta(days=horizon)).isoformat(),
        "first_slot": _hhmm(starts[0]),
        "last_slot": _hhmm(starts[-1]),
        "distinct_start_times": len(set(starts)),
    })
    return summary


# ---------------------------------------------------------------------------
# Strategie afleiden uit meerdere momentopnames (pure functies)
# ---------------------------------------------------------------------------

def classify_observation(obs: dict) -> str | None:
    """
    "cutoff"   — op de horizon-dag stoppen de slots rond het observatietijdstip (→ rollend)
    "full_day" — de horizon-dag staat ook ná het observatietijdstip open (→ hele dag tegelijk)
    None       — niet informatief (buiten speeluren, geen slots, of de pagina toonde maar
                 één starttijd zodat het verloop over de dag niet te zien is)
    """
    if obs.get("last_slot") is None or obs.get("distinct_start_times", 0) < 2:
        return None
    observed = datetime.fromisoformat(obs["observed_at"])
    if observed.hour not in INFORMATIVE_HOURS:
        return None
    now_min = observed.hour * 60 + observed.minute
    last = _minutes(obs["last_slot"])
    if now_min - CUTOFF_TOLERANCE_MIN <= last <= now_min:
        return "cutoff"
    if last >= now_min + FULL_DAY_MARGIN_MIN:
        return "full_day"
    return None


def fit_daily_release(observations: list[dict], typical_horizon: int) -> tuple[int, list[int]]:
    """
    Zoek venster N en uur H die de geziene horizons het best verklaren onder het model
    "elke dag om H komt dag vandaag+N vrij" (vóór H is de horizon N−1).

    N is de meest geziene horizon, of één meer: bij een late release (bijv. 20:00)
    zien de meeste observaties nog de horizon van vóór de release.
    Geeft (N, best passende uren) terug.
    """
    def fit(window: int, hour: int) -> int:
        return sum(
            obs["horizon_days"] == (window if datetime.fromisoformat(obs["observed_at"]).hour >= hour else window - 1)
            for obs in observations
        )

    scores = {(window, hour): fit(window, hour) for window in (typical_horizon, typical_horizon + 1) for hour in range(24)}
    best = max(scores.values())
    window = min(w for (w, _), score in scores.items() if score == best)
    return window, [hour for (w, hour), score in scores.items() if w == window and score == best]


def _hour_range_label(hours: list[int]) -> str:
    if not hours:
        return "onbekend"
    if len(hours) == 1:
        return f"om {hours[0]:02d}:00"
    return f"tussen {hours[0]:02d}:00 en {hours[-1]:02d}:00"


def infer_strategy(observations: list[dict]) -> dict:
    """Leid de release-strategie van één club af uit diens observaties."""
    seen = [o for o in observations if o.get("horizon_days") is not None]
    base = {"observations": len(observations), "window_days": None, "release_hours": [], "release": "", "advice": ""}
    if not observations:
        return {**base, "strategy": INSUFFICIENT}
    if not seen:
        return {**base, "strategy": NONE_SEEN, "advice": "Geen boekbare padel-slots voor niet-leden gezien."}

    window = Counter(o["horizon_days"] for o in seen).most_common(1)[0][0]
    spread = max(o["horizon_days"] for o in seen) - min(o["horizon_days"] for o in seen)
    labels = [label for label in map(classify_observation, seen) if label]
    base["window_days"] = window

    if len(labels) < MIN_INFORMATIVE:
        return {**base, "strategy": INSUFFICIENT, "advice": f"Horizon ~{window} dagen; meer observaties overdag nodig."}

    if labels.count("cutoff") >= AGREEMENT * len(labels):
        return {
            **base,
            "strategy": ROLLING,
            "release": f"{window} × 24 uur vóór het slot",
            "advice": f"Start de booker precies {window} dagen vóór het gewenste slot, op de starttijd van het slot.",
        }

    if labels.count("full_day") < AGREEMENT * len(labels):
        return {**base, "strategy": INSUFFICIENT, "advice": "Observaties spreken elkaar tegen."}

    if spread >= BLOCK_HORIZON_SPREAD:
        low = min(o["horizon_days"] for o in seen)
        high = max(o["horizon_days"] for o in seen)
        return {
            **base,
            "strategy": BLOCK,
            "release": f"periode in één keer; horizon varieert {low}–{high} dagen",
            "advice": "Zoek het weekmoment waarop de horizon verspringt en start de booker dan.",
        }

    window, hours = fit_daily_release(seen, window)
    return {
        **base,
        "window_days": window,
        "release_hours": hours,
        "strategy": DAILY,
        "release": f"dagelijks {_hour_range_label(hours)}, {window} dagen vooruit",
        "advice": f"Start de booker dagelijks {_hour_range_label(hours)} en zoek {window} dagen vooruit.",
    }


# ---------------------------------------------------------------------------
# Opslag en rapport
# ---------------------------------------------------------------------------

def load_observations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def save_observations(path: Path, observations: list[dict], now: datetime) -> None:
    """Schrijf observaties weg en gooi alles ouder dan RETENTION_DAYS weg."""
    cutoff = now - timedelta(days=RETENTION_DAYS)
    kept = [o for o in observations if datetime.fromisoformat(o["observed_at"]) >= cutoff]
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(o, ensure_ascii=False) + "\n" for o in kept)


def infer_all(observations: list[dict], now: datetime) -> list[dict]:
    recent_cutoff = now - timedelta(days=INFERENCE_DAYS)
    by_club: dict[str, list[dict]] = {}
    for obs in observations:
        if datetime.fromisoformat(obs["observed_at"]) >= recent_cutoff and not obs.get("error"):
            by_club.setdefault(obs["club_url"], []).append(obs)

    rows = []
    for club_obs in by_club.values():
        latest = max(club_obs, key=lambda o: o["observed_at"])
        rows.append({
            "club_name": latest["club_name"],
            "club_address": latest["club_address"],
            "club_url": latest["club_url"],
            "last_seen": latest["observed_at"],
            **infer_strategy(club_obs),
        })
    order = {ROLLING: 0, DAILY: 1, BLOCK: 2, INSUFFICIENT: 3, NONE_SEEN: 4}
    return sorted(rows, key=lambda r: (order[r["strategy"]], r["club_name"]))


def build_schedule(rows: list[dict], now: datetime) -> dict:
    """Machine-leesbare samenvatting voor scheduler.py: per club strategie, venster en vrijgave-uren."""
    return {
        "generated_at": now.isoformat(timespec="minutes"),
        "clubs": [
            {
                "club_url": r["club_url"],
                "club_name": r["club_name"],
                "club_address": r["club_address"],
                "strategy": r["strategy"],
                "window_days": r["window_days"],
                "release_hours": r["release_hours"],
            }
            for r in rows
        ],
    }


def render_report(rows: list[dict], now: datetime) -> str:
    lines = [
        "# Release-strategieën padelbanen (Meet & Play)",
        "",
        f"_Automatisch gegenereerd op {now:%Y-%m-%d %H:%M} (Europe/Amsterdam) door "
        f"`.github/workflows/booking-research.yml` op basis van de observaties van de laatste "
        f"{INFERENCE_DAYS} dagen. Gemeten als niet-ingelogde bezoeker._",
        "",
        "| Club | Adres | Venster | Strategie | Vrijgave | Advies | Obs. |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        window = f"{r['window_days']} d" if r["window_days"] is not None else "–"
        lines.append(
            f"| [{r['club_name']}]({r['club_url']}) | {r['club_address']} | {window} | "
            f"{r['strategy']} | {r['release'] or '–'} | {r['advice'] or '–'} | {r['observations']} |"
        )
    lines += [
        "",
        "## Strategieën",
        "",
        f"- **{ROLLING}** — slot T komt vrij op T − N dagen, op het exacte tijdstip "
        "(bijv. de baan van 20:00 komt 7 dagen eerder om 20:00 vrij).",
        f"- **{DAILY}** — elke dag om een vast uur komt de dag vandaag + N in zijn geheel vrij "
        "(bijv. om 00:00 of 07:00).",
        f"- **{BLOCK}** — een hele periode komt in één keer vrij (bijv. elke maandag de volgende week).",
        f"- **{NONE_SEEN}** — geen boekbare slots gezien voor niet-leden.",
        f"- **{INSUFFICIENT}** — nog te weinig (informatieve) observaties; een observatie telt alleen "
        f"mee als die tussen {INFORMATIVE_HOURS.start}:00 en {INFORMATIVE_HOURS.stop}:00 gedaan is.",
        "",
        "Kanttekening: een volgeboekte dag ziet er hetzelfde uit als een dag die nog niet open is. "
        "Daarom wordt over meerdere observaties de meerderheid genomen.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Browser: clubs vinden en scannen
# ---------------------------------------------------------------------------

def _save_snapshot(page, snapshot_dir: Path | None, name: str) -> None:
    """Bewaar de pagina-HTML en de filteropties (diagnose van de paginastructuur)."""
    if snapshot_dir is None:
        return
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        (snapshot_dir / f"{name}.html").write_text(page.content(), encoding="utf-8")
        selects = page.evaluate(
            """() => [...document.querySelectorAll('select')].map(s => ({
                id: s.id, name: s.name, value: s.value,
                options: [...s.options].map(o => [o.value, o.text.trim()]),
            }))"""
        )
        (snapshot_dir / f"{name}.selects.json").write_text(json.dumps(selects, indent=2, ensure_ascii=False), encoding="utf-8")


def _probe_club(page, booker, club: dict, observed_at: datetime, snapshot_dir: Path | None = None) -> dict:
    page.goto(club["url"], wait_until="load", timeout=60000)
    page.wait_for_timeout(1500)
    booker._accept_cookies(page)
    booker._select_padel(page)
    if booker._booking.get("court_type", "indoor") == "indoor":
        indoor_select = page.locator("select#indoor")
        if indoor_select.count() > 0:
            indoor_select.select_option("INDOOR")
            page.wait_for_timeout(1500)

    def probe_day(offset: int) -> list[str]:
        day = observed_at.date() + timedelta(days=offset)
        if not booker._set_livewire_date(page, day.strftime("%d-%m-%Y")):
            raise RuntimeError("Livewire datum-component niet gevonden op clubpagina")
        # De "Tijdstip"-kiezer bevat precies de starttijden die die dag nog beschikbaar zijn;
        # de slotlijst eronder toont alleen de eerst geselecteerde tijd.
        values = page.locator('input[name="time"]').evaluate_all("els => els.map(e => e.value)")
        return [m.group(0) for v in values if (m := TIME_RE.search(v))]

    summary = summarize_scan(observed_at, scan_days(probe_day))
    if snapshot_dir is not None:
        slug = re.sub(r"\W+", "_", club["name"]).strip("_").lower()
        for label, offset in (("dag0", 0), ("horizon", summary["horizon_days"])):
            if offset is not None:
                probe_day(offset)
                _save_snapshot(page, snapshot_dir, f"{slug}_{label}")
    return summary


def observe_clubs(config: dict, max_clubs: int | None = None, snapshot_dir: Path | None = None) -> list[dict]:
    from playwright.sync_api import sync_playwright

    from providers.meetandplay.booking import MeetAndPlayBooker

    request = build_provider_request(config, {"email": "", "password": ""}, {"cookies_file": "/dev/null"}, dry_run=True)
    booker = MeetAndPlayBooker(request)
    observations = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = booker.session_manager.new_context(browser, with_cookies=False).new_page()
            clubs = booker._search_clubs(page, filter_on_date=False)[:max_clubs]
            logger.info("%d club(s) te onderzoeken", len(clubs))
            for club in clubs:
                observed_at = datetime.now(TZ)
                record = {"club_name": club["name"], "club_address": club["address"], "club_url": club["url"]}
                try:
                    record.update(_probe_club(page, booker, club, observed_at, snapshot_dir))
                    logger.info("%s: horizon %s dagen (laatste slot %s)", club["name"], record["horizon_days"], record["last_slot"])
                except Exception as e:  # noqa: BLE001 — één club mag het onderzoek niet stoppen
                    logger.warning("%s: scan mislukt: %s", club["name"], e)
                    record.update({"observed_at": observed_at.isoformat(timespec="minutes"), "error": str(e)})
                observations.append(record)
        finally:
            with contextlib.suppress(Exception):
                browser.close()
    return observations


def main() -> None:
    parser = argparse.ArgumentParser(description="Onderzoek release-strategieën van padelclubs")
    parser.add_argument("--data-dir", default="research-data", help="Map met observations.jsonl en het rapport")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--max-clubs", type=int, default=None)
    parser.add_argument("--report-only", action="store_true", help="Alleen rapport opnieuw genereren")
    parser.add_argument("--snapshot-dir", type=Path, default=None,
                        help="Bewaar per club de HTML en filteropties van dag 0 en de horizon-dag (diagnose)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    setup_logging(args.debug)

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    obs_path = data_dir / OBSERVATIONS_FILE
    observations = load_observations(obs_path)

    if not args.report_only:
        new = observe_clubs(load_config(args.config), args.max_clubs, args.snapshot_dir)
        if not new or all(o.get("error") for o in new):
            logger.error("Geen enkele club succesvol gescand — observaties niet opgeslagen")
            sys.exit(1)
        observations += new

    now = datetime.now(TZ)
    save_observations(obs_path, observations, now)
    rows = infer_all(observations, now)
    (data_dir / REPORT_FILE).write_text(render_report(rows, now), encoding="utf-8")
    (data_dir / SCHEDULE_FILE).write_text(json.dumps(build_schedule(rows, now), indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Rapport geschreven naar %s", data_dir / REPORT_FILE)


if __name__ == "__main__":
    main()
