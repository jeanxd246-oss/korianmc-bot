"""Límites de uso de KorianMC IA: cooldown, anti-duplicado y cuota diaria."""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from typing import Any, Deque

from core.logging_cfg import get_logger

log = get_logger("ia_limites")


def _normalize(text: str) -> str:
    lowered = (text or "").lower().strip()
    cleaned = "".join(ch for ch in lowered if ch.isalnum() or ch.isspace())
    return " ".join(cleaned.split())


def _fingerprint(text: str) -> str:
    return hashlib.sha1(_normalize(text).encode("utf-8")).hexdigest()


class AILimiter:
    """Controla abuso: cooldown por usuario, mensajes repetidos y cuota diaria."""

    def __init__(self) -> None:
        self._cooldowns: dict[tuple[int, int], float] = {}
        self._duplicates: dict[tuple[int, int], dict[str, float]] = defaultdict(dict)
        self._daily: dict[tuple[int, int], tuple[str, int]] = {}
        self._recent_texts: dict[tuple[int, int], Deque[str]] = defaultdict(deque)
        self._processing: set[tuple[int, int]] = set()

    # ------------------------------------------------------------------
    def check(
        self,
        guild_id: int,
        user_id: int,
        text: str,
        cfg: dict[str, Any],
    ) -> tuple[bool, str]:
        """Devuelve (permitido, motivo). `motivo` explica el bloqueo en español."""
        ai_cfg = cfg.get("ai") or {}
        now = time.time()
        key = (int(guild_id), int(user_id))

        if key in self._processing:
            return False, "processing"

        cooldown = float(ai_cfg.get("cooldown_seconds", 20))
        last = self._cooldowns.get(key, 0.0)
        remaining = cooldown - (now - last)
        if remaining > 0:
            return False, f"cooldown:{int(remaining) + 1}"

        window = float(ai_cfg.get("duplicate_window", 120))
        fingerprint = _fingerprint(text)
        seen = self._duplicates[key]
        for old_fingerprint, stamp in list(seen.items()):
            if now - stamp > window:
                seen.pop(old_fingerprint, None)
        if fingerprint in seen:
            return False, "duplicado"

        limit = int(ai_cfg.get("max_daily_messages", 30))
        day, count = self._daily.get(key, (_today(), 0))
        if day != _today():
            count = 0
        if limit > 0 and count >= limit:
            return False, "cuota"

        return True, ""

    def register(self, guild_id: int, user_id: int, text: str) -> None:
        """Marca el mensaje como atendido (cooldown, duplicado y cuota)."""
        key = (int(guild_id), int(user_id))
        now = time.time()
        self._cooldowns[key] = now
        self._duplicates[key][_fingerprint(text)] = now
        day, count = self._daily.get(key, (_today(), 0))
        if day != _today():
            count = 0
        self._daily[key] = (day, count + 1)
        queue = self._recent_texts[key]
        queue.append(_normalize(text)[:200])
        while len(queue) > 10:
            queue.popleft()

    def was_said_recently(self, guild_id: int, user_id: int, text: str) -> bool:
        """True si el usuario acaba de decir prácticamente lo mismo."""
        normalized = _normalize(text)
        if not normalized:
            return False
        return any(
            abs(len(previous) - len(normalized)) <= 12
            and (previous == normalized or normalized[:60] in previous)
            for previous in self._recent_texts.get((int(guild_id), int(user_id)), ())
        )

    # ------------------------------------------------------------------
    def start_processing(self, guild_id: int, user_id: int) -> bool:
        key = (int(guild_id), int(user_id))
        if key in self._processing:
            return False
        self._processing.add(key)
        return True

    def stop_processing(self, guild_id: int, user_id: int) -> None:
        self._processing.discard((int(guild_id), int(user_id)))

    def reset_user(self, guild_id: int, user_id: int) -> None:
        """Borra el estado de un usuario (pruebas y `/iaconfig reset`)."""
        key = (int(guild_id), int(user_id))
        self._cooldowns.pop(key, None)
        self._duplicates.pop(key, None)
        self._daily.pop(key, None)
        self._recent_texts.pop(key, None)
        self._processing.discard(key)

    def status(self, guild_id: int, user_id: int) -> dict[str, Any]:
        key = (int(guild_id), int(user_id))
        day, count = self._daily.get(key, (_today(), 0))
        if day != _today():
            count = 0
        return {
            "hoy": count,
            "cooldown_restante": max(
                0.0, 20 - (time.time() - self._cooldowns.get(key, 0.0))
            ),
        }

    def cleanup(self) -> None:
        """Poda estados viejos para no acumular memoria."""
        now = time.time()
        for key in list(self._cooldowns):
            if now - self._cooldowns[key] > 3600:
                self._cooldowns.pop(key, None)
        for key, seen in list(self._duplicates.items()):
            for fingerprint, stamp in list(seen.items()):
                if now - stamp > 600:
                    seen.pop(fingerprint, None)
            if not seen:
                self._duplicates.pop(key, None)
        for key, (day, _) in list(self._daily.items()):
            if day != _today():
                self._daily.pop(key, None)


def _today() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


limiter = AILimiter()

__all__ = ["AILimiter", "limiter"]
