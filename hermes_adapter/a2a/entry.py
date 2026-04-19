"""CLI entry point for the A2A server.

Loads ``~/.hermes/.env`` when present, then starts the A2A HTTP server via
uvicorn. Run via ``hermes-adapter-a2a`` or ``python -m hermes_adapter.a2a``.

Environment:
    A2A_HOST          bind host        (default: 0.0.0.0)
    A2A_PORT          bind port        (default: 9000)
    A2A_KEY           optional Bearer token
    A2A_PUBLIC_URL    URL advertised in the Agent Card
    AGENT_NAME        Agent Card name
    AGENT_DESCRIPTION Agent Card description
    AGENT_SKILLS      comma-separated skill names
    AGENT_MODEL       Model hint in Agent Card metadata
    A2A_TOOLSETS      Comma-separated toolset filter for Hermes
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def _load_env() -> None:
    """Best-effort .env loading: prefer hermes_cli's loader, else python-dotenv."""
    try:
        from hermes_cli.env_loader import load_hermes_dotenv
        from hermes_constants import get_hermes_home

        loaded = load_hermes_dotenv(hermes_home=get_hermes_home())
        for env_file in loaded or []:
            logger.info("Loaded env from %s", env_file)
        return
    except ImportError:
        pass

    try:
        from dotenv import load_dotenv

        hermes_home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
        env_path = hermes_home / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
            logger.info("Loaded env from %s", env_path)
    except ImportError:
        logger.debug("python-dotenv not available; relying on system env")


def run(host: str | None = None, port: int | None = None) -> None:
    """Start the A2A server on *host*:*port*."""
    try:
        import uvicorn
    except ImportError as e:
        raise RuntimeError(
            "uvicorn is not installed. Install with: pip install 'hermes-adapter[a2a]'"
        ) from e

    from .server import build_app

    host = host or os.getenv("A2A_HOST", "0.0.0.0")
    port = port or int(os.getenv("A2A_PORT", "9000"))

    # Ensure hermes-agent's project root is on sys.path so ``from run_agent import AIAgent``
    # works when the user has cloned hermes-agent rather than pip-installed it.
    hermes_root = os.getenv("HERMES_AGENT_ROOT")
    if hermes_root and hermes_root not in sys.path:
        sys.path.insert(0, hermes_root)

    logger.info("Starting A2A adapter on http://%s:%d", host, port)
    logger.info("Agent Card: http://%s:%d/.well-known/agent.json", host, port)

    app = build_app(port=port)
    uvicorn.run(app, host=host, port=port, log_level="warning")


def main() -> None:
    """Entry point used by the ``hermes-adapter-a2a`` console script."""
    _setup_logging()
    _load_env()
    try:
        run()
    except RuntimeError as e:
        logger.error("%s", e)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Shutting down (KeyboardInterrupt)")


if __name__ == "__main__":
    main()
