"""Registro de logs en consola y en archivo rotativo."""

from __future__ import annotations

import logging
import logging.handlers
import sys

from core.config import LOG_FILE, settings

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_configured = False


def setup_logging() -> None:
    """Instala los manejadores de consola y archivo una sola vez."""
    global _configured
    if _configured:
        return

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            logging.getLogger("korianmc").warning(
                "No se pudo cambiar la codificación de la consola; se usa la del sistema."
            )

    level = getattr(logging, settings.log_level, logging.INFO)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str = "korianmc") -> logging.Logger:
    """Devuelve un logger con el nombre indicado."""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
