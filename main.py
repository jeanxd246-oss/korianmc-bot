#!/usr/bin/env python3
"""KorianMC Bot - Punto de entrada.

Uso:
    python main.py
"""

from __future__ import annotations

import sys

from core.bot import KorianMCBot
from core.config import DATA_DIR, settings
from core.logging_cfg import get_logger, setup_logging

log = get_logger("main")


def main() -> int:
    """Configura el registro de logs y arranca el bot."""
    setup_logging()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not settings.token:
        log.critical(
            "No se encontró DISCORD_TOKEN. Copia .env.example a .env y pega tu token "
            "de https://discord.com/developers/applications antes de arrancar."
        )
        return 1

    bot = KorianMCBot()
    log.info("Iniciando %s...", settings.bot_name)
    try:
        bot.run(settings.token, log_handler=None)
    except KeyboardInterrupt:
        log.info("Interrupción recibida, apagando el bot.")
    except Exception:
        log.exception("Error fatal durante la ejecución del bot.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
