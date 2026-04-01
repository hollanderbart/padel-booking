"""
Gedeeld contract voor alle booking providers.

Elke provider is een zelfstandig subprocess dat:
  - een BookingRequest JSON leest van stdin via read_request()
  - een ProviderResult JSON schrijft naar stdout via result.write_stdout()
"""

import json
import sys
from dataclasses import asdict, dataclass
from typing import Optional


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
    booked_date: Optional[str] = None   # ISO date "YYYY-MM-DD"
    slot_info: Optional[dict] = None
    error: Optional[str] = None

    def write_stdout(self) -> None:
        """Schrijf resultaat als JSON naar stdout. Roep precies één keer aan."""
        json.dump(asdict(self), sys.stdout)
        sys.stdout.flush()


def read_request() -> dict:
    """Lees de BookingRequest JSON van stdin."""
    return json.load(sys.stdin)
