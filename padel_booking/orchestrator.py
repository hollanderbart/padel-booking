"""
Padel Booking Orchestrator.

Coördineert meerdere booking providers (meetandplay, playtomic) als parallelle
subprocessen. De eerste provider die een boeking succesvol afrondt wint;
overige providers worden geannuleerd.

Gebruik:
  python orchestrator.py
  python orchestrator.py --debug
  python orchestrator.py --dry-run
  python orchestrator.py --target '{"dates": ["2026-10-22"], "clubs": [{"name": "...", "url": "...", "address": "..."}]}'
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from notify import notify_booking_available
from providers.base import booking_days, upcoming_booking_dates

logger = logging.getLogger(__name__)

CONFIG_PATH = "config.yaml"
# Providers loggen zelf al een timestamp + level; strip die om dubbele prefixes te voorkomen
PROVIDER_LOG_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\s+\w+\s+")


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    enabled_by_default: bool
    email_env: str
    password_env: str
    session_file_key: str
    default_session_file: str
    # Niet leeg → provider tijdelijk uitgeschakeld, ongeacht de configuratie
    disabled_reason: str = ""


PROVIDERS = [
    ProviderSpec("meetandplay", True, "KNLTB_EMAIL", "KNLTB_PASSWORD",
                 "cookies_file", ".meetandplay_cookies.json"),
    ProviderSpec("playtomic", False, "PLAYTOMIC_EMAIL", "PLAYTOMIC_PASSWORD",
                 "token_cache_file", ".playtomic_token.json",
                 disabled_reason="API-host api.playtomic.io resolveert niet meer (DNS)"),
]


def setup_logging(debug: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Configuratie
# ---------------------------------------------------------------------------

def load_config(config_path: str = CONFIG_PATH) -> dict:
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Configuratiebestand niet gevonden: {config_path}")
    with open(config_file, encoding="utf-8") as f:
        return yaml.safe_load(f)


def enabled_providers(config: dict) -> list[tuple[ProviderSpec, dict, dict]]:
    """
    Geef per ingeschakelde provider (spec, credentials, provider_config) terug.
    Logt welke providers in- of uitgeschakeld zijn.
    """
    providers_config = config.get("providers", {})
    result = []
    for spec in PROVIDERS:
        cfg = providers_config.get(spec.name, {})
        if spec.disabled_reason:
            logger.warning("Provider '%s' tijdelijk uitgeschakeld: %s", spec.name, spec.disabled_reason)
            continue
        if not cfg.get("enabled", spec.enabled_by_default):
            logger.info("Provider '%s' uitgeschakeld", spec.name)
            continue
        logger.info("Provider '%s' ingeschakeld", spec.name)
        credentials = {
            "email": os.getenv(spec.email_env, ""),
            "password": os.getenv(spec.password_env, ""),
        }
        provider_cfg = {spec.session_file_key: cfg.get(spec.session_file_key, spec.default_session_file)}
        result.append((spec, credentials, provider_cfg))
    return result


def _write_json(path: Path, data, what: str) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        logger.warning("Fout bij schrijven %s: %s", what, e)
        return False
    return True


# ---------------------------------------------------------------------------
# Boekingsstate
# ---------------------------------------------------------------------------

def save_booking_state(state_file: Path, booked_date: str, slot_info: dict, provider: str) -> None:
    state = {
        "booked_date": booked_date,
        "booked_at": datetime.now().isoformat(timespec="seconds"),
        "provider": provider,
        "slot_info": {
            "court_name": slot_info.get("court_name", ""),
            "time_range": slot_info.get("time_range", ""),
            "club_name": slot_info.get("club_name", ""),
            "club_address": slot_info.get("club_address", ""),
        },
    }
    if _write_json(state_file, state, "booking state"):
        logger.info("Booking state opgeslagen: %s via %s op %s", slot_info.get("club_name"), provider, booked_date)


def append_booking_history(history_file: Path, booked_date: str, slot_info: dict, provider: str) -> None:
    entry = {
        "booked_date": booked_date,
        "booked_at": datetime.now().isoformat(timespec="seconds"),
        "provider": provider,
        "club_name": slot_info.get("club_name", ""),
        "club_address": slot_info.get("club_address", ""),
        "court_name": slot_info.get("court_name", ""),
        "time_range": slot_info.get("time_range", ""),
        "payment_url": slot_info.get("payment_url", ""),
    }
    history = []
    if history_file.exists():
        try:
            with open(history_file, encoding="utf-8") as f:
                history = json.load(f)
        except (OSError, ValueError) as e:
            logger.warning("Fout bij schrijven boekingsgeschiedenis: %s", e)
            return
    if _write_json(history_file, [entry, *history][:20], "boekingsgeschiedenis"):
        logger.info("Boekingsgeschiedenis bijgewerkt: %s", history_file)


def write_last_run(last_run_file: Path, success: bool, provider: str | None = None) -> None:
    data = {
        "last_run": datetime.now().isoformat(timespec="seconds"),
        "success": success,
    }
    if provider:
        data["winning_provider"] = provider
    _write_json(last_run_file, data, "last_run")


# ---------------------------------------------------------------------------
# Provider subproces uitvoering
# ---------------------------------------------------------------------------

async def run_provider(name: str, request: dict, debug: bool) -> dict:
    """
    Start een provider als subprocess, stuur request op stdin,
    lees ProviderResult JSON van stdout.
    """
    cmd = [sys.executable, "-m", f"providers.{name}.provider"]
    if debug:
        cmd.append("--debug")

    logger.info("Provider '%s' gestart...", name)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        input_data = json.dumps(request).encode()
        stdout, stderr = await proc.communicate(input=input_data)

        for line in stderr.decode(errors="replace").splitlines():
            if line.strip():
                logger.info(PROVIDER_LOG_PREFIX.sub("", line))

        if not stdout.strip():
            logger.warning("Provider '%s' gaf geen output (exit code: %s)", name, proc.returncode)
            return {"success": False, "provider": name, "error": f"geen output van provider (exit {proc.returncode})"}

        result = json.loads(stdout.decode())
        if result.get("success"):
            logger.info("Provider '%s' meldt succes: %s op %s", name, result.get("slot_info", {}).get("club_name"), result.get("booked_date"))
        else:
            logger.info("Provider '%s' meldt geen boeking: %s", name, result.get("error"))
        return result

    except asyncio.CancelledError:
        logger.info("Provider '%s' geannuleerd", name)
        raise
    except Exception as e:  # noqa: BLE001 — een crashende provider mag de andere niet stoppen
        logger.warning("Provider '%s' fout: %s", name, e)
        return {"success": False, "provider": name, "error": str(e)}


async def run_all_providers(provider_requests: list[tuple[str, dict]], debug: bool) -> dict | None:
    """
    Start alle providers parallel. Geeft het resultaat van de eerste
    die succes meldt terug, en annuleert de rest.
    """
    if not provider_requests:
        return None

    tasks = {
        asyncio.create_task(run_provider(name, req, debug), name=name): name
        for name, req in provider_requests
    }

    pending = set(tasks.keys())

    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                result = task.result()
            except asyncio.CancelledError:
                continue
            except Exception as e:  # noqa: BLE001
                logger.warning("Taak '%s' gooit exception: %s", task.get_name(), e)
                continue

            if result.get("success"):
                # Annuleer resterende providers
                for p in pending:
                    p.cancel()
                # Wacht op annuleringen
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                return result

    return None


# ---------------------------------------------------------------------------
# Al geboekte datums
# ---------------------------------------------------------------------------

def load_future_booking_dates(future_bookings_file: Path) -> set[str]:
    """
    Datums met een reservering in je account (future_bookings.json, gevuld door fetch_bookings.py).
    Geeft een lege set terug als het bestand niet bestaat of niet leesbaar is.
    """
    if not future_bookings_file.exists():
        return set()
    try:
        with open(future_bookings_file, encoding="utf-8") as f:
            bookings = json.load(f)
        dates = {b["booked_date"] for b in bookings if b.get("booked_date")}
        logger.info(
            "Al geboekte datums uit future_bookings: %s",
            ", ".join(sorted(dates)) if dates else "(geen)",
        )
        return dates
    except Exception as e:  # noqa: BLE001
        logger.warning("Fout bij lezen future_bookings: %s — skip_booked_dates wordt genegeerd", e)
        return set()


def open_dates(booking_cfg: dict, skip_dates: set[str], now: datetime | None = None) -> list[str]:
    """Doeldatums (alle gekozen weekdagen, weeks_ahead weken vooruit) zonder boeking."""
    dates = upcoming_booking_dates(
        booking_days(booking_cfg),
        booking_cfg.get("time_start", "00:00"),
        count=booking_cfg.get("weeks_ahead", 4),
        now=now,
    )
    return [d for d in (d.strftime("%Y-%m-%d") for d in dates) if d not in skip_dates]


# ---------------------------------------------------------------------------
# Hoofdflow
# ---------------------------------------------------------------------------

def build_provider_request(
    config: dict,
    credentials: dict,
    provider_config: dict,
    dry_run: bool,
    skip_dates: set[str] | None = None,
    target: dict | None = None,
) -> dict:
    """
    Stel het request samen dat naar een provider subprocess gestuurd wordt.

    `target` (gerichte run door scheduler.py) beperkt de zoektocht tot
    {"dates": ["YYYY-MM-DD", ...], "clubs": [{"name", "url", "address"}, ...]}.
    """
    request = {
        "booking_request": {
            "location": config["location"],
            "days": booking_days(config["booking"]),
            "time_start": config["booking"]["time_start"],
            "time_end": config["booking"]["time_end"],
            "duration_minutes": config["booking"].get("duration_minutes", 90),
            "court_type": config["booking"].get("court_type", "indoor"),
            "game_type": config["booking"].get("game_type", "double"),
            "weeks_ahead": config["booking"].get("weeks_ahead", 4),
            "skip_dates": sorted(skip_dates) if skip_dates else [],
        },
        "credentials": credentials,
        "provider_config": provider_config,
        "dry_run": dry_run,
    }
    if target:
        request["booking_request"]["only_dates"] = list(target.get("dates", []))
        request["booking_request"]["clubs"] = list(target.get("clubs", []))
    return request


def _remaining_target(target: dict, skip_dates: set[str]) -> dict | None:
    """Haal datums die al geboekt zijn uit een gerichte run; None als er niets overblijft."""
    dates = [d for d in target.get("dates", []) if d not in skip_dates]
    if not dates:
        logger.info("Gerichte run overgeslagen — %s al geboekt", ", ".join(target.get("dates", [])))
        return None
    return {**target, "dates": dates}


def _load_account_dates(config: dict) -> set[str]:
    """skip_booked_dates: datums met een reservering in je account (future_bookings.json)."""
    fetch_cfg = config.get("fetch_bookings", {})
    if not fetch_cfg.get("skip_booked_dates", False):
        logger.info("skip_booked_dates uitgeschakeld")
        return set()

    skip_dates = load_future_booking_dates(Path(fetch_cfg.get("output_file", "future_bookings.json")))
    if skip_dates:
        logger.info("skip_booked_dates ingeschakeld — %d datum(s) worden overgeslagen", len(skip_dates))
    else:
        logger.info("skip_booked_dates ingeschakeld — geen bestaande boekingen gevonden")
    return skip_dates


def _print_summary(provider: str, slot_info: dict) -> None:
    print("\n" + "=" * 60)
    print(f"BESCHIKBAARHEID GEVONDEN via {provider.upper()}")
    print("=" * 60)
    print(f"Club:    {slot_info['club_name']}")
    print(f"Adres:   {slot_info['club_address']}")
    print(f"Baan:    {slot_info['court_name']}")
    print(f"Tijd:    {slot_info['time_range']}")
    print(f"Boek nu: {slot_info.get('payment_url', '')}")
    print("=" * 60 + "\n")


def _handle_success(result: dict, dry_run: bool, state_file: Path, history_file: Path) -> None:
    provider = result["provider"]
    booked_date = result["booked_date"]
    slot_info = result["slot_info"]

    if not dry_run:
        save_booking_state(state_file, booked_date, slot_info, provider)
        append_booking_history(history_file, booked_date, slot_info, provider)

    notify_booking_available(
        slot_info["court_name"],
        slot_info["time_range"],
        f"{slot_info['club_name']} — {slot_info['club_address']}",
        slot_info.get("payment_url", ""),
        booked_date,
        provider,
    )
    _print_summary(provider, slot_info)


async def main_async(debug: bool, dry_run: bool, target: dict | None = None) -> int:
    load_dotenv()

    config = load_config()

    state_cfg = config.get("state", config.get("session", {}))
    state_file = Path(state_cfg.get("booking_state_file", state_cfg.get("state_file", ".booking_state.json")))
    history_file = Path(state_cfg.get("history_file", "booking_history.json"))
    last_run_file = Path(state_cfg.get("last_run_file", "last_run.json"))

    logger.info("=" * 60)
    logger.info("Padel Booking Orchestrator gestart")
    logger.info("=" * 60)

    # Al geboekt = reserveringen in je account (future_bookings.json); de boekingsgeschiedenis telt niet mee
    skip_dates = _load_account_dates(config)
    providers = enabled_providers(config)

    if target:
        logger.info("Gerichte run: %s bij %s", ", ".join(target.get("dates", [])),
                    ", ".join(c.get("name", "?") for c in target.get("clubs", [])) or "alle clubs")
        target = _remaining_target(target, skip_dates)
        if target is None:
            return 0
        providers = [p for p in providers if p[0].name == target.get("provider", "meetandplay")]
        remaining = list(target["dates"])
    else:
        remaining = open_dates(config["booking"], skip_dates)
        if not remaining:
            logger.info("Alle doeldatums zijn al geboekt — niets te doen")
            write_last_run(last_run_file, success=True)
            return 0
        logger.info("Nog te boeken: %s", ", ".join(remaining))

    if not providers:
        logger.error("Geen providers ingeschakeld — controleer config.yaml")
        write_last_run(last_run_file, success=False)
        return 1

    # Boek alle open datums: na elke boeking zoeken de providers verder zonder die datum
    winners = []
    for _ in range(len(remaining)):
        provider_requests = [
            (spec.name, build_provider_request(config, credentials, provider_cfg, dry_run, skip_dates, target))
            for spec, credentials, provider_cfg in providers
        ]
        result = await run_all_providers(provider_requests, debug=debug)
        if not (result and result.get("success")):
            break
        _handle_success(result, dry_run, state_file, history_file)
        winners.append(result["provider"])
        skip_dates.add(result["booked_date"])
        remaining = [d for d in remaining if d != result["booked_date"]]
        if not remaining:
            break
        if target:
            target = {**target, "dates": remaining}
        logger.info("Verder zoeken voor: %s", ", ".join(remaining))

    if not winners:
        logger.warning("Geen boeking gemaakt door alle providers")
        write_last_run(last_run_file, success=False)
        return 1

    logger.info("%d boeking(en) gevonden%s", len(winners),
                f" — geen slot voor {', '.join(remaining)}" if remaining else "")
    write_last_run(last_run_file, success=True, provider=winners[-1])
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Padel Booking Orchestrator")
    parser.add_argument("--debug", action="store_true", help="Verbose logging")
    parser.add_argument("--dry-run", action="store_true", help="Zoek slots maar boek niet")
    parser.add_argument(
        "--target", type=json.loads, default=None,
        help='Gerichte run (JSON): {"dates": ["YYYY-MM-DD"], "clubs": [{"name", "url", "address"}]}',
    )
    args = parser.parse_args()
    setup_logging(args.debug)

    exit_code = asyncio.run(main_async(debug=args.debug, dry_run=args.dry_run, target=args.target))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
