"""Entry point: python -m nexuri2mqtt"""

from __future__ import annotations

import logging
import sys

from .bridge import Bridge
from .config import Config


def main() -> int:
    config = Config.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # requests logs every request at DEBUG; useful only when chasing the API.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    log = logging.getLogger("nexuri2mqtt")
    log.info(
        "starting: poll every %ss, rediscovery every %ss, shared devices %s",
        config.poll_interval,
        config.discovery_interval,
        "included" if config.include_shared else "excluded",
    )

    Bridge(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
