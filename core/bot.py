"""Clase principal de KorianMC Bot.

Se ocupa de: intents, carga de extensiones, sincronización de slash commands,
arranque del planificador y registro de vistas persistentes.
"""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from core.config import settings
from core.database import db
from core.errors import handle_command_error, handle_tree_error
from core.logging_cfg import get_logger

log = get_logger("bot")

COG_EXTENSIONS: list[str] = [
    "core.events",
    "cogs.welcome",
    "cogs.tickets",
    "cogs.levels",
    "cogs.points",
    "cogs.shop",
    "cogs.reportes",
    "cogs.postulaciones",
    "cogs.sugerencias",
    "cogs.encuestas",
    "cogs.sorteos",
    "cogs.reaction_roles",
    "cogs.afk",
    "cogs.reminders",
    "cogs.moderation",
    "cogs.automod",
    "cogs.antiraid",
    "cogs.logs_sys",
    "cogs.ia_korianmc",
    "cogs.ia_moderacion",
    "cogs.referidos",
    "cogs.faq",
    "cogs.setup_cmds",
]


class KorianCommandTree(app_commands.CommandTree):
    """Árbol de comandos con manejo de errores global en español."""

    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        await handle_tree_error(interaction, error)


async def _view_on_error(
    self: discord.ui.View,
    interaction: discord.Interaction,
    error: Exception,
    item: Any,
    /,
) -> None:
    """Error único para cualquier vista del bot: log técnico + aviso al usuario."""
    log.error("Error en la vista %s", type(self).__name__, exc_info=error)
    from core.errors import handle_error

    await handle_error(interaction, error, f"vista {type(self).__name__}")


# Toda vista del proyecto comparte el mismo manejo de errores (sin tracebacks
# al usuario final): el error va al log y a la tabla error_logs.
discord.ui.View.on_error = _view_on_error  # type: ignore[method-assign]


class KorianMCBot(commands.Bot):
    """Bot híbrido (slash + prefijo) con base SQLite y módulo de IA."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.reactions = True
        intents.bans = True
        intents.moderation = True
        intents.emojis_and_stickers = True
        intents.invites = True

        super().__init__(
            command_prefix=settings.prefixes,
            description=settings.bot_description,
            intents=intents,
            tree_cls=KorianCommandTree,
            help_command=None,
            case_insensitive=True,
            allowed_mentions=discord.AllowedMentions(
                users=True, roles=False, everyone=False, replied_user=False
            ),
        )
        self.views_registered = False
        self.scheduler: Any = None
        self.startup_complete = False

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    async def setup_hook(self) -> None:
        await db.connect()

        for extension in COG_EXTENSIONS:
            try:
                await self.load_extension(extension)
            except Exception:
                log.exception("No se pudo cargar la extensión %s", extension)

        await self._sync_commands()

        try:
            from services.scheduler import Scheduler

            self.scheduler = Scheduler(self)
            self.scheduler.start()
        except Exception:
            log.exception("No se pudo iniciar el planificador de tareas.")

    async def _sync_commands(self) -> None:
        try:
            synced = await self.tree.sync()
            log.info("%s comandos slash sincronizados.", len(synced))
        except Exception:
            log.exception("Error al sincronizar los comandos slash.")

    async def close(self) -> None:
        if self.scheduler is not None:
            try:
                self.scheduler.stop()
            except Exception:
                log.exception("No se pudo detener el planificador.")
        try:
            await db.close()
        except Exception:
            log.exception("No se pudo cerrar la base de datos.")
        await super().close()

    # ------------------------------------------------------------------
    # Vistas persistentes
    # ------------------------------------------------------------------
    async def register_persistent_views(self) -> None:
        """Registra todas las vistas persistentes (se llama en cada on_ready)."""
        for cog in list(self.cogs.values()):
            registrar = getattr(cog, "register_views", None)
            if registrar is None:
                continue
            try:
                await registrar()
            except Exception:
                log.exception("Error al registrar vistas del cog %s", type(cog).__name__)

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------
    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        await handle_command_error(ctx, error)

    async def on_ready(self) -> None:
        if self.user is None:
            return
        log.info(
            "Conectado como %s (ID %s) en %s servidor(es).",
            self.user,
            self.user.id,
            len(self.guilds),
        )
        await self.register_persistent_views()
        if not self.startup_complete:
            self.startup_complete = True
            await self._announce_startup()

    async def _announce_startup(self) -> None:
        from core.database import db as database
        from utils.embeds import base_embed

        for guild in self.guilds:
            try:
                cfg = await database.guild_config(guild.id)
                channel_id = (cfg.get("channels") or {}).get("logs")
                if not channel_id:
                    continue
                channel = guild.get_channel(int(channel_id))
                if channel is None:
                    continue
                embed = base_embed(
                    "🟢 Bot en línea",
                    f"**{settings.bot_name}** está operativo.\n"
                    f"Prefijos: {' '.join(settings.prefixes)} · Slash: `/`",
                )
                await channel.send(embed=embed)
            except Exception:
                log.warning("No se pudo anunciar el arranque en %s", guild.name)
        await super().on_ready()


__all__ = ["KorianMCBot", "KorianCommandTree", "COG_EXTENSIONS"]
