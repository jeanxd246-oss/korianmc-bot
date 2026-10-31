"""Estado AFK: ausencia declarada y respuesta automática al mencionar."""

from __future__ import annotations

from typing import Optional

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from utils.embeds import base_embed, success_embed
from utils.timeformat import discord_timestamp, from_sql
from utils.validators import clean_text

log = get_logger("afk")

MAX_REASON = 150


class AFK(commands.Cog):
    """Marca usuarios como ausentes y avisa a quien los menciona."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._notice_channels: set[tuple[int, int]] = set()

    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        # 1) El autor vuelve: se le quita el AFK
        row = await db.fetchone(
            "SELECT * FROM afk_users WHERE guild_id = ? AND user_id = ?",
            (str(message.guild.id), str(message.author.id)),
        )
        if row is not None:
            await db.execute(
                "DELETE FROM afk_users WHERE guild_id = ? AND user_id = ?",
                (str(message.guild.id), str(message.author.id)),
            )
            try:
                await message.channel.send(
                    embed=success_embed(
                        "👋 ¡Bienvenido de vuelta!",
                        f"{message.author.mention} ya no está AFK.",
                    ),
                    delete_after=8,
                )
            except discord.HTTPException:
                log.info("No se pudo avisar del regreso de %s", message.author)

        # 2) Menciona a alguien ausente
        if not message.mentions:
            return
        key = (message.guild.id, message.channel.id)
        if key in self._notice_channels:
            return
        for user in message.mentions:
            if user.bot:
                continue
            afk_row = await db.fetchone(
                "SELECT * FROM afk_users WHERE guild_id = ? AND user_id = ?",
                (str(message.guild.id), str(user.id)),
            )
            if afk_row is None:
                continue
            since = from_sql(afk_row.get("since"))
            try:
                self._notice_channels.add(key)
                await message.channel.send(
                    embed=base_embed(
                        "💤 Usuario ausente",
                        f"**{user.display_name}** está AFK "
                        f"desde {discord_timestamp(since, 'R')}.\n"
                        f"**Motivo:** {clean_text(str(afk_row.get('reason', 'AFK')), MAX_REASON)}",
                    ),
                    delete_after=10,
                )
            except discord.HTTPException:
                log.info("No se pudo avisar del AFK de %s", user)
            finally:
                self._notice_channels.discard(key)

    # ------------------------------------------------------------------
    @commands.hybrid_command(name="afk", description="Marca tu cuenta como ausente (AFK).")
    @commands.guild_only()
    async def afk(self, ctx: commands.Context, razon: Optional[str] = None) -> None:
        reason = clean_text(razon, MAX_REASON) or "AFK"
        await db.execute(
            "INSERT INTO afk_users (guild_id, user_id, reason, since) VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET reason = excluded.reason, "
            "since = datetime('now')",
            (str(ctx.guild.id), str(ctx.author.id), reason),
        )
        await ctx.send(
            embed=success_embed(
                "💤 Modo AFK activado",
                f"**{ctx.author.display_name}** está ausente: *{reason}*.\n"
                "Se te quitará automáticamente cuando vuelvas a escribir.",
            )
        )

    @commands.hybrid_command(name="afk-list", description="Lista los usuarios ausentes del servidor.")
    @commands.guild_only()
    async def afk_list(self, ctx: commands.Context) -> None:
        rows = await db.fetchall(
            "SELECT user_id, reason, since FROM afk_users WHERE guild_id = ? ORDER BY since DESC LIMIT 25",
            (str(ctx.guild.id),),
        )
        if not rows:
            await ctx.send(embed=base_embed("💤 AFK", "No hay usuarios ausentes ahora mismo."))
            return
        lines = [
            f"<@{row['user_id']}> · {clean_text(str(row['reason']), 60)} · desde {discord_timestamp(from_sql(row['since']), 'R')}"
            for row in rows
        ]
        await ctx.send(embed=base_embed("💤 Usuarios ausentes", "\n".join(lines)))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AFK(bot))
