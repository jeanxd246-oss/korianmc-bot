"""Configuración global de KorianMC Bot.

Carga `.env` (secretos) y `config.json` (valores por defecto del servidor).
Todas las rutas y credenciales que necesita el bot salen de aquí.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
BACKUP_DIR = DATA_DIR / "backups"
TRANSCRIPT_DIR = DATA_DIR / "transcripts"
DB_PATH = DATA_DIR / "korianmc.db"
LOG_FILE = DATA_DIR / "bot.log"
DASHBOARD_FILE = DATA_DIR / "dashboard.json"

load_dotenv(BASE_DIR / ".env")


def _load_json(path: Path) -> dict[str, Any]:
    """Lee un JSON de configuración; si falta o está roto devuelve {}."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


CONFIG: dict[str, Any] = _load_json(BASE_DIR / "config.json")
BOT_SECTION: dict[str, Any] = CONFIG.get("bot", {})
DEFAULTS: dict[str, Any] = CONFIG.get("defaults", {})


def env_str(key: str, default: str = "") -> str:
    value = os.getenv(key)
    return default if value is None else value.strip()


def env_int(key: str, default: int) -> int:
    try:
        return int(env_str(key, str(default)))
    except ValueError:
        return default


def env_float(key: str, default: float) -> float:
    try:
        return float(env_str(key, str(default)))
    except ValueError:
        return default


def env_bool(key: str, default: bool) -> bool:
    raw = env_str(key, "true" if default else "false").lower()
    return raw in {"1", "true", "si", "sí", "yes", "on"}


def env_list(key: str, default: list[str]) -> list[str]:
    raw = env_str(key, "")
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def color_int(value: Any, fallback: int = 0x5865F2) -> int:
    """Convierte '#5865F2' / '5865F2' / 0x5865F2 en un entero RGB."""
    if isinstance(value, int):
        return value & 0xFFFFFF
    if isinstance(value, str) and value.strip():
        text = value.strip().lstrip("#")
        try:
            return int(text, 16) & 0xFFFFFF
        except ValueError:
            return fallback
    return fallback


class Settings:
    """Secretos y ajustes globales leídos del entorno."""

    def __init__(self) -> None:
        self.token = env_str("DISCORD_TOKEN")
        self.gemini_api_key = env_str("GEMINI_API_KEY")
        self.gemini_model = env_str("GEMINI_MODEL", "gemini-1.5-flash")
        self.ai_timeout = env_float("AI_TIMEOUT", 30.0)
        self.bot_name = str(BOT_SECTION.get("name", "KorianMC Bot"))
        self.bot_description = str(
            BOT_SECTION.get("description", "Bot premium para comunidades gaming.")
        )
        self.language = env_str("LANGUAGE", str(BOT_SECTION.get("language", "es")))
        self.prefixes = env_list("BOT_PREFIXES", list(BOT_SECTION.get("prefixes", ["!", "/"])))
        self.default_color = color_int(env_str("BOT_COLOR", str(BOT_SECTION.get("color", "#5865F2"))))
        self.invite_permissions = int(BOT_SECTION.get("invite_permissions", 2772931072))
        self.log_level = env_str("LOG_LEVEL", "INFO").upper()
        self.backup_interval_hours = env_int("BACKUP_INTERVAL_HOURS", 6)
        self.panel_enabled = env_bool("PANEL_ENABLED", False)
        self.panel_port = env_int("PANEL_PORT", 3000)
        self.panel_token = env_str("PANEL_TOKEN", "cambia_este_token_largo_y_secreto")
        self.payments_provider = env_str("PAYMENTS_PROVIDER", "none").lower()
        self.stripe_secret_key = env_str("STRIPE_SECRET_KEY")
        self.paypal_client_id = env_str("PAYPAL_CLIENT_ID")
        self.paypal_secret = env_str("PAYPAL_SECRET")

    @property
    def gemini_available(self) -> bool:
        """True si hay una clave de Gemini que parezca real."""
        key = self.gemini_api_key
        return bool(key) and key != "PEGA_AQUI_TU_TOKEN" and len(key) > 10


settings = Settings()


def guild_defaults() -> dict[str, Any]:
    """Copia profunda de los valores por defecto de un servidor."""
    return copy.deepcopy(DEFAULTS)
