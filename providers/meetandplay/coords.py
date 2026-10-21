"""
Clubcoördinaten en afstand — zonder Playwright, zodat ook de planner (scheduler.py) ze kan gebruiken.

De provider bewaart de coördinaten uit de zoekpagina in `.meetandplay_club_coords.json` naast het
cookiebestand ({club-slug: [lat, lon]}). De planner leest datzelfde bestand om clubs buiten
`location.radius_km` niet te plannen.
"""

import contextlib
import json
import math
import re
from pathlib import Path

CLUB_SLUG_RE = re.compile(r"/club/([^/?#]+)")
COORDS_FILENAME = ".meetandplay_club_coords.json"


def coords_file(cookies_file: str | Path) -> Path:
    return Path(cookies_file).with_name(COORDS_FILENAME)


def load_club_coords(path: Path) -> dict[str, list[float]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_club_coords(path: Path, coords: dict[str, list[float]]) -> None:
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(coords), encoding="utf-8")


def club_slug(url: str) -> str | None:
    match = CLUB_SLUG_RE.search(url or "")
    return match.group(1) if match else None


def distance_km(a: list[float], b: list[float]) -> float:
    """Hemelsbrede afstand (haversine) tussen twee [lat, lon]-punten."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def club_distance_km(location: dict, position: list[float] | None) -> float | None:
    """Afstand van `location` (latitude/longitude) tot `position`; None als een van beide onbekend is."""
    if position is None or location.get("latitude") is None or location.get("longitude") is None:
        return None
    return distance_km([float(location["latitude"]), float(location["longitude"])], position)
