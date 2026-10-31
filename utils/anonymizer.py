"""Enmascaramiento de datos sensibles antes de guardar o exportar texto.

Se usa en transcripciones de tickets y en los logs del panel web para no
dejar tokens, correos o IPs expuestos.
"""

from __future__ import annotations

import re
from typing import Iterable

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Tokens de Discord ( formato viejo y nuevo de 59-72 caracteres )
    (re.compile(r"[\w-]{23,28}\.[\w-]{6,7}\.[\w-]{25,110}"), "[TOKEN-OCULTO]"),
    (re.compile(r"\b[\w-]{24,}\.[\w]{6}\.[\w-]{25,110}\b"), "[TOKEN-OCULTO]"),
    # Correos electrónicos
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}"), "[EMAIL-OCULTO]"),
    # Direcciones IP
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP-OCULTA]"),
    # Claves tipo sk_live / AIza
    (re.compile(r"\b(?:sk_(?:live|test)_[A-Za-z0-9]{10,}|AIza[0-9A-Za-z_-]{30,})\b"), "[CLAVE-OCULTA]"),
    # Contraseñas en texto plano
    (re.compile(r"(?i)(password|contraseña|passwd|token|api[_-]?key)\s*[:=]\s*\S+"), r"\1=[OCULTO]"),
]


def redact_text(text: str) -> str:
    """Devuelve el texto con tokens, correos, IPs y claves ocultos."""
    if not text:
        return ""
    result = str(text)
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def mask_user(user_id: str, visible: int = 4) -> str:
    """`1234567890` -> `••••7890` (para listados anónimos del panel)."""
    text = str(user_id)
    if len(text) <= visible:
        return "•" * len(text)
    return "•" * (len(text) - visible) + text[-visible:]


def clean_snippet(text: str, limit: int = 180) -> str:
    """Texto corto y seguro para loguear (una sola línea)."""
    flat = " ".join((text or "").split())
    flat = redact_text(flat)
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return flat


def filter_words(text: str, words: Iterable[str]) -> list[str]:
    """Devuelve las palabras prohibidas que aparecen en el texto."""
    lowered = (text or "").lower()
    hits: list[str] = []
    for word in words or []:
        needle = str(word).strip().lower()
        if needle and needle in lowered:
            hits.append(needle)
    return hits


__all__ = ["redact_text", "mask_user", "clean_snippet", "filter_words"]
