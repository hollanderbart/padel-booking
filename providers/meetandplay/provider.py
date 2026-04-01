"""
Meet & Play provider entry point.
Leest een BookingRequest JSON van stdin, boekt een baan, schrijft ProviderResult naar stdout.

Gebruik:
  echo '<json>' | python -m providers.meetandplay.provider
"""

import logging
import sys

from providers.base import ProviderResult, read_request
from providers.meetandplay.booking import MeetAndPlayBooker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  [meetandplay]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)


if __name__ == "__main__":
    try:
        request = read_request()

        if "--debug" in sys.argv:
            logging.getLogger().setLevel(logging.DEBUG)

        booker = MeetAndPlayBooker(request)
        result = booker.run()
    except Exception as e:
        result = ProviderResult(success=False, provider="meetandplay", error=str(e))

    result.write_stdout()
