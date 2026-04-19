"""CLI entry point for ``hermes-adapter-gateway``."""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


def main() -> None:
    from .app import run

    try:
        run()
    except RuntimeError as e:
        logger.error("%s", e)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Shutting down (KeyboardInterrupt)")


if __name__ == "__main__":
    main()
