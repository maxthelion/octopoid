"""Local Multi-Agent Scheduler - File-driven orchestrator for Claude Code agents."""
import logging
import logging.handlers
import sys
sys.dont_write_bytecode = True

__version__ = "0.1.0"


def _setup_logging() -> None:
    """Configure the octopoid package logger.

    Sets up a RotatingFileHandler writing to .octopoid/runtime/logs/octopoid.log
    and a StreamHandler on stderr at WARNING level.

    Called once at import time. Idempotent — safe to call multiple times.
    """
    root_logger = logging.getLogger("octopoid")

    # Avoid adding duplicate handlers on re-import
    if root_logger.handlers:
        return

    root_logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Rotating file handler — try to set up, silently skip if path unavailable
    try:
        from .config import get_logs_dir  # noqa: PLC0415
        logs_dir = get_logs_dir()
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            logs_dir / "octopoid.log",
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except Exception:
        pass  # No project root found (e.g. in tests) — skip file handler

    # StreamHandler on stderr at WARNING so critical errors appear in launchd-stderr.log
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(formatter)
    root_logger.addHandler(stderr_handler)


_setup_logging()
