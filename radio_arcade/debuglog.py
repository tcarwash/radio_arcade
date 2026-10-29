"""Dedicated readable debug log for on-air opaque payloads."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def opaque_logger(directory: Path) -> logging.Logger:
    directory.mkdir(parents=True, exist_ok=True)
    target = (directory / "opaque-debug.log").resolve()
    logger = logging.getLogger("radio_arcade.opaque")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if not any(getattr(handler, "radio_arcade_path", None) == target for handler in logger.handlers):
        handler = RotatingFileHandler(target, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.radio_arcade_path = target
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)
    return logger
