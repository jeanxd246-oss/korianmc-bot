"""Ejecución de sorteos: cierre programado, ganadores aleatorios y reroll."""

from __future__ import annotations

import random
from typing import Any, Optional, Sequence

import discord

from core.database import db
from core.logging_cfg import get_logger
from utils.timeformat import from_sql, utcnow

log = get_logger("sorteos")


async def participants(giveaway_id: int) -> list[str]:
    rows = await db.fetchall(
        "SELECT user_id FROM giveaway_participants WHERE giveaway_id = ?",
        (giveaway_id,),
    )
    return [str(row["user_id"]) for row in rows]


def pick_winners(pool: Sequence[str], count: int) -> list[str]:
    """Selección aleatoria sin repetición y respetando el número de ganadores."""
    unique = list(dict.fromkeys(str(user_id) for user_id in pool))
    if not unique:
        return []
    count = max(1, min(count, len(unique)))
    return random.sample(unique, count)


async def _load(giveaway_id: int) -> Optional[dict[str, Any]]:
    return await db.fetchone("SELECT * FROM giveaways WHERE id = ?", (giveaway_id,))


async def _render(bot: discord.Client, giveaway: dict[str, Any], ended: bool) -> None:
    """Actualiza el mensaje del sorteo tras finalizar o al cambiar participantes."""
    from services.embed_factory import giveaway_embed

    guild = bot.get_guild(int(giveaway["guild_id"]))
    if guild is None:
        return
    channel = guild.get_channel(int(giveaway["channel_id"] or 0))
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        message = await channel.fetch_message(int(giveaway["message_id"]))
    except (discord.NotFound, discord.HTTPException, discord.Forbidden):
        return

    pool = await participants(int(giveaway["id"]))
    winner_ids = []
    try:
        import json as _json

        winner_ids = list(_json.loads(giveaway.get("winner_ids") or "[]"))
    except (ValueError, TypeError):
        winner_ids = []

    embed = giveaway_embed(
        str(giveaway["prize"]),
        int(giveaway["winners_count"]),
        from_sql(giveaway.get("ends_at")),
        len(pool),
        giveaway.get("created_by"),
        ended=ended,
    )
    if winner_ids:
        mentions = ", ".join(f"<@{user_id}>" for user_id in winner_ids)
        embed.add_field(name="🏆 Ganador(es)", value=mentions, inline=False)

    from cogs.sorteos import GiveawayView

    view = GiveawayView(int(giveaway["id"])) if not ended else None
    try:
        await message.edit(
            embed=embed,
            view=view,
            content=f"🎉 **¡{giveaway['prize']}!**" if ended else None,
        )
    except (discord.Forbidden, discord.HTTPException) as exc:
        log.warning("No se pudo actualizar el mensaje del sorteo #%s: %s", giveaway["id"], exc)


async def end_giveaway(bot: discord.Client, giveaway_id: int) -> Optional[list[str]]:
    """Finaliza un sorteo, elige ganadores y actualiza el mensaje."""
    import json as _json

    giveaway = await _load(giveaway_id)
    if giveaway is None or str(giveaway["status"]) != "activa":
        return None

    pool = await participants(giveaway_id)
    winners = pick_winners(pool, int(giveaway["winners_count"]))
    await db.execute(
        "UPDATE giveaways SET status = 'finalizada', ended_at = ?, winner_ids = ? WHERE id = ?",
        (utcnow(), _json.dumps(winners), giveaway_id),
    )
    giveaway = await _load(giveaway_id)
    if giveaway:
        await _render(bot, giveaway, ended=True)
        await db.log_event(
            giveaway["guild_id"],
            "giveaway_finalizado",
            channel_id=giveaway.get("channel_id"),
            content=f"Sorteo #{giveaway_id}: {giveaway.get('prize')} · {len(winners)} ganador(es)",
        )
        if winners:
            await _announce_winners(bot, giveaway, winners)
    return winners


async def _announce_winners(
    bot: discord.Client, giveaway: dict[str, Any], winners: Sequence[str]
) -> None:
    guild = bot.get_guild(int(giveaway["guild_id"]))
    if guild is None:
        return
    channel = guild.get_channel(int(giveaway["channel_id"] or 0))
    if not isinstance(channel, discord.TextChannel):
        return
    mentions = ", ".join(f"<@{user_id}>" for user_id in winners)
    await channel.send(
        content=f"🎊 ¡Felicidades {mentions}! Ganaste **{giveaway['prize']}**. "
        "Contacta con el staff para recibir tu premio.",
        allowed_mentions=discord.AllowedMentions(users=True),
    )


async def reroll(bot: discord.Client, giveaway_id: int) -> Optional[list[str]]:
    """Vuelve a sortear entre los participantes (para un sorteo ya finalizado)."""
    import json as _json

    giveaway = await _load(giveaway_id)
    if giveaway is None:
        return None
    pool = await participants(giveaway_id)
    previous = set(_json.loads(giveaway.get("winner_ids") or "[]"))
    fresh_pool = [user_id for user_id in pool if user_id not in previous] or pool
    winners = pick_winners(fresh_pool, int(giveaway["winners_count"]))
    await db.execute(
        "UPDATE giveaways SET winner_ids = ?, ended_at = ? WHERE id = ?",
        (_json.dumps(winners), utcnow(), giveaway_id),
    )
    giveaway = await _load(giveaway_id)
    if giveaway:
        await _render(bot, giveaway, ended=True)
        await _announce_winners(bot, giveaway, winners)
    return winners


async def process_due(bot: discord.Client) -> int:
    """Cierra los sorteos cuya fecha ya pasó (sobrevive reinicios)."""
    rows = await db.fetchall(
        "SELECT id FROM giveaways WHERE status = 'activa' AND ends_at IS NOT NULL AND ends_at <= ?",
        (utcnow(),),
    )
    closed = 0
    for row in rows:
        try:
            if await end_giveaway(bot, int(row["id"])) is not None:
                closed += 1
        except Exception:
            log.exception("Error al finalizar el sorteo #%s", row["id"])
    return closed


async def refresh(bot: discord.Client, giveaway_id: int) -> None:
    giveaway = await _load(giveaway_id)
    if giveaway:
        await _render(bot, giveaway, ended=str(giveaway["status"]) != "activa")


async def list_active(guild_id: Any) -> list[dict[str, Any]]:
    return await db.fetchall(
        "SELECT * FROM giveaways WHERE guild_id = ? AND status = 'activa' ORDER BY id DESC",
        (str(guild_id),),
    )


__all__ = [
    "participants",
    "pick_winners",
    "end_giveaway",
    "reroll",
    "process_due",
    "refresh",
    "list_active",
]
