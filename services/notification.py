"""Notificaciones del bot: DMs con plantillas y canal de logs."""

from __future__ import annotations

from typing import Any, Optional

import discord

from core.database import db
from core.logging_cfg import get_logger
from utils.embeds import base_embed
from utils.permissions import channel_id, role_ids

log = get_logger("notificaciones")


class _SafeDict(dict):
    """`format_map` tolerante: una clave inexistente se queda tal cual."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_template(cfg: dict[str, Any], key: str, fallback: str = "", **fmt: Any) -> str:
    templates = cfg.get("dm_templates") or {}
    template = templates.get(key) or fallback or "Mensaje de {server}."
    try:
        return str(template).format_map(_SafeDict(**fmt))
    except (IndexError, KeyError, ValueError):
        return str(template)


async def send_template_dm(
    bot: discord.Client,
    guild: discord.Guild,
    user_id: int,
    key: str,
    fallback: str = "",
    **fmt: Any,
) -> bool:
    """Envía una DM con la plantilla configurada. Devuelve True si se envió."""
    cfg = await db.guild_config(guild.id)
    fmt.setdefault("server", guild.name)
    fmt.setdefault("user", f"<@{user_id}>")
    text = render_template(cfg, key, fallback, **fmt)
    try:
        user = guild.get_member(user_id) or await guild.fetch_member(user_id)
    except (discord.NotFound, discord.HTTPException, discord.Forbidden):
        user = None
    target = user or (await bot.fetch_user(user_id) if bot else None)
    if target is None:
        return False
    try:
        await target.send(text[:1900])
        return True
    except (discord.Forbidden, discord.HTTPException) as exc:
        log.info("No se pudo enviar DM a %s: %s", user_id, exc)
        return False


async def get_log_channel(guild: discord.Guild, cfg: Optional[dict[str, Any]] = None) -> Optional[discord.abc.Messageable]:
    cfg = cfg or await db.guild_config(guild.id)
    log_id = channel_id(cfg, "logs")
    if not log_id:
        return None
    channel = guild.get_channel(log_id)
    return channel


async def log_action(
    guild: discord.Guild,
    event: str,
    *,
    cfg: Optional[dict[str, Any]] = None,
    embed: Optional[discord.Embed] = None,
    content: Optional[str] = None,
    channel_key: str = "logs",
    user_id: Any = None,
    target_id: Any = None,
    channel_target: Any = None,
) -> None:
    """Registra una acción en la BD y, si existe, en el canal de logs."""
    cfg = cfg or await db.guild_config(guild.id)
    try:
        await db.log_event(
            guild.id,
            event,
            channel_id=channel_target,
            user_id=user_id,
            target_id=target_id,
            content=(content or (embed.description if embed else "") or event)[:1800],
        )
    except Exception:
        log.warning("No se pudo guardar el evento %s en la BD", event)

    destination_id = channel_id(cfg, channel_key) or channel_id(cfg, "logs")
    if not destination_id:
        return
    channel = guild.get_channel(destination_id)
    if channel is None:
        return
    try:
        await channel.send(content=content, embed=embed)
    except (discord.Forbidden, discord.HTTPException) as exc:
        log.warning("No se pudo publicar %s en el canal de logs: %s", event, exc)


async def notify_staff(
    guild: discord.Guild,
    cfg: dict[str, Any],
    embed: discord.Embed,
    *,
    channel_key: str = "logs",
) -> None:
    """Avisa al equipo staff mencionando sus roles en el canal indicado."""
    mentions = " ".join(f"<@&{role_id}>" for role_id in role_ids(cfg, "staff"))
    await log_action(
        guild,
        "aviso_staff",
        cfg=cfg,
        embed=embed,
        content=mentions or None,
        channel_key=channel_key,
    )


async def send_welcome_dm(bot: discord.Client, guild: discord.Guild, user_id: int) -> bool:
    return await send_template_dm(bot, guild, user_id, "welcome", fallback="¡Bienvenido/a a {server}!")


def startup_embed(bot: discord.Client) -> discord.Embed:
    embed = base_embed("🟢 Bot en línea")
    embed.add_field(name="Prefijos", value=" ".join(bot.command_prefix) if isinstance(bot.command_prefix, list) else str(bot.command_prefix), inline=True)
    embed.add_field(name="Servidores", value=str(len(bot.guilds)), inline=True)
    return embed


__all__ = [
    "render_template",
    "send_template_dm",
    "log_action",
    "notify_staff",
    "send_welcome_dm",
    "get_log_channel",
    "startup_embed",
]
