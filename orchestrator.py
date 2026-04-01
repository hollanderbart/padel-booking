"""
Padel Booking Orchestrator.

Coördineert meerdere booking providers (meetandplay, playtomic) als parallelle
subprocessen. De eerste provider die een boeking succesvol afrondt wint;
overige providers worden geannuleerd.

Gebruik:
  python orchestrator.py
  python orchestrator.py --debug
  python orchestrator.py --dry-run
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

from notify import notify_booking_available

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

CONFIG_PATH = "config.yaml"


# ---------------------------------------------------------------------------
# Configuratie
# ---------------------------------------------------------------------------

def load_config(config_path: str = CONFIG_PATH) -> dict:
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Configuratiebestand niet gevonden: {config_path}")
    with open(config_file) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Deduplicatie
# ---------------------------------------------------------------------------

def is_already_booked(state_file: Path) -> bool:
    if not state_file.exists():
        return False
    try:
        with open(state_file) as f:
            state = json.load(f)
        booked_date = datetime.strptime(state["booked_date"], "%Y-%m-%d").date()
        today = datetime.now().date()
        if booked_date >= today:
            logger.info("Al geboekt voor %s — boeking overgeslagen", booked_date.isoformat())
            return True
        logger.info(
            "Vorige boeking (%s) is verlopen — nieuwe boeking starten",
            booked_date.isoformat(),
        )
        return False
    except Exception as e:
        logger.warning("Fout bij lezen booking state: %s — doorgaan met boeken", e)
        return False


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
    try:
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)
        logger.info("Booking state opgeslagen: %s via %s op %s", slot_info.get("club_name"), provider, booked_date)
    except Exception as e:
        logger.warning("Fout bij opslaan booking state: %s", e)


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
    try:
        if history_file.exists():
            with open(history_file) as f:
                history = json.load(f)
        else:
            history = []
        history.insert(0, entry)
        history = history[:20]
        with open(history_file, "w") as f:
            json.dump(history, f, indent=2)
        logger.info("Boekingsgeschiedenis bijgewerkt: %s", history_file)
    except Exception as e:
        logger.warning("Fout bij schrijven boekingsgeschiedenis: %s", e)


def write_last_run(last_run_file: Path, success: bool, provider: Optional[str] = None) -> None:
    try:
        data = {
            "last_run": datetime.now().isoformat(timespec="seconds"),
            "success": success,
        }
        if provider:
            data["winning_provider"] = provider
        with open(last_run_file, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning("Fout bij schrijven last_run: %s", e)


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

        # Log provider stderr als debug output
        if stderr:
            for line in stderr.decode(errors="replace").splitlines():
                logger.debug("[%s] %s", name, line)

        if not stdout.strip():
            logger.warning("Provider '%s' gaf geen output", name)
            return {"success": False, "provider": name, "error": "geen output van provider"}

        result = json.loads(stdout.decode())
        if result.get("success"):
            logger.info("Provider '%s' meldt succes: %s op %s", name, result.get("slot_info", {}).get("club_name"), result.get("booked_date"))
        else:
            logger.info("Provider '%s' meldt geen boeking: %s", name, result.get("error"))
        return result

    except asyncio.CancelledError:
        logger.info("Provider '%s' geannuleerd", name)
        raise
    except Exception as e:
        logger.warning("Provider '%s' fout: %s", name, e)
        return {"success": False, "provider": name, "error": str(e)}


async def run_all_providers(provider_requests: list[tuple[str, dict]], debug: bool) -> Optional[dict]:
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
            except Exception as e:
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
# Hoofdflow
# ---------------------------------------------------------------------------

def build_provider_request(config: dict, credentials: dict, provider_config: dict, dry_run: bool) -> dict:
    """Stel het request samen dat naar een provider subprocess gestuurd wordt."""
    return {
        "booking_request": {
            "location": config["location"],
            "day": config["booking"]["day"],
            "time_start": config["booking"]["time_start"],
            "time_end": config["booking"]["time_end"],
            "duration_minutes": config["booking"].get("duration_minutes", 90),
            "court_type": config["booking"].get("court_type", "indoor"),
            "game_type": config["booking"].get("game_type", "double"),
            "weeks_ahead": config["booking"].get("weeks_ahead", 4),
        },
        "credentials": credentials,
        "provider_config": provider_config,
        "dry_run": dry_run,
    }


async def main_async(debug: bool, dry_run: bool) -> int:
    load_dotenv()

    config = load_config()

    state_cfg = config.get("state", config.get("session", {}))
    state_file = Path(state_cfg.get("booking_state_file", state_cfg.get("state_file", ".booking_state.json")))
    history_file = Path(state_cfg.get("history_file", "booking_history.json"))
    last_run_file = Path(state_cfg.get("last_run_file", "last_run.json"))

    logger.info("=" * 60)
    logger.info("Padel Booking Orchestrator gestart")
    logger.info("=" * 60)

    if not dry_run and is_already_booked(state_file):
        write_last_run(last_run_file, success=True)
        return 0

    providers_config = config.get("providers", {})
    provider_requests = []

    # Meet & Play
    map_config = providers_config.get("meetandplay", {})
    if map_config.get("enabled", True):
        credentials = {
            "email": os.getenv("KNLTB_EMAIL", ""),
            "password": os.getenv("KNLTB_PASSWORD", ""),
        }
        provider_cfg = {
            "cookies_file": map_config.get("cookies_file", ".meetandplay_cookies.json"),
        }
        provider_requests.append((
            "meetandplay",
            build_provider_request(config, credentials, provider_cfg, dry_run),
        ))
        logger.info("Provider 'meetandplay' ingeschakeld")
    else:
        logger.info("Provider 'meetandplay' uitgeschakeld")

    # Playtomic
    pt_config = providers_config.get("playtomic", {})
    if pt_config.get("enabled", False):
        credentials = {
            "email": os.getenv("PLAYTOMIC_EMAIL", ""),
            "password": os.getenv("PLAYTOMIC_PASSWORD", ""),
        }
        provider_cfg = {
            "token_cache_file": pt_config.get("token_cache_file", ".playtomic_token.json"),
        }
        provider_requests.append((
            "playtomic",
            build_provider_request(config, credentials, provider_cfg, dry_run),
        ))
        logger.info("Provider 'playtomic' ingeschakeld")
    else:
        logger.info("Provider 'playtomic' uitgeschakeld")

    if not provider_requests:
        logger.error("Geen providers ingeschakeld — controleer config.yaml")
        write_last_run(last_run_file, success=False)
        return 1

    result = await run_all_providers(provider_requests, debug=debug)

    if result and result.get("success"):
        provider = result["provider"]
        booked_date = result["booked_date"]
        slot_info = result["slot_info"]

        if not dry_run:
            save_booking_state(state_file, booked_date, slot_info, provider)
            append_booking_history(history_file, booked_date, slot_info, provider)

        write_last_run(last_run_file, success=True, provider=provider)

        notify_booking_available(
            slot_info["court_name"],
            slot_info["time_range"],
            f"{slot_info['club_name']} — {slot_info['club_address']}",
            slot_info.get("payment_url", ""),
        )

        print("\n" + "=" * 60)
        print(f"BOEKING GESLAAGD via {provider.upper()}")
        print("=" * 60)
        print(f"Club:  {slot_info['club_name']}")
        print(f"Adres: {slot_info['club_address']}")
        print(f"Baan:  {slot_info['court_name']}")
        print(f"Tijd:  {slot_info['time_range']}")
        print(f"URL:   {slot_info.get('payment_url', '')}")
        print("=" * 60 + "\n")
        return 0
    else:
        logger.warning("Geen boeking gemaakt door alle providers")
        write_last_run(last_run_file, success=False)
        return 1


def main() -> None:
    debug = "--debug" in sys.argv
    dry_run = "--dry-run" in sys.argv

    if debug:
        logging.getLogger().setLevel(logging.DEBUG)

    exit_code = asyncio.run(main_async(debug=debug, dry_run=dry_run))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
