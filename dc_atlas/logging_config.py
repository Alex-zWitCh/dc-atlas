"""
Logging configuration for DC Atlas.
Uses RotatingFileHandler to prevent logs from filling the disk.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import get_config


def setup_logging() -> logging.Logger:
    cfg = get_config()
    log_dir = cfg.APP_LOG_DIR
    log_dir = Path(log_dir)

    logger = logging.getLogger("dc_atlas")
    log_level = (
        logging.DEBUG
        if cfg.APP_ENV == "development"
        else logging.DEBUG if os.getenv("DEBUG", "").lower() in ("1", "true", "yes")
        else logging.INFO
    )
    logger.setLevel(log_level)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # File handlers with rotation (5 MB per file, keep 3 backups)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)

        app_handler = RotatingFileHandler(
            log_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3
        )
        app_handler.setLevel(logging.DEBUG)
        app_handler.setFormatter(formatter)
        logger.addHandler(app_handler)

        error_handler = RotatingFileHandler(
            log_dir / "error.log", maxBytes=5 * 1024 * 1024, backupCount=3
        )
        error_handler.setLevel(logging.WARNING)
        error_handler.setFormatter(formatter)
        logger.addHandler(error_handler)
    except PermissionError:
        logger.warning("Could not create log files at %s", log_dir)

    return logger
