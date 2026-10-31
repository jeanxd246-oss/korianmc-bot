"""Planificador asíncrono (APScheduler) de tareas periódicas.

Todas las tareas programadas se recalculan desde la base de datos, así que
sobreviven a los reinicios: nada depende de memoria en RAM.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from core.config import BACKUP_DIR, DASHBOARD_FILE, settings
from core.database import db
from core.logging_cfg import get_logger

log = get_logger("scheduler")

TICK_SECONDS = 15
MAX_AUTO_BACKUPS = 20


class Scheduler:
    """Tareas recurrentes del bot: recordatorios, sorteos, encuestas, sanciones,
    copias de seguridad y exportación del panel web."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self.started = False
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self.started:
            return
        self._scheduler.add_job(
            self.tick,
            IntervalTrigger(seconds=TICK_SECONDS),
            id="tick",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self._scheduler.add_job(
            self.maintenance,
            IntervalTrigger(minutes=10),
            id="maintenance",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        if settings.backup_interval_hours > 0:
            self._scheduler.add_job(
                self.backup,
                IntervalTrigger(hours=settings.backup_interval_hours),
                id="backup",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        if settings.panel_enabled:
            self._scheduler.add_job(
                self.export_dashboard,
                IntervalTrigger(seconds=60),
                id="dashboard",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        self._scheduler.start()
        self.started = True
        log.info(
            "Planificador iniciado (tick cada %ss, copia cada %sh).",
            TICK_SECONDS,
            settings.backup_interval_hours,
        )

    def stop(self) -> None:
        if not self.started:
            return
        try:
            self._scheduler.shutdown(wait=False)
        except Exception:
            log.warning("No se pudo detener el planificador.")
        self.started = False

    # ------------------------------------------------------------------
    async def _run(self, coro: Any, label: str) -> None:
        try:
            await coro
        except Exception:
            log.exception("Error en la tarea programada '%s'", label)

    async def tick(self) -> None:
        """Cada pocos segundos: lo que tiene fecha límite vencida."""
        from cogs.encuestas import process_due_polls
        from cogs.moderation import process_expired_cases
        from cogs.reminders import process_due_reminders
        from services.giveaway_runner import process_due

        await self._run(process_due_reminders(self.bot), "recordatorios")
        await self._run(process_due(self.bot), "sorteos")
        await self._run(process_due_polls(self.bot), "encuestas")
        await self._run(process_expired_cases(self.bot), "sanciones")

    async def maintenance(self) -> None:
        from services.ai_limits import limiter

        limiter.cleanup()

    # ------------------------------------------------------------------
    async def backup(self) -> None:
        """Copia automática de SQLite + poda de copias antiguas."""
        try:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            dest = BACKUP_DIR / f"auto_{stamp}.db"
            info = await db.backup_to(dest, kind="auto")
            log.info("Copia automática creada (%s bytes).", info.get("size_bytes"))
            await self._prune_backups()
        except Exception:
            log.exception("No se pudo crear la copia automática.")

    async def _prune_backups(self) -> None:
        rows = await db.fetchall(
            "SELECT id, path FROM backups WHERE kind = 'auto' ORDER BY id DESC"
        )
        for row in rows[MAX_AUTO_BACKUPS:]:
            try:
                from pathlib import Path

                path = Path(row["path"])
                if path.exists():
                    path.unlink()
            except OSError:
                log.warning("No se pudo eliminar la copia antigua %s", row["path"])
            await db.execute("DELETE FROM backups WHERE id = ?", (row["id"],))

    # ------------------------------------------------------------------
    async def export_dashboard(self) -> None:
        """Escribe el JSON que lee el panel web Express (solo lectura)."""
        try:
            data = await build_dashboard(self.bot)
            tmp = DASHBOARD_FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(DASHBOARD_FILE)
        except Exception:
            log.exception("No se pudo exportar el panel de control.")


async def _count(sql: str, params: Any = ()) -> int:
    try:
        value = await db.scalar(sql, params, default=0)
        return int(value or 0)
    except Exception:
        return 0


async def build_dashboard(bot: Any) -> dict[str, Any]:
    """Resumen legible para el panel web."""
    started_at = getattr(bot, "_started_at", None)
    if started_at is None:
        started_at = datetime.now(timezone.utc)
        bot._started_at = started_at
    uptime = int((datetime.now(timezone.utc) - started_at).total_seconds())

    counts = {
        "servidores": len(bot.guilds),
        "miembros": sum(g.member_count or 0 for g in bot.guilds),
        "tickets_abiertos": await _count("SELECT COUNT(*) FROM tickets WHERE status = 'abierta'"),
        "reportes_pendientes": await _count("SELECT COUNT(*) FROM reports WHERE status = 'pendiente'"),
        "sugerencias": await _count("SELECT COUNT(*) FROM suggestions"),
        "sorteos_activos": await _count("SELECT COUNT(*) FROM giveaways WHERE status = 'activa'"),
        "encuestas_abiertas": await _count("SELECT COUNT(*) FROM polls WHERE status = 'abierta'"),
        "productos": await _count("SELECT COUNT(*) FROM products WHERE enabled = 1"),
        "postulaciones": await _count("SELECT COUNT(*) FROM applications"),
        "advertencias": await _count("SELECT COUNT(*) FROM warns WHERE active = 1"),
        "errores": await _count("SELECT COUNT(*) FROM error_logs"),
    }

    top_points = await db.fetchall(
        "SELECT guild_id, user_id, points, level FROM users ORDER BY points DESC LIMIT 10"
    )
    recent_logs = await db.fetchall(
        "SELECT guild_id, event, user_id, content, created_at FROM logs "
        "ORDER BY id DESC LIMIT 15"
    )
    recent_errors = await db.fetchall(
        "SELECT context, message, created_at FROM error_logs ORDER BY id DESC LIMIT 10"
    )

    return {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bot": {
            "nombre": str(bot.user) if bot.user else settings.bot_name,
            "id": str(bot.user.id) if bot.user else "",
            "uptime_segundos": uptime,
            "prefijos": settings.prefixes,
        },
        "recuentos": counts,
        "top_puntos": [
            {"guild": row["guild_id"], "usuario": row["user_id"], "puntos": row["points"], "nivel": row["level"]}
            for row in top_points
        ],
        "ultimos_logs": [
            {
                "servidor": row["guild_id"],
                "evento": row["event"],
                "usuario": row["user_id"],
                "detalle": row["content"],
                "fecha": row["created_at"],
            }
            for row in recent_logs
        ],
        "ultimos_errores": [
            {"contexto": row["context"], "mensaje": row["message"], "fecha": row["created_at"]}
            for row in recent_errors
        ],
    }


__all__ = ["Scheduler", "build_dashboard"]
