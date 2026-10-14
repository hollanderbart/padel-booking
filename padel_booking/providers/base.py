"""
Gedeeld contract voor alle booking providers.

Elke provider is een zelfstandig subprocess dat:
  - een BookingRequest JSON leest van stdin via read_request()
  - een ProviderResult JSON schrijft naar stdout via result.write_stdout()
"""

import json
import logging
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Protocol

logger = logging.getLogger(__name__)

WEEKDAYS = {
    "monday": 0, "maandag": 0,
    "tuesday": 1, "dinsdag": 1,
    "wednesday": 2, "woensdag": 2,
    "thursday": 3, "donderdag": 3,
    "friday": 4, "vrijdag": 4,
    "saturday": 5, "zaterdag": 5,
    "sunday": 6, "zondag": 6,
}


@dataclass
class SlotInfo:
    club_name: str
    club_address: str
    court_name: str
    time_range: str
    payment_url: str = ""


@dataclass
class ProviderResult:
    success: bool
    provider: str
    booked_date: str | None = None   # ISO date "YYYY-MM-DD"
    slot_info: dict | None = None
    error: str | None = None

    def write_stdout(self) -> None:
        """Schrijf resultaat als JSON naar stdout. Roep precies één keer aan."""
        json.dump(asdict(self), sys.stdout)
        sys.stdout.flush()


def read_request() -> dict:
    """Lees de BookingRequest JSON van stdin."""
    return json.load(sys.stdin)


# ---------------------------------------------------------------------------
# Gedeelde datum- en tijdhelpers
# ---------------------------------------------------------------------------

def hhmm_to_minutes(value: str) -> int:
    """Zet "HH:MM" (of "HH:MM:SS") om naar minuten sinds middernacht."""
    hours, minutes = value.split(":")[:2]
    return int(hours) * 60 + int(minutes)


def upcoming_booking_dates(
    day: str, time_start: str = "00:00", count: int = 3, now: datetime | None = None,
) -> list[datetime]:
    """
    Geef de eerstvolgende `count` datums op weekdag `day`.

    Vandaag telt alleen mee als het de doelweekdag is én `time_start` nog niet
    verstreken is.
    """
    target_weekday = WEEKDAYS.get(day.lower())
    if target_weekday is None:
        raise ValueError(f"Ongeldige dag in config: {day.lower()}")

    now = now or datetime.now()
    days_ahead = (target_weekday - now.weekday()) % 7
    if days_ahead == 0:
        start = hhmm_to_minutes(time_start)
        slot_time = now.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0)
        if now >= slot_time:
            days_ahead = 7

    first_date = now + timedelta(days=days_ahead)
    return [first_date + timedelta(weeks=i) for i in range(count)]


def without_skip_dates(dates: list[datetime], skip_dates: list[str]) -> list[datetime]:
    """Filter datums weg die al geboekt zijn (skip_booked_dates)."""
    if not skip_dates:
        return dates
    remaining = [d for d in dates if d.strftime("%Y-%m-%d") not in set(skip_dates)]
    logger.info(
        "skip_booked_dates: %d datum(s) overgeslagen %s",
        len(dates) - len(remaining),
        sorted(skip_dates),
    )
    return remaining


# ---------------------------------------------------------------------------
# Subprocess entry point
# ---------------------------------------------------------------------------

class Booker(Protocol):
    def run(self) -> ProviderResult: ...


def run_provider_cli(name: str, booker_factory: Callable[[dict], Booker]) -> None:
    """Gedeelde `__main__` voor providers: stdin → booker.run() → stdout."""
    logging.basicConfig(
        level=logging.DEBUG if "--debug" in sys.argv else logging.INFO,
        format=f"%(asctime)s  %(levelname)-8s  [{name}]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    try:
        result = booker_factory(read_request()).run()
    except Exception as e:  # noqa: BLE001 — subprocess moet altijd een ProviderResult schrijven
        result = ProviderResult(success=False, provider=name, error=str(e))
    result.write_stdout()
