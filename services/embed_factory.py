"""Fábrica de embeds por módulo (bienvenida, tickets, niveles, sorteos...)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

import discord

from utils.embeds import add_fields, base_embed, progress_bar
from utils.timeformat import discord_timestamp, format_duration, utcnow
from utils.validators import clean_text


# ---------------------------------------------------------------------------
# Bienvenida / despedida
# ---------------------------------------------------------------------------
def welcome_embed(member: discord.Member, cfg: dict[str, Any]) -> discord.Embed:
    account_age = datetime.utcnow() - member.created_at.replace(tzinfo=None)
    embed = base_embed(f"👋 Bienvenido/a, {member.display_name}")
    embed.description = (
        f"**{member.name}** llegó a **{member.guild.name}**.\n"
        f"¡Que disfrutes la comunidad!"
    )
    embed.set_thumbnail(url=member.display_avatar.with_size(256).url)
    add_fields(
        embed,
        [
            ("🆔 Usuario", f"`{member.id}`", True),
            ("📅 Cuenta creada", f"{discord_timestamp(member.created_at)}\nhace {format_duration(account_age.total_seconds())}", True),
            ("👥 Miembro n.º", f"#{len(member.guild.members)}", True),
            ("📆 Se unió hoy", discord_timestamp(utcnow(), "F"), True),
        ],
    )
    return embed


def leave_embed(member: discord.Member | discord.User, cfg: dict[str, Any]) -> discord.Embed:
    embed = base_embed(f"👋 {member.name} se fue")
    embed.description = f"**{member.name}** dejó de estar en **{member.guild.name}**."
    embed.set_thumbnail(url=member.display_avatar.with_size(256).url)
    add_fields(
        embed,
        [
            ("🆔 Usuario", f"`{member.id}`", True),
            ("📅 Cuenta creada", discord_timestamp(member.created_at), True),
        ],
    )
    return embed


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------
def ticket_open_embed(
    member: discord.abc.User, cfg: dict[str, Any], category_label: str, reason: str, ticket_id: int
) -> discord.Embed:
    embed = base_embed(f"🎫 Ticket #{ticket_id} · {category_label}")
    embed.description = (
        f"**Usuario:** {member.mention} (`{member.id}`)\n"
        f"**Categoría:** {category_label}\n"
        f"**Razón:**\n```{clean_text(reason, 800)}```"
    )
    embed.set_thumbnail(url=member.display_avatar.with_size(256).url)
    add_fields(embed, [("📅 Abierto", discord_timestamp(datetime.now()), True)])
    embed.set_footer(text="Usa los botones de abajo para gestionar el ticket")
    return embed


def ticket_close_embed(
    cfg: dict[str, Any],
    ticket: dict[str, Any],
    closed_by: discord.abc.User,
    reason: str,
) -> discord.Embed:
    embed = base_embed(f"🔒 Ticket #{ticket['id']} cerrado")
    embed.description = (
        f"**Usuario:** <@{ticket['user_id']}>\n"
        f"**Categoría:** {ticket.get('category_label', ticket.get('category', '—'))}\n"
        f"**Cerrado por:** {closed_by.mention}\n"
        f"**Motivo:** {clean_text(reason, 500) or 'Sin motivo indicado'}"
    )
    add_fields(
        embed,
        [
            ("📅 Creado", discord_timestamp(_parse(ticket.get('created_at'))), True),
            ("📅 Cerrado", "ahora", True),
        ],
    )
    return embed


def _parse(value: Any) -> Optional[datetime]:
    from utils.timeformat import from_sql

    return from_sql(value)


# ---------------------------------------------------------------------------
# Niveles y puntos
# ---------------------------------------------------------------------------
def level_up_embed(member: discord.Member, level: int, role: Optional[discord.Role], xp: int, cfg: dict[str, Any]) -> discord.Embed:
    embed = base_embed(f"🎉 ¡Subiste de nivel, {member.display_name}!")
    embed.description = (
        f"**Nuevo nivel:** {level}\n"
        f"**Rol obtenido:** {role.mention if role else '—'}\n"
        f"**XP acumulada:** {xp}"
    )
    embed.set_thumbnail(url=member.display_avatar.with_size(256).url)
    return embed


def level_card_embed(member: discord.Member, data: dict[str, Any], cfg: dict[str, Any]) -> discord.Embed:
    xp = int(data.get("xp", 0))
    level = int(data.get("level", 0))
    needed = xp_needed(level)
    bar = progress_bar(xp, needed)
    embed = base_embed(f"📊 Nivel de {member.display_name}")
    embed.set_thumbnail(url=member.display_avatar.with_size(256).url)
    embed.add_field(name="Nivel", value=f"**{level}**", inline=True)
    embed.add_field(name="XP total", value=f"**{xp}**", inline=True)
    embed.add_field(name="Mensajes", value=f"**{data.get('messages', 0)}**", inline=True)
    embed.add_field(name=f"Progreso al nivel {level + 1}", value=f"`{bar}` {xp}/{needed}", inline=False)
    return embed


def xp_needed(level: int) -> int:
    """XP necesaria para pasar de `level` a `level + 1`."""
    return 5 * (level ** 2) + 50 * level + 100


def points_top_embed(entries: Sequence[tuple[discord.abc.User, int]], cfg: dict[str, Any]) -> discord.Embed:
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for index, (user, points) in enumerate(entries):
        prefix = medals[index] if index < 3 else f"`#{index + 1}`"
        lines.append(f"{prefix} **{getattr(user, 'display_name', None) or getattr(user, 'name', user)}** — {points} pts")
    embed = base_embed("🏆 Top de puntos", "\n".join(lines) or "Todavía no hay puntos.")
    return embed


# ---------------------------------------------------------------------------
# Comunidad
# ---------------------------------------------------------------------------
def giveaway_embed(
    prize: str, winners: int, ends_at: Optional[datetime], participants: int, created_by: Any, ended: bool = False
) -> discord.Embed:
    title = "🎉 ¡Sorteo finalizado!" if ended else "🎉 ¡Sorteo!"
    embed = base_embed(title)
    embed.description = (
        f"**Premio:** {clean_text(prize, 200)}\n"
        f"**Ganadores:** {winners}\n"
        f"**Termina:** {discord_timestamp(ends_at, 'R') if ends_at else '—'}\n"
        f"**Participantes:** {participants}"
    )
    embed.add_field(name="Organizador", value=f"<@{created_by}>", inline=True)
    return embed


def poll_embed(
    question: str,
    options: Sequence[str],
    votes: Optional[Sequence[int]] = None,
    ends_at: Optional[datetime] = None,
    closed: bool = False,
) -> discord.Embed:
    title = "📊 Encuesta cerrada" if closed else "📊 Encuesta en curso"
    embed = base_embed(title, f"**{clean_text(question, 300)}**")
    votes = list(votes or [0] * len(options))
    total = sum(votes) or 1
    lines = []
    for index, option in enumerate(options):
        count = votes[index] if index < len(votes) else 0
        pct = round(count / total * 100)
        bar = progress_bar(count, total, 10)
        lines.append(f"**{index + 1}.** {option}\n`{bar}` {count} votos ({pct}%)")
    embed.description = f"**{clean_text(question, 300)}**\n\n" + "\n\n".join(lines)
    if ends_at:
        embed.set_footer(text=f"Cierre programado {discord_timestamp(ends_at, 'R')}")
    return embed


def suggestion_embed(data: dict[str, Any], author: Optional[discord.abc.User] = None) -> discord.Embed:
    status = str(data.get("status", "pendiente")).lower()
    emoji = {"pendiente": "🕓", "aprobada": "✅", "rechazada": "❌"}.get(status, "🕓")
    embed = base_embed(f"{emoji} Sugerencia #{data['id']} · {status.title()}")
    embed.description = clean_text(data.get("content", ""), 1500)
    embed.add_field(name="👍 A favor", value=str(data.get("ups", 0)), inline=True)
    embed.add_field(name="👎 En contra", value=str(data.get("downs", 0)), inline=True)
    if author is not None:
        embed.set_thumbnail(url=author.display_avatar.with_size(256).url)
        embed.add_field(name="Autor", value=author.mention, inline=True)
    return embed


def report_embed(data: dict[str, Any], reporter: Optional[discord.abc.User] = None) -> discord.Embed:
    embed = base_embed(f"📢 Reporte #{data['id']} · {str(data.get('status', 'pendiente')).title()}")
    embed.description = (
        f"**Usuario reportado:** <@{data['target_id']}> (`{data['target_id']}`)\n"
        f"**Motivo:** {clean_text(data.get('reason', ''), 700)}\n"
        f"**Evidencia:** {clean_text(data.get('evidence') or 'No adjunta', 400)}"
    )
    embed.add_field(name="Reportante", value=f"<@{data['reporter_id']}>", inline=True)
    embed.add_field(name="Fecha", value=discord_timestamp(_parse(data.get("created_at"))), inline=True)
    return embed


def application_embed(data: dict[str, Any], user: Optional[discord.abc.User] = None) -> discord.Embed:
    import json as _json

    try:
        answers = _json.loads(data.get("answers") or "{}")
    except (ValueError, TypeError):
        answers = {}
    lines = [f"**{key}:** {clean_text(str(value), 400)}" for key, value in answers.items()]
    embed = base_embed(
        f"🧾 Postulación #{data['id']} · {data.get('puesto', '—')}",
        "\n".join(lines) or "Sin respuestas.",
    )
    embed.add_field(name="Estado", value=str(data.get("status", "pendiente")).title(), inline=True)
    if user is not None:
        embed.set_thumbnail(url=user.display_avatar.with_size(256).url)
        embed.add_field(name="Postulante", value=user.mention, inline=True)
    embed.add_field(name="Fecha", value=discord_timestamp(_parse(data.get("created_at"))), inline=True)
    return embed


# ---------------------------------------------------------------------------
# Moderación
# ---------------------------------------------------------------------------
def moderation_log_embed(
    action: str,
    moderator: discord.abc.User,
    target: discord.abc.User,
    reason: str,
    duration: Optional[str] = None,
) -> discord.Embed:
    icons = {
        "ban": "🔨",
        "unban": "🔓",
        "kick": "👢",
        "timeout": "🔇",
        "unmute": "🔊",
        "warn": "⚠️",
        "tempban": "⏳",
        "automod": "🤖",
        "antiraid": "🛡️",
    }
    embed = base_embed(f"{icons.get(action, '⚖️')} {action.upper()}")
    embed.add_field(name="Usuario", value=f"{target.mention} (`{target.id}`)", inline=True)
    embed.add_field(name="Moderador", value=moderator.mention, inline=True)
    embed.add_field(name="Motivo", value=clean_text(reason, 500) or "Sin motivo", inline=False)
    if duration:
        embed.add_field(name="Duración", value=duration, inline=True)
    return embed


__all__ = [
    "welcome_embed",
    "leave_embed",
    "ticket_open_embed",
    "ticket_close_embed",
    "level_up_embed",
    "level_card_embed",
    "xp_needed",
    "points_top_embed",
    "giveaway_embed",
    "poll_embed",
    "suggestion_embed",
    "report_embed",
    "application_embed",
    "moderation_log_embed",
]
