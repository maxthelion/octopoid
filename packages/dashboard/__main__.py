"""Entry point: python -m packages.dashboard"""

import logging
from pathlib import Path

from .app import OctopoidDashboard

LOG_PATH = Path.cwd() / ".octopoid" / "logs" / "dashboard.log"


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_PATH),
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("dashboard")
    try:
        app = OctopoidDashboard()
        app.run()
    except Exception:
        logger.exception("Dashboard crashed")
        raise


if __name__ == "__main__":
    main()
