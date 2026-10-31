"""Capa de datos SQLite de KorianMC Bot.

Reglas del módulo:
- El esquema se crea solo al arrancar (idempotente).
- Todas las consultas van parametrizadas; ningún SQL se construye por concatenación.
- Ningún otro módulo escribe SQL: pasan por estos helpers.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any, Optional, Sequence

import aiosqlite

from core.config import DB_PATH, guild_defaults
from core.logging_cfg import get_logger

log = get_logger("database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id    TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, key)
);

CREATE TABLE IF NOT EXISTS users (
    guild_id      TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    xp            INTEGER NOT NULL DEFAULT 0,
    level         INTEGER NOT NULL DEFAULT 0,
    points        INTEGER NOT NULL DEFAULT 0,
    messages      INTEGER NOT NULL DEFAULT 0,
    warnings      INTEGER NOT NULL DEFAULT 0,
    referral_code TEXT,
    referred_by   TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_users_points ON users (guild_id, points);
CREATE INDEX IF NOT EXISTS idx_users_xp ON users (guild_id, xp);

CREATE TABLE IF NOT EXISTS level_roles (
    guild_id TEXT NOT NULL,
    level    INTEGER NOT NULL,
    role_id  TEXT NOT NULL,
    PRIMARY KEY (guild_id, level)
);

CREATE TABLE IF NOT EXISTS tickets (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id       TEXT NOT NULL,
    channel_id     TEXT,
    user_id        TEXT NOT NULL,
    category       TEXT NOT NULL,
    category_label TEXT NOT NULL,
    reason         TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'abierta',
    staff_id       TEXT,
    transcript_path TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at      TEXT,
    closed_by      TEXT,
    close_reason   TEXT
);
CREATE INDEX IF NOT EXISTS idx_tickets_guild_status ON tickets (guild_id, status);
CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets (guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_tickets_channel ON tickets (channel_id);

CREATE TABLE IF NOT EXISTS ticket_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL,
    author_id   TEXT NOT NULL,
    author_name TEXT NOT NULL,
    content     TEXT NOT NULL,
    attachments TEXT,
    is_bot      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket ON ticket_messages (ticket_id, id);

CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    TEXT NOT NULL,
    reporter_id TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    target_tag  TEXT NOT NULL,
    reason      TEXT NOT NULL,
    evidence    TEXT,
    status      TEXT NOT NULL DEFAULT 'pendiente',
    message_id  TEXT,
    channel_id  TEXT,
    handled_by  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reports_guild_status ON reports (guild_id, status);

CREATE TABLE IF NOT EXISTS warns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    moderator_id TEXT NOT NULL,
    reason       TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_warns_user ON warns (guild_id, user_id, active);

CREATE TABLE IF NOT EXISTS moderation_cases (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id         TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    user_tag         TEXT,
    moderator_id     TEXT NOT NULL,
    action           TEXT NOT NULL,
    reason           TEXT,
    duration_minutes INTEGER,
    expires_at       TEXT,
    active           INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cases_expiry ON moderation_cases (active, expires_at);
CREATE INDEX IF NOT EXISTS idx_cases_user ON moderation_cases (guild_id, user_id);

CREATE TABLE IF NOT EXISTS ai_conversations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id       TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    channel_id     TEXT NOT NULL,
    last_message_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_conv_user ON ai_conversations (guild_id, user_id, channel_id);

CREATE TABLE IF NOT EXISTS ai_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    message_id      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (conversation_id) REFERENCES ai_conversations (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ai_messages_conv ON ai_messages (conversation_id, id);

CREATE TABLE IF NOT EXISTS ai_knowledge (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   TEXT NOT NULL,
    question   TEXT NOT NULL,
    answer     TEXT NOT NULL,
    keywords   TEXT,
    source     TEXT NOT NULL DEFAULT 'ia',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_knowledge_guild ON ai_knowledge (guild_id, enabled);

CREATE TABLE IF NOT EXISTS ai_moderation_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id       TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    channel_id     TEXT,
    message_id     TEXT,
    classification TEXT NOT NULL,
    score          REAL NOT NULL DEFAULT 0,
    action         TEXT NOT NULL,
    snippet        TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_mod_guild ON ai_moderation_logs (guild_id, created_at);

CREATE TABLE IF NOT EXISTS suggestions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    content     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pendiente',
    message_id  TEXT,
    channel_id  TEXT,
    handled_by  TEXT,
    ups         INTEGER NOT NULL DEFAULT 0,
    downs       INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_suggestions_guild ON suggestions (guild_id, status);

CREATE TABLE IF NOT EXISTS suggestion_votes (
    suggestion_id INTEGER NOT NULL,
    user_id       TEXT NOT NULL,
    vote          INTEGER NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (suggestion_id, user_id)
);

CREATE TABLE IF NOT EXISTS polls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   TEXT NOT NULL,
    channel_id TEXT,
    message_id TEXT,
    question   TEXT NOT NULL,
    options    TEXT NOT NULL,
    ends_at    TEXT,
    status     TEXT NOT NULL DEFAULT 'abierta',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_polls_status ON polls (guild_id, status);

CREATE TABLE IF NOT EXISTS poll_votes (
    poll_id      INTEGER NOT NULL,
    user_id      TEXT NOT NULL,
    option_index INTEGER NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (poll_id, user_id)
);

CREATE TABLE IF NOT EXISTS applications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    puesto     TEXT NOT NULL,
    answers    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pendiente',
    message_id TEXT,
    channel_id TEXT,
    handled_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_applications_guild ON applications (guild_id, status);

CREATE TABLE IF NOT EXISTS giveaways (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      TEXT NOT NULL,
    channel_id    TEXT,
    message_id    TEXT,
    prize         TEXT NOT NULL,
    winners_count INTEGER NOT NULL DEFAULT 1,
    ends_at       TEXT,
    status        TEXT NOT NULL DEFAULT 'activa',
    created_by    TEXT NOT NULL,
    winner_ids    TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_giveaways_status ON giveaways (guild_id, status, ends_at);

CREATE TABLE IF NOT EXISTS giveaway_participants (
    giveaway_id INTEGER NOT NULL,
    user_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (giveaway_id, user_id)
);

CREATE TABLE IF NOT EXISTS points_transactions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    amount     INTEGER NOT NULL,
    reason     TEXT NOT NULL,
    actor_id   TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_points_user ON points_transactions (guild_id, user_id, id);

CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    price       INTEGER NOT NULL,
    stock       INTEGER NOT NULL DEFAULT -1,
    role_id     TEXT,
    delivery    TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_products_guild ON products (guild_id, enabled);

CREATE TABLE IF NOT EXISTS purchases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    product_id   INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    price        INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pendiente',
    code         TEXT,
    delivered_by TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_purchases_user ON purchases (guild_id, user_id);

CREATE TABLE IF NOT EXISTS referrals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    TEXT NOT NULL,
    referrer_id TEXT NOT NULL,
    referred_id TEXT NOT NULL,
    bonus       INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (guild_id, referred_id)
);
CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals (guild_id, referrer_id);

CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    channel_id TEXT,
    message    TEXT NOT NULL,
    remind_at  TEXT NOT NULL,
    delivered  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (delivered, remind_at);

CREATE TABLE IF NOT EXISTS afk_users (
    guild_id TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    reason   TEXT NOT NULL DEFAULT 'AFK',
    since    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS reaction_roles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    emoji      TEXT NOT NULL,
    role_id    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (guild_id, message_id, emoji)
);
CREATE INDEX IF NOT EXISTS idx_reaction_roles_message ON reaction_roles (guild_id, message_id);

CREATE TABLE IF NOT EXISTS logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   TEXT NOT NULL,
    event      TEXT NOT NULL,
    channel_id TEXT,
    user_id    TEXT,
    target_id  TEXT,
    content    TEXT,
    message_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_logs_guild_time ON logs (guild_id, created_at);

CREATE TABLE IF NOT EXISTS error_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    context    TEXT NOT NULL,
    message    TEXT NOT NULL,
    traceback  TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS backups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    kind       TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _deep_set(node: dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    current: dict[str, Any] = node
    for part in parts[:-1]:
        nxt = current.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            current[part] = nxt
        current = nxt
    current[parts[-1]] = value


class Database:
    """Conexión única a SQLite con helpers parametrizados."""

    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = Path(path)
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        if self._conn is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.execute("PRAGMA synchronous = NORMAL")
        await conn.executescript(SCHEMA)
        await conn.commit()
        self._conn = conn
        log.info("Base de datos lista en %s", self.path)

    async def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            await conn.close()

    async def reconnect(self) -> None:
        await self.close()
        await self.connect()

    async def _ensure(self) -> aiosqlite.Connection:
        if self._conn is None:
            await self.connect()
        assert self._conn is not None
        return self._conn

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    async def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        conn = await self._ensure()
        cursor = await conn.execute(sql, tuple(params))
        await conn.commit()
        return cursor

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict[str, Any]]:
        conn = await self._ensure()
        cursor = await conn.execute(sql, tuple(params))
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row is not None else None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        conn = await self._ensure()
        cursor = await conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = await self.fetchone(sql, params)
        if not row:
            return default
        return next(iter(row.values()))

    # ------------------------------------------------------------------
    # Configuración por servidor
    # ------------------------------------------------------------------
    async def guild_config(self, guild_id: Any) -> dict[str, Any]:
        config = guild_defaults()
        rows = await self.fetchall(
            "SELECT key, value FROM guild_config WHERE guild_id = ?",
            (str(guild_id),),
        )
        for row in rows:
            try:
                value = json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                continue
            _deep_set(config, row["key"], value)
        return config

    async def set_config(self, guild_id: Any, key: str, value: Any) -> None:
        await self.execute(
            "INSERT INTO guild_config (guild_id, key, value, updated_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT (guild_id, key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (str(guild_id), key, json.dumps(value, ensure_ascii=False)),
        )

    async def set_configs(self, guild_id: Any, values: dict[str, Any]) -> None:
        for key, value in values.items():
            await self.set_config(guild_id, key, value)

    # ------------------------------------------------------------------
    # Usuarios
    # ------------------------------------------------------------------
    async def ensure_user(self, guild_id: Any, user_id: Any) -> dict[str, Any]:
        params = (str(guild_id), str(user_id))
        row = await self.fetchone("SELECT * FROM users WHERE guild_id = ? AND user_id = ?", params)
        if row is None:
            await self.execute("INSERT OR IGNORE INTO users (guild_id, user_id) VALUES (?, ?)", params)
            row = await self.fetchone("SELECT * FROM users WHERE guild_id = ? AND user_id = ?", params)
        return row or {
            "guild_id": str(guild_id),
            "user_id": str(user_id),
            "xp": 0,
            "level": 0,
            "points": 0,
            "messages": 0,
            "warnings": 0,
            "referral_code": None,
            "referred_by": None,
        }

    async def add_points(
        self, guild_id: Any, user_id: Any, amount: int, reason: str, actor_id: Any = None
    ) -> int:
        await self.ensure_user(guild_id, user_id)
        await self.execute(
            "UPDATE users SET points = MAX(0, points + ?), updated_at = datetime('now') "
            "WHERE guild_id = ? AND user_id = ?",
            (amount, str(guild_id), str(user_id)),
        )
        await self.execute(
            "INSERT INTO points_transactions (guild_id, user_id, amount, reason, actor_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(guild_id), str(user_id), amount, reason, str(actor_id) if actor_id else None),
        )
        row = await self.fetchone(
            "SELECT points FROM users WHERE guild_id = ? AND user_id = ?",
            (str(guild_id), str(user_id)),
        )
        return int(row["points"]) if row else 0

    async def add_xp(self, guild_id: Any, user_id: Any, amount: int) -> dict[str, Any]:
        await self.ensure_user(guild_id, user_id)
        await self.execute(
            "UPDATE users SET xp = xp + ?, messages = messages + 1, updated_at = datetime('now') "
            "WHERE guild_id = ? AND user_id = ?",
            (amount, str(guild_id), str(user_id)),
        )
        row = await self.fetchone(
            "SELECT xp, level FROM users WHERE guild_id = ? AND user_id = ?",
            (str(guild_id), str(user_id)),
        )
        return row or {"xp": 0, "level": 0}

    # ------------------------------------------------------------------
    # Registro de eventos y errores
    # ------------------------------------------------------------------
    async def log_event(
        self,
        guild_id: Any,
        event: str,
        *,
        channel_id: Any = None,
        user_id: Any = None,
        target_id: Any = None,
        content: str = "",
        message_id: Any = None,
    ) -> None:
        await self.execute(
            "INSERT INTO logs (guild_id, event, channel_id, user_id, target_id, content, message_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(guild_id),
                event,
                str(channel_id) if channel_id else None,
                str(user_id) if user_id else None,
                str(target_id) if target_id else None,
                content[:1900],
                str(message_id) if message_id else None,
            ),
        )

    async def log_error(self, context: str, message: str, traceback_text: str = "") -> None:
        try:
            await self.execute(
                "INSERT INTO error_logs (context, message, traceback) VALUES (?, ?, ?)",
                (context[:120], message[:1900], traceback_text[:6000]),
            )
        except Exception:
            log.error("No se pudo guardar el error en error_logs", exc_info=True)

    # ------------------------------------------------------------------
    # Copias de seguridad
    # ------------------------------------------------------------------
    async def backup_to(self, dest: Path, kind: str = "manual") -> dict[str, Any]:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        conn = await self._ensure()
        await conn.execute("VACUUM INTO ?", (str(dest),))
        await conn.commit()
        size = dest.stat().st_size
        cursor = await self.execute(
            "INSERT INTO backups (path, size_bytes, kind) VALUES (?, ?, ?)",
            (str(dest), size, kind),
        )
        row = await self.fetchone("SELECT * FROM backups WHERE id = ?", (cursor.lastrowid,))
        return row or {"path": str(dest), "size_bytes": size, "kind": kind}

    async def list_backups(self) -> list[dict[str, Any]]:
        return await self.fetchall("SELECT * FROM backups ORDER BY id DESC")

    async def restore_from(self, source: Path) -> None:
        source = Path(source)
        if not source.exists():
            raise FileNotFoundError(f"No existe la copia {source.name}")
        await self.close()
        try:
            self.path.replace(self.path.with_suffix(".db.prev"))
        except OSError:
            log.warning("No se pudo conservar la base anterior antes de restaurar.")
        # sqlite3.backup copia de forma segura aunque haya WAL.
        raw = sqlite3.connect(str(source))
        try:
            raw.execute("PRAGMA foreign_keys = ON")
            target = sqlite3.connect(str(self.path))
            try:
                raw.backup(target)
            finally:
                target.close()
        finally:
            raw.close()
        previous = self.path.with_suffix(".db.prev")
        if previous.exists():
            try:
                previous.unlink()
            except OSError:
                log.warning("No se pudo eliminar el temporal %s", previous)
        await self.connect()


db = Database()


async def init_database() -> None:
    """Abre la base de datos y crea el esquema si no existe."""
    await db.connect()


__all__ = ["Database", "db", "init_database", "SCHEMA"]
