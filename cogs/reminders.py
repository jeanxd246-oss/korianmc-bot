"""Recordatorios programados por usuario (sobreviven a los reinicios)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.timeformat import discord_timestamp, parse_duration, to_sql, utcnow
from utils.validators import clean_text

log = get_logger("recordatorios")

MAX_MESSAGE = 300


class Reminders(commands.Cog):
    """Creación, listado y cancelación de recordatorios."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    @commands.hybrid_command(name="remind", description="Crea un recordatorio (ej: /remind 2h revisar la tienda).")
    @commands.guild_only()
    async def remind(self, ctx: commands.Context, duracion: str, mensaje: str) -> None:
        seconds = parse_duration(duracion)
        if seconds is None or seconds < 60:
            await ctx.send(
                embed=error_embed(
                    "Duración no válida",
                    "Usa formatos como `30m`, `2h` o `1d` (mínimo 1 minuto).",
                )
            )
            return
        seconds = min(seconds, 365 * 86400)
        text = clean_text(mensaje, MAX_MESSAGE)
        if len(text) < 3:
            await ctx.send(embed=error_embed("Mensaje muy corto", "Escribe un recordatorio más claro."))
            return

        remind_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        cursor = await db.execute(
            "INSERT INTO reminders (guild_id, user_id, channel_id, message, remind_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(ctx.guild.id),
                str(ctx.author.id),
                str(ctx.channel.id),
                text,
                to_sql(remind_at),
            ),
        )
        await ctx.send(
            embed=success_embed(
                "⏰ Recordatorio creado",
                f"ID `#{cursor.lastrowid}` · te avisaré {discord_timestamp(remind_at, 'R')} "
                f"en este canal.\n**Mensaje:** {text}",
            )
        )

    @commands.hybrid_command(name="remind-list", description="Muestra tus recordatorios pendientes.")
    @commands.guild_only()
    async def remind_list(self, ctx: commands.Context) -> None:
        rows = await db.fetchall(
            "SELECT id, message, remind_at FROM reminders "
            "WHERE guild_id = ? AND user_id = ? AND delivered = 0 ORDER BY remind_at ASC LIMIT 20",
            (str(ctx.guild.id), str(ctx.author.id)),
        )
        if not rows:
            await ctx.send(embed=base_embed("⏰ Recordatorios", "No tienes recordatorios pendientes."))
            return
        lines = [
            f"**#{row['id']}** · {discord_timestamp(_parse(row['remind_at']), 'R')} · {clean_text(row['message'], 70)}"
            for row in rows
        ]
        await ctx.send(embed=base_embed("⏰ Tus recordatorios", "\n".join(lines)))

    @commands.hybrid_command(name="remind-cancel", description="Cancela un recordatorio pendiente.")
    @commands.guild_only()
    async def remind_cancel(self, ctx: commands.Context, id_recordatorio: int) -> None:
        row = await db.fetchone(
            "SELECT * FROM reminders WHERE id = ? AND guild_id = ? AND user_id = ? AND delivered = 0",
            (int(id_recordatorio), str(ctx.guild.id), str(ctx.author.id)),
        )
        if row is None:
            await ctx.send(
                embed=warn_embed(
                    "Recordatorio no encontrado",
                    f"No tienes ningún recordatorio `#{id_recordatorio}` pendiente.",
                )
            )
            return
        await db.execute("DELETE FROM reminders WHERE id = ?", (int(id_recordatorio),))
        await ctx.send(
            embed=success_embed("Recordatorio cancelado", f"Se eliminó `#{id_recordatorio}`.")
        )


def _parse(value) -> Optional[datetime]:
    from utils.timeformat import from_sql

    return from_sql(value)


async def process_due_reminders(bot: commands.Bot) -> int:
    """Entrega los recordatorios cuya fecha ya pasó."""
    rows = await db.fetchall(
        "SELECT * FROM reminders WHERE delivered = 0 AND remind_at <= ? ORDER BY remind_at ASC LIMIT 25",
        (utcnow(),),
    )
    delivered = 0
    for row in rows:
        try:
            guild = bot.get_guild(int(row["guild_id"]))
            user_id = int(row["user_id"])
            sent = False
            if guild is not None:
                channel = guild.get_channel(int(row["channel_id"] or 0))
                if isinstance(channel, discord.TextChannel):
                    try:
                        await channel.send(
                            content=f"<@{user_id}>",
                            embed=base_embed(
                                "⏰ Recordatorio",
                                f"**{row['message']}**\n"
                                f"Programado para {discord_timestamp(_parse(row['remind_at']))}",
                            ),
                            allowed_mentions=discord.AllowedMentions(users=True),
                        )
                        sent = True
                    except discord.HTTPException:
                        log.warning("No se pudo entregar el recordatorio #%s", row["id"])
            if not sent:
                try:
                    user = await bot.fetch_user(user_id)
                    await user.send(f"⏰ Recordatorio: **{row['message']}**")
                    sent = True
                except (discord.NotFound, discord.HTTPException, discord.Forbidden):
                    log.info("No se pudo entregar por DM el recordatorio #%s", row["id"])
            if sent:
                await db.execute("UPDATE reminders SET delivered = 1 WHERE id = ?", (row["id"],))
                delivered += 1
            else:
                # Si no se pudo entregar, se reintenta en el siguiente tick.
                break
        except Exception:
            log.exception("Error al entregar el recordatorio #%s", row["id"])
            break
    return delivered


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Reminders(bot))
