"""Validación de entradas de usuario para todos los comandos."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

from core.errors import ValidationError

_MENTION_RE = re.compile(r"@(everyone|here)", re.IGNORECASE)
_URL_RE = re.compile(r"^https?://[^\s<>]+$", re.IGNORECASE)
_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_INVITE_RE = re.compile(r"(discord\.(gg|io)|discordapp\.com/invite)/[a-zA-Z0-9]+", re.IGNORECASE)


def clean_text(value: Optional[str], max_len: int = 1000) -> str:
    """Recorta, colapsa espacios y elimina @everyone/@here."""
    if not value:
        return ""
    text = " ".join(str(value).split())
    text = _MENTION_RE.sub(lambda match: "@\u200b" + match.group(1), text)
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text.strip()


def require_text(value: Optional[str], label: str, min_len: int = 1, max_len: int = 1000) -> str:
    text = clean_text(value, max_len)
    if len(text) < min_len:
        raise ValidationError(
            f"{label} debe tener al menos {min_len} caracteres (recibido: {len(text)})."
        )
    return text


def slugify(value: str, max_len: int = 90) -> str:
    """Convierte un texto en algo seguro para el nombre de un canal."""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("-", normalized.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        slug = "usuario"
    return slug[:max_len].strip("-") or "usuario"


def parse_int(
    value: Any,
    label: str,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{label} debe ser un número entero.") from None
    if minimum is not None and number < minimum:
        raise ValidationError(f"{label} debe ser como mínimo {minimum}.")
    if maximum is not None and number > maximum:
        raise ValidationError(f"{label} debe ser como máximo {maximum}.")
    return number


def parse_color_value(value: str) -> int:
    raw = str(value).strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", raw):
        raise ValidationError("El color debe ser un código hexadecimal como `#5865F2`.")
    return int(raw, 16)


def is_valid_url(value: str) -> bool:
    return bool(_URL_RE.match(value.strip()))


def contains_invite(value: str) -> bool:
    return bool(_INVITE_RE.search(value or ""))


def contains_link(value: str) -> bool:
    return "http://" in (value or "").lower() or "https://" in (value or "").lower() or "www." in (value or "").lower()


def caps_percentage(value: str) -> float:
    letters = [ch for ch in value if ch.isalpha()]
    if len(letters) < 2:
        return 0.0
    upper = sum(1 for ch in letters if ch.isupper())
    return round(upper / len(letters) * 100, 1)


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))
