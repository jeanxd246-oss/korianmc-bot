"""Eventos globales del bot.

Aquí viven los listeners que no pertenecen a un módulo concreto: errores no
controlados, entrada/salida de servidores y auditoría de comandos.
"""

from __future__ import annotations

from typing import Any

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger

log = get_logger("eventos")


class Events(commands.Cog):
    """Listeners globales del bot."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_error(self, event_method: str, *args: Any, **kwargs: Any) -> None:
        """Último recurso: cualquier excepción no capturada va al log y a la BD."""
        log.error("Error no controlado en el evento %s", event_method, exc_info=True)
        try:
            await db.log_error(
                f"evento:{event_method}",
                "Excepción no controlada en un listener",
                repr(args[:2])[:4000],
            )
        except Exception:
            log.warning("No se pudo registrar el error del evento %s", event_method)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Nuevo servidor: %s (ID %s)", guild.name, guild.id)
        await db.log_event(guild.id, "servidor_unido", content=guild.name)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        log.info("Servidor abandonado: %s (ID %s)", guild.name, guild.id)

    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context) -> None:
        """Auditoría de todos los comandos ejecutados (prefijo y slash)."""
        if ctx.guild is None:
            return
        try:
            await db.log_event(
                ctx.guild.id,
                "comando",
                channel_id=getattr(ctx.channel, "id", None),
                user_id=ctx.author.id,
                content=f"{ctx.invoked_with} {ctx.message.content or ''}".strip()[:400],
            )
        except Exception:
            log.warning("No se pudo auditar el comando %s", ctx.invoked_with)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Events(bot))
