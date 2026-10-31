"""Transcripciones de tickets: HTML premium, envío a logs y al usuario."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Optional

import discord

from core.config import TRANSCRIPT_DIR
from core.database import db
from core.logging_cfg import get_logger
from utils.anonymizer import redact_text
from utils.timeformat import from_sql, utcnow

log = get_logger("transcript")

_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin:0; padding:32px 16px; background:#0b0d13; color:#e6e9f2;
  font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }
.wrap { max-width: 940px; margin: 0 auto; }
header { background: linear-gradient(135deg, #141726, #1b1f33);
  border:1px solid #2b3150; border-radius:14px; padding:22px 24px; }
header h1 { margin:0 0 6px; font-size:22px; color:#5865F2; letter-spacing:.4px; }
header p { margin:4px 0; font-size:14px; color:#aab1c9; }
header .badge { display:inline-block; background:#5865F2; color:#fff; padding:2px 10px;
  border-radius:999px; font-size:12px; margin-left:6px; }
.msg { display:flex; gap:14px; padding:14px 16px; margin-top:12px;
  background:#11141f; border:1px solid #232842; border-radius:12px; }
.msg.bot { background:#131a2c; border-color:#2f3a63; }
.avatar { width:44px; height:44px; border-radius:50%; background:#5865F2; color:#fff;
  display:flex; align-items:center; justify-content:center; font-weight:700; flex:none; }
.meta { display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; }
.name { font-weight:700; color:#fff; }
.time { font-size:12px; color:#7f88a8; }
.content { margin-top:6px; font-size:15px; line-height:1.5; white-space:pre-wrap; word-break:break-word; }
.attach { margin-top:8px; font-size:13px; color:#8ab4ff; }
footer { margin-top:26px; font-size:12px; color:#6d7592; text-align:center; }
"""


async def messages_for_ticket(ticket_id: int) -> list[dict[str, Any]]:
    return await db.fetchall(
        "SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY id ASC",
        (ticket_id,),
    )


def render_html(
    guild: discord.Guild,
    ticket: dict[str, Any],
    messages: list[dict[str, Any]],
    closed_by: str = "",
    close_reason: str = "",
) -> str:
    """Genera el HTML de la transcripción con estilo oscuro premium."""
    user_id = str(ticket.get("user_id", ""))
    created = from_sql(ticket.get("created_at"))
    rows: list[str] = []
    for message in messages:
        author = str(message.get("author_name", "Usuario"))
        content = redact_text(str(message.get("content", "")))
        content = html.escape(content if content else "(mensaje sin texto)")
        stamp = message.get("created_at", "")
        initial = html.escape((author or "?")[:1])
        attachments = message.get("attachments")
        attach_html = (
            f'<div class="attach">📎 {html.escape(str(attachments))}</div>'
            if attachments else ""
        )
        bot_class = " bot" if int(message.get("is_bot", 0) or 0) else ""
        rows.append(
            f'<div class="msg{bot_class}">'
            f'<div class="avatar">{initial}</div>'
            f"<div>"
            f'<div class="meta"><span class="name">{html.escape(author)}</span>'
            f'<span class="time">{html.escape(str(stamp))}</span></div>'
            f'<div class="content">{content}</div>{attach_html}'
            f"</div></div>"
        )

    body = "\n".join(rows) or '<p style="color:#7f88a8">Sin mensajes en este ticket.</p>'
    created_txt = created.strftime("%Y-%m-%d %H:%M UTC") if created else "—"
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transcripción ticket #{ticket.get('id')} · {html.escape(guild.name)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>🎫 Ticket #{ticket.get('id')} <span class="badge">{html.escape(str(ticket.get('category_label', '')))}</span></h1>
    <p><strong>Servidor:</strong> {html.escape(guild.name)} (ID {guild.id})</p>
    <p><strong>Usuario:</strong> {html.escape(user_id)}</p>
    <p><strong>Razón:</strong> {html.escape(redact_text(str(ticket.get('reason', ''))))}</p>
    <p><strong>Creado:</strong> {created_txt}</p>
    <p><strong>Cerrado por:</strong> {html.escape(closed_by or '—')}</p>
    <p><strong>Motivo de cierre:</strong> {html.escape(close_reason or 'Sin motivo indicado')}</p>
    <p><strong>Mensajes:</strong> {len(messages)}</p>
  </header>
  {body}
  <footer>Generado por KorianMC Bot · {utcnow()} · Confidencial</footer>
</div>
</body>
</html>
"""


async def create_transcript(
    guild: discord.Guild,
    ticket: dict[str, Any],
    closed_by: str = "",
    close_reason: str = "",
) -> Optional[Path]:
    """Guarda la transcripción en data/transcripts y devuelve la ruta."""
    try:
        messages = await messages_for_ticket(int(ticket["id"]))
        html_text = render_html(guild, ticket, messages, closed_by, close_reason)
        TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"ticket-{ticket['id']}-{guild.id}.html"
        path = TRANSCRIPT_DIR / filename
        path.write_text(html_text, encoding="utf-8")
        await db.execute(
            "UPDATE tickets SET transcript_path = ? WHERE id = ?",
            (str(path), int(ticket["id"])),
        )
        return path
    except OSError as exc:
        log.error("No se pudo guardar la transcripción: %s", exc)
        return None


async def deliver_transcript(
    bot: discord.Client,
    guild: discord.Guild,
    ticket: dict[str, Any],
    path: Path,
    cfg: dict[str, Any],
) -> None:
    """Envía la transcripción al canal de logs y una copia por DM al usuario."""
    from utils.permissions import channel_id

    file = discord.File(str(path), filename=path.name)
    log_channel_id = channel_id(cfg, "ticket_logs") or channel_id(cfg, "logs")
    log_url = ""
    if log_channel_id:
        channel = guild.get_channel(log_channel_id)
        if isinstance(channel, discord.abc.Messageable):
            try:
                message = await channel.send(
                    content=f"📄 Transcripción del ticket #{ticket['id']}",
                    file=file,
                )
                log_url = f"https://discord.com/channels/{guild.id}/{channel.id}/{message.id}"
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("No se pudo subir la transcripción a logs: %s", exc)

    user_id = int(ticket.get("user_id", 0))
    try:
        user = await bot.fetch_user(user_id)
    except (discord.NotFound, discord.HTTPException):
        return
    dm_lines = [
        f"📄 Tu ticket **#{ticket['id']}** se cerró y aquí tienes la transcripción.",
        f"**Categoría:** {ticket.get('category_label', '—')}",
        f"**Motivo de cierre:** {ticket.get('close_reason') or 'Sin motivo indicado'}",
    ]
    if log_url:
        dm_lines.append(f"**Copia en el servidor:** {log_url}")
    try:
        await user.send(content="\n".join(dm_lines)[:1900], file=discord.File(str(path), filename=path.name))
    except (discord.Forbidden, discord.HTTPException) as exc:
        log.info("No se pudo enviar la transcripción por DM a %s: %s", user_id, exc)


async def recent_transcripts(limit: int = 10) -> list[dict[str, Any]]:
    return await db.fetchall(
        "SELECT id, user_id, category_label, closed_by, transcript_path "
        "FROM tickets WHERE status = 'cerrada' ORDER BY id DESC LIMIT ?",
        (limit,),
    )


__all__ = [
    "messages_for_ticket",
    "render_html",
    "create_transcript",
    "deliver_transcript",
    "recent_transcripts",
]
