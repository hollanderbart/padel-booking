"""
Playtomic provider entry point.
Leest een BookingRequest JSON van stdin, boekt een baan via de Playtomic API,
schrijft ProviderResult naar stdout.

Gebruik:
  echo '<json>' | python -m providers.playtomic.provider
"""

import logging
import sys

from providers.base import ProviderResult, read_request
from providers.playtomic.booking import PlaytomicBooker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  [playtomic]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)


if __name__ == "__main__":
    try:
        request = read_request()

        if "--debug" in sys.argv:
            logging.getLogger().setLevel(logging.DEBUG)

        booker = PlaytomicBooker(request)
        result = booker.run()
    except Exception as e:
        result = ProviderResult(success=False, provider="playtomic", error=str(e))

    result.write_stdout()
