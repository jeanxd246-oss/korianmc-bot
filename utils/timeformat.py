"""Utilidades de tiempo: duraciones, marcas de tiempo y conversión de fechas."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional, Union

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_DURATION_RE = re.compile(r"(\d+)\s*([smhdw])", re.IGNORECASE)


def utcnow() -> datetime:
    """Fecha y hora actuales en UTC (con zona horaria)."""
    return datetime.now(timezone.utc)


def now_str() -> str:
    """Marca de tiempo en el formato que usa SQLite (`YYYY-MM-DD HH:MM:SS`)."""
    return utcnow().strftime("%Y-%m-%d %H:%M:%S")


def parse_duration(text: Union[str, int, None]) -> Optional[int]:
    """Convierte '10m', '2h', '1d30m' o 15 en segundos. Devuelve None si no es válido."""
    if isinstance(text, int):
        return text if text > 0 else None
    if not text:
        return None
    raw = str(text).strip().lower().replace(" ", "")
    if raw.isdigit():
        return int(raw)
    total = 0
    matched = False
    for value, unit in _DURATION_RE.findall(raw):
        total += int(value) * _UNITS[unit]
        matched = True
    if not matched or total <= 0:
        return None
    return total


def format_duration(seconds: Union[int, float]) -> str:
    """'86400' -> '1d 0h'. Muestra la unidad más alta de cada bloque."""
    seconds = int(seconds)
    if seconds <= 0:
        return "0 segundos"
    parts: list[str] = []
    rest = seconds
    for unit, divisor in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        amount, rest = divmod(rest, divisor)
        if amount:
            names = {"w": "sem", "d": "d", "h": "h", "m": "min", "s": "seg"}
            parts.append(f"{amount}{names[unit]}")
    return " ".join(parts)


def parse_datetime(text: str) -> Optional[datetime]:
    """Interpreta ISO, 'YYYY-MM-DD HH:MM' o 'YYYY-MM-DD'."""
    if not text:
        return None
    raw = text.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def to_sql(dt: datetime) -> str:
    """Convierte un datetime al formato almacenable y comparable en SQLite."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def from_sql(value: Union[str, datetime, None]) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = parse_datetime(value)
    return parsed


def discord_timestamp(dt: Optional[datetime], style: str = "f") -> str:
    """Genera `<t:unix:f>` para que Discord muestre la hora local del usuario."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:{style}>"
