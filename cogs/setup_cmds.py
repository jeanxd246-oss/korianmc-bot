"""Configuración del servidor (/setup), backups, información y ayuda."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import discord
from discord.ext import commands

from core.config import BACKUP_DIR, settings
from core.database import db
from core.logging_cfg import get_logger
from utils.checks import admin_only
from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.helpers import ConfirmView, Paginator
from utils.permissions import channel_id
from utils.validators import parse_color_value

log = get_logger("setup")

HELP_SECTIONS: list[tuple[str, str]] = [
    (
        "🎫 Soporte",
        "`/setup` · `/ticket create|close|transcript|claim` · `/report send|list|history` · "
        "`/postulacion` · `/faq` · `/help`",
    ),
    (
        "🛡️ Moderación",
        "`/ban` · `/tempban` · `/kick` · `/timeout` · `/unmute` · `/warn add|list|clear` · "
        "`/warn-historial` · `/automod` · `/raidmode` · `/logs`",
    ),
    (
        "🪙 Economía",
        "`/tienda` · `/buy` · `/product create|list|edit|set-price|delete|entregar` · "
        "`/points view|top|pay|add|remove` · `/referir` · `/pagos`",
    ),
    (
        "🎉 Comunidad",
        "`/suggest send|list` · `/poll create|end|list` · `/giveaway create|end|reroll|list|info` · "
        "`/level view|top` · `/afk` · `/remind`",
    ),
    (
        "🤖 IA",
        "`/ia chat|resumir|traducir|imagen|moderar|soporte` · `/iaconfig` · `/iamod`",
    ),
    (
        "⚙️ Administración",
        "`/setup all` · `/backup create|list|restore` · `/setinfo` · `/ip` · "
        "`/plantillas set|list` · `/serverinfo` · `/botinfo` · `/ping`",
    ),
]

DM_TEMPLATE_TYPES = (
    "welcome",
    "ticket_close",
    "level_up",
    "report_update",
    "reminder",
    "verification",
)


class Setup(commands.Cog):
    """Configuración guiada del servidor, backups, información y ayuda."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /setup
    # ------------------------------------------------------------------
    @commands.hybrid_group(name="setup", description="Configura todos los sistemas del bot.", invoke_without_command=True)
    @admin_only()
    async def setup(self, ctx: commands.Context) -> None:
        await ctx.send(
            embed=base_embed(
                "⚙️ Configuración",
                "Subcomandos disponibles:\n"
                "`tickets` · `welcome` · `levels` · `moderation` · `economy` · `suggestions` · "
                "`polls` · `giveaways` · `verification` · `reactionroles` · `roles` · `logs` · **`all`**\n\n"
                "Ejecuta `/setup all` para configurar todo de una vez.",
            )
        )

    # ------------------------------------------------------------------
    @setup.command(name="logs", description="Define el canal de logs.")
    @admin_only()
    async def setup_logs(self, ctx: commands.Context, canal: Optional[discord.TextChannel] = None) -> None:
        channel = canal or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if channel is None:
            await ctx.send(embed=error_embed("Canal no válido", "Especifica un canal de texto."))
            return
        await db.set_config(ctx.guild.id, "channels.logs", str(channel.id))
        await ctx.send(
            embed=success_embed("Logs configurados", f"Canal de logs: {channel.mention}.")
        )

    @setup.command(name="welcome", description="Define el canal de bienvenida y el auto-rol.")
    @admin_only()
    async def setup_welcome(
        self,
        ctx: commands.Context,
        canal: Optional[discord.TextChannel] = None,
        rol: Optional[discord.Role] = None,
    ) -> None:
        channel = canal or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if channel is None:
            await ctx.send(embed=error_embed("Canal no válido", "Especifica un canal de texto."))
            return
        updates: dict[str, Any] = {"channels.welcome": str(channel.id)}
        if rol is not None:
            updates["roles.autorole"] = str(rol.id)
        await db.set_configs(ctx.guild.id, updates)
        from services.embed_factory import welcome_embed

        await channel.send(embed=welcome_embed(ctx.author, await db.guild_config(ctx.guild.id)))
        await ctx.send(
            embed=success_embed(
                "Bienvenida configurada",
                f"Canal {channel.mention}"
                + (f" · auto-rol {rol.mention}" if rol else ""),
            )
        )

    @setup.command(name="logs-welcome", description="Atajo: configura logs y bienvenida juntos.")
    @admin_only()
    async def setup_logs_welcome(
        self, ctx: commands.Context, canal_logs: discord.TextChannel, canal_bienvenida: discord.TextChannel
    ) -> None:
        await db.set_configs(
            ctx.guild.id,
            {"channels.logs": str(canal_logs.id), "channels.welcome": str(canal_bienvenida.id)},
        )
        await ctx.send(
            embed=success_embed(
                "Canales guardados",
                f"Logs → {canal_logs.mention} · Bienvenida → {canal_bienvenida.mention}",
            )
        )

    @setup.command(name="tickets", description="Crea el panel de tickets en el canal indicado.")
    @admin_only()
    async def setup_tickets(
        self,
        ctx: commands.Context,
        canal: Optional[discord.TextChannel] = None,
        categoria: Optional[discord.CategoryChannel] = None,
    ) -> None:
        from cogs.tickets import TicketPanelView

        channel = canal or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if channel is None:
            await ctx.send(embed=error_embed("Canal no válido", "Especifica un canal de texto."))
            return
        updates: dict[str, Any] = {"channels.tickets": str(channel.id)}
        if categoria is not None:
            updates["categories.tickets"] = str(categoria.id)
        await db.set_configs(ctx.guild.id, updates)
        embed = base_embed(
            "🎫 Panel de soporte",
            "Selecciona la categoría de tu ticket. Se te pedirá la **razón** "
            "(obligatoria) antes de crearlo.",
        )
        await channel.send(embed=embed, view=TicketPanelView())
        await ctx.send(
            embed=success_embed(
                "Panel de tickets publicado",
                f"En {channel.mention}"
                + (f" dentro de la categoría **{categoria.name}**." if categoria else "."),
            )
        )

    @setup.command(name="levels", description="Activa los niveles y define su canal.")
    @admin_only()
    async def setup_levels(self, ctx: commands.Context, canal: Optional[discord.TextChannel] = None) -> None:
        channel = canal or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if channel is None:
            await ctx.send(embed=error_embed("Canal no válido", "Especifica un canal de texto."))
            return
        await db.set_configs(
            ctx.guild.id, {"channels.levels": str(channel.id), "levels.enabled": True}
        )
        await ctx.send(
            embed=success_embed(
                "Niveles activados",
                f"Las subidas de nivel se anunciarán en {channel.mention}. "
                "Crea los roles con `/setup roles`.",
            )
        )

    @setup.command(name="roles", description="Crea los 10 roles de nivel automáticamente.")
    @admin_only()
    async def setup_roles(self, ctx: commands.Context) -> None:
        from cogs.levels import LEVEL_ROLE_COLORS, LEVEL_ROLE_NAMES, MAX_LEVEL

        created: list[str] = []
        existing: list[str] = []
        mapping: dict[str, Any] = {}
        role_ids_list: list[str] = []

        for index, name in enumerate(LEVEL_ROLE_NAMES):
            role = discord.utils.get(ctx.guild.roles, name=name)
            if role is None:
                try:
                    role = await ctx.guild.create_role(
                        name=name,
                        colour=discord.Colour(LEVEL_ROLE_COLORS[index % len(LEVEL_ROLE_COLORS)]),
                        reason="KorianMC /setup roles",
                    )
                    created.append(name)
                except discord.HTTPException as exc:
                    await ctx.send(
                        embed=error_embed(
                            "No se pudieron crear todos los roles",
                            f"Error al crear **{name}**: {exc}",
                        )
                    )
                    return
            else:
                existing.append(name)
            role_ids_list.append(str(role.id))
            mapping[str(index + 1)] = str(role.id)
            await db.execute(
                "INSERT INTO level_roles (guild_id, level, role_id) VALUES (?, ?, ?) "
                "ON CONFLICT (guild_id, level) DO UPDATE SET role_id = excluded.role_id",
                (str(ctx.guild.id), index + 1, str(role.id)),
            )

        await db.set_configs(
            ctx.guild.id, {"roles.level": role_ids_list}
        )
        lines = [
            f"**Creados:** {', '.join(created) if created else 'ninguno (ya existían)'}",
            f"**Reutilizados:** {', '.join(existing) if existing else 'ninguno'}",
            f"**Niveles:** 1 → {MAX_LEVEL} (nivel máximo: Level Demon)",
            "",
            "Asegúrate de que mi rol esté **por encima** de todos ellos para poder asignarlos.",
        ]
        await ctx.send(embed=success_embed("Roles de nivel configurados", "\n".join(lines)))

    @setup.command(name="moderation", description="Configura canales de moderación y reportes.")
    @admin_only()
    async def setup_moderation(
        self,
        ctx: commands.Context,
        canal_logs: Optional[discord.TextChannel] = None,
        canal_reportes: Optional[discord.TextChannel] = None,
        anti_spam: bool = True,
        anti_invitaciones: bool = True,
        anti_enlaces: bool = False,
        anti_mayusculas: bool = True,
    ) -> None:
        updates: dict[str, Any] = {
            "automod.anti_spam": anti_spam,
            "automod.anti_invites": anti_invitaciones,
            "automod.anti_links": anti_enlaces,
            "automod.anti_caps": anti_mayusculas,
            "automod.enabled": True,
        }
        if canal_logs is not None:
            updates["channels.moderation"] = str(canal_logs.id)
        if canal_reportes is not None:
            updates["channels.reports"] = str(canal_reportes.id)
        await db.set_configs(ctx.guild.id, updates)
        cfg = await db.guild_config(ctx.guild.id)
        reports_channel = channel_id(cfg, "reports")
        await ctx.send(
            embed=success_embed(
                "Moderación configurada",
                f"**Anti-spam:** {'sí' if anti_spam else 'no'} · "
                f"**Anti-invitaciones:** {'sí' if anti_invitaciones else 'no'} · "
                f"**Anti-enlaces:** {'sí' if anti_enlaces else 'no'} · "
                f"**Anti-mayúsculas:** {'sí' if anti_mayusculas else 'no'}\n"
                f"**Canal de reportes:** {f'<#{reports_channel}>' if reports_channel else 'sin definir'}\n"
                "Revisa todo con `/automod-estado`.",
            )
        )

    @setup.command(name="economy", description="Activa los puntos y define su canal de premios.")
    @admin_only()
    async def setup_economy(self, ctx: commands.Context, canal: Optional[discord.TextChannel] = None) -> None:
        updates: dict[str, Any] = {"points.enabled": True}
        channel = canal or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if channel is not None:
            updates["channels.points"] = str(channel.id)
        await db.set_configs(ctx.guild.id, updates)
        await ctx.send(
            embed=success_embed(
                "Economía activada",
                "Los usuarios ganan puntos por actividad. Gestiona productos con `/product create`.",
            )
        )

    @setup.command(name="suggestions", description="Define el canal de sugerencias.")
    @admin_only()
    async def setup_suggestions(
        self, ctx: commands.Context, canal: Optional[discord.TextChannel] = None
    ) -> None:
        channel = canal or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if channel is None:
            await ctx.send(embed=error_embed("Canal no válido", "Especifica un canal de texto."))
            return
        await db.set_config(ctx.guild.id, "channels.suggestions", str(channel.id))
        await ctx.send(
            embed=success_embed("Sugerencias configuradas", f"Panel en {channel.mention}.")
        )

    @setup.command(name="polls", description="Define el canal de encuestas.")
    @admin_only()
    async def setup_polls(self, ctx: commands.Context, canal: Optional[discord.TextChannel] = None) -> None:
        channel = canal or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if channel is None:
            await ctx.send(embed=error_embed("Canal no válido", "Especifica un canal de texto."))
            return
        await db.set_config(ctx.guild.id, "channels.polls", str(channel.id))
        await ctx.send(embed=success_embed("Encuestas configuradas", f"Canal {channel.mention}."))

    @setup.command(name="giveaways", description="Define el canal de sorteos.")
    @admin_only()
    async def setup_giveaways(
        self, ctx: commands.Context, canal: Optional[discord.TextChannel] = None
    ) -> None:
        channel = canal or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if channel is None:
            await ctx.send(embed=error_embed("Canal no válido", "Especifica un canal de texto."))
            return
        await db.set_config(ctx.guild.id, "channels.giveaways", str(channel.id))
        await ctx.send(embed=success_embed("Sorteos configurados", f"Canal {channel.mention}."))

    @setup.command(name="verification", description="Configura la verificación anti-raid.")
    @admin_only()
    async def setup_verification(
        self,
        ctx: commands.Context,
        canal: Optional[discord.TextChannel] = None,
        rol: Optional[discord.Role] = None,
    ) -> None:
        from cogs.antiraid import VerifyView

        channel = canal or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if channel is None:
            await ctx.send(embed=error_embed("Canal no válido", "Especifica un canal de texto."))
            return
        updates: dict[str, Any] = {"channels.verification": str(channel.id)}
        if rol is not None:
            updates["roles.verification"] = str(rol.id)
        await db.set_configs(ctx.guild.id, updates)
        embed = base_embed(
            "✅ Verificación",
            "Pulsa el botón para verificar tu cuenta y poder participar en el servidor.",
        )
        await channel.send(embed=embed, view=VerifyView())
        await ctx.send(
            embed=success_embed(
                "Verificación configurada",
                f"Panel en {channel.mention}"
                + (f" · rol {rol.mention}" if rol else " (define el rol con `rol:`)"),
            )
        )

    @setup.command(name="reactionroles", description="Ayuda y atajo de reaction roles.")
    @admin_only()
    async def setup_reactionroles(
        self,
        ctx: commands.Context,
        rol: Optional[discord.Role] = None,
        emoji: Optional[str] = None,
        canal: Optional[discord.TextChannel] = None,
    ) -> None:
        if rol is not None and emoji:
            cog = self.bot.get_cog("ReactionRoles")
            if cog is not None:
                await cog.reactionroles_create(ctx, rol, emoji, canal, None)
                return
        await ctx.send(
            embed=base_embed(
                "🎭 Reaction roles",
                "Crea uno con `/reactionroles crear rol:@Rol emoji:🎉 canal:#canal`.\n"
                "Consulta los existentes con `/reactionroles` y elimínalos con "
                "`/reactionroles eliminar id_mensaje emoji`.",
            )
        )

    @setup.command(name="all", description="Configura todo el bot de una vez.")
    @admin_only()
    async def setup_all(
        self,
        ctx: commands.Context,
        canal: Optional[discord.TextChannel] = None,
        bienvenida: Optional[discord.TextChannel] = None,
        categoria_tickets: Optional[discord.CategoryChannel] = None,
    ) -> None:
        channel = canal or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        welcome_channel = bienvenida or channel
        if channel is None:
            await ctx.send(embed=error_embed("Canal no válido", "Especifica un canal de texto."))
            return

        updates: dict[str, Any] = {
            "channels.logs": str(channel.id),
            "channels.welcome": str(welcome_channel.id) if welcome_channel else "",
            "channels.tickets": str(channel.id),
            "channels.ticket_logs": str(channel.id),
            "channels.levels": str(channel.id),
            "channels.points": str(channel.id),
            "channels.suggestions": str(channel.id),
            "channels.polls": str(channel.id),
            "channels.giveaways": str(channel.id),
            "channels.reports": str(channel.id),
            "channels.applications": str(channel.id),
            "channels.moderation": str(channel.id),
            "channels.verification": str(channel.id),
            "channels.ai_support": str(channel.id),
            "levels.enabled": True,
            "points.enabled": True,
            "automod.enabled": True,
            "ai.enabled": True,
        }
        from cogs.tickets import TicketPanelView

        panel_channel = channel
        if categoria_tickets is not None:
            updates["categories.tickets"] = str(categoria_tickets.id)
        await db.set_configs(ctx.guild.id, updates)

        results: list[str] = []
        panels = 0
        try:
            from cogs.antiraid import VerifyView

            if categoria_tickets is not None:
                panel_channel = await categoria_tickets.create_text_channel(
                    name="🎫-tickets", reason="KorianMC /setup all"
                )
            await panel_channel.send(
                embed=base_embed(
                    "🎫 Panel de soporte",
                    "Selecciona la categoría de tu ticket. La **razón** es obligatoria.",
                ),
                view=TicketPanelView(),
            )
            panels += 1
            await channel.send(
                embed=base_embed(
                    "✅ Verificación",
                    "Pulsa el botón para verificar tu cuenta y participar en el servidor.",
                ),
                view=VerifyView(),
            )
            panels += 1
            results.append("✔ Paneles publicados")
        except discord.HTTPException as exc:
            log.warning("No se pudieron publicar todos los paneles: %s", exc)
            results.append(f"⚠ Paneles ({panels} publicados)")

        await self.setup_roles(ctx)

        results = [
            "✔ Logs",
            "✔ Bienvenida",
            "✔ Tickets",
            "✔ Niveles",
            "✔ Moderación",
            "✔ Economía",
            "✔ Sugerencias",
            "✔ Encuestas",
            "✔ Sorteos",
            "✔ Verificación",
            "✔ Roles de nivel",
            "✔ IA (KorianMC)",
        ] + results

        embed = success_embed(
            "⚙️ Configuración completa",
            "\n".join(results)
            + "\n\n**Siguientes pasos:**\n"
            "1. Revisa los canales y el rol de **KorianMC Bot** (debe estar arriba de los roles de nivel).\n"
            "2. Añade productos con `/product create`.\n"
            "3. Configura `GEMINI_API_KEY` en `.env` para activar la IA.",
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Plantillas de DM
    # ------------------------------------------------------------------
    @commands.hybrid_group(name="plantillas", description="Plantillas de mensajes directos.", invoke_without_command=True)
    @admin_only()
    async def plantillas(self, ctx: commands.Context) -> None:
        cfg = await db.guild_config(ctx.guild.id)
        templates = cfg.get("dm_templates") or {}
        lines = [f"**{key}** → {str(value)[:120]}" for key, value in templates.items()]
        await ctx.send(
            embed=base_embed(
                "✉️ Plantillas de DM",
                "\n".join(lines)
                + f"\n\nTipos disponibles: {', '.join(DM_TEMPLATE_TYPES)}\n"
                "Edita con `/plantillas set tipo:<tipo> texto:<texto>`.",
            )
        )

    @plantillas.command(name="set", description="Edita una plantilla de mensaje directo.")
    @admin_only()
    async def plantillas_set(self, ctx: commands.Context, tipo: str, texto: str) -> None:
        key = tipo.strip().lower()
        if key not in DM_TEMPLATE_TYPES:
            await ctx.send(
                embed=error_embed(
                    "Tipo no válido",
                    f"Tipos disponibles: {', '.join(DM_TEMPLATE_TYPES)}.",
                )
            )
            return
        value = str(texto).strip()[:900]
        if not value:
            await ctx.send(embed=error_embed("Texto vacío", "La plantilla no puede estar vacía."))
            return
        await db.set_config(ctx.guild.id, f"dm_templates.{key}", value)
        await ctx.send(
            embed=success_embed("Plantilla guardada", f"**{key}** actualizada correctamente.")
        )

    # ------------------------------------------------------------------
    # Backups
    # ------------------------------------------------------------------
    @commands.hybrid_group(name="backup", description="Copias de seguridad de la base de datos.", invoke_without_command=True)
    @admin_only()
    async def backup(self, ctx: commands.Context) -> None:
        await ctx.send(
            embed=base_embed(
                "💾 Backups",
                "`/backup create` · `/backup list` · `/backup restore id:<n>`\n"
                f"Copia automática cada **{settings.backup_interval_hours}** horas.",
            )
        )

    @backup.command(name="create", description="Crea una copia de seguridad manual.")
    @admin_only()
    async def backup_create(self, ctx: commands.Context) -> None:
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        dest = BACKUP_DIR / f"manual_{stamp}.db"
        try:
            info = await db.backup_to(dest, kind="manual")
        except OSError as exc:
            await ctx.send(embed=error_embed("No se pudo crear la copia", str(exc)))
            return
        size_kb = max(1, int(info.get("size_bytes", 0)) // 1024)
        await ctx.send(
            embed=success_embed(
                "💾 Copia creada",
                f"`{Path(info['path']).name}` · {size_kb} KB · usa `/backup list` para verlas.",
            )
        )

    @backup.command(name="list", description="Lista las copias de seguridad disponibles.")
    @admin_only()
    async def backup_list(self, ctx: commands.Context) -> None:
        rows = await db.list_backups()
        if not rows:
            await ctx.send(
                embed=base_embed("💾 Backups", "Todavía no hay copias. Crea una con `/backup create`.")
            )
            return
        lines = [
            f"`#{row['id']}` {Path(row['path']).name} · {max(1, int(row['size_bytes']) // 1024)} KB · "
            f"{row['kind']} · {row['created_at']}"
            for row in rows[:15]
        ]
        await ctx.send(embed=base_embed("💾 Copias de seguridad", "\n".join(lines)))

    @backup.command(name="restore", description="Restaura una copia de seguridad (con confirmación).")
    @admin_only()
    async def backup_restore(self, ctx: commands.Context, id_copia: int) -> None:
        rows = await db.list_backups()
        row = next((item for item in rows if int(item["id"]) == int(id_copia)), None)
        if row is None:
            await ctx.send(embed=error_embed("Copia no encontrada", f"No existe la copia `#{id_copia}`."))
            return
        source = Path(row["path"])
        if not source.exists():
            await ctx.send(
                embed=error_embed(
                    "Archivo perdido",
                    f"La copia `#{id_copia}` ya no está en disco (`{source.name}`).",
                )
            )
            return

        async def _confirm(interaction: discord.Interaction) -> None:
            try:
                await db.restore_from(source)
            except Exception as exc:
                log.exception("Error al restaurar la copia #%s", id_copia)
                await interaction.response.edit_message(
                    embed=error_embed("No se pudo restaurar", str(exc)), view=None
                )
                return
            await interaction.response.edit_message(
                embed=success_embed(
                    "💾 Copia restaurada",
                    f"La base de datos volvió al estado de la copia `#{id_copia}`. "
                    "El bot ya está usando los datos restaurados.",
                ),
                view=None,
            )

        view = ConfirmView(
            user_id=ctx.author.id,
            on_confirm=_confirm,
            confirm_label="Restaurar",
            cancel_label="Cancelar",
        )
        message = await ctx.send(
            embed=warn_embed(
                "⚠️ Restaurar copia",
                f"Se **sobrescribirá** la base de datos actual con `{source.name}` "
                f"(creada el {row['created_at']}). Esta acción no se puede deshacer.",
            ),
            view=view,
        )
        view.message = message

    # ------------------------------------------------------------------
    # Información y ayuda
    # ------------------------------------------------------------------
    @commands.hybrid_command(name="ip", description="Muestra la IP del servidor de juego.")
    @commands.guild_only()
    async def ip(self, ctx: commands.Context, nueva_ip: Optional[str] = None) -> None:
        cfg = await db.guild_config(ctx.guild.id)
        info = cfg.get("info") or {}
        if nueva_ip is not None:
            from utils.checks import admin_check

            try:
                await admin_check(ctx)
            except Exception:
                await ctx.send(embed=error_embed("Permisos", "Solo el staff puede cambiar la IP."))
                return
            await db.set_config(ctx.guild.id, "info.ip", nueva_ip.strip()[:80])
            await ctx.send(
                embed=success_embed("IP actualizada", f"Ahora se muestra `{nueva_ip.strip()[:80]}`.")
            )
            return
        current = info.get("ip") or "sin configurar (usa `/ip nueva_ip:mi.servidor.net`)"
        embed = base_embed("🌐 IP del servidor", f"`{current}`")
        if info.get("website"):
            embed.add_field(name="Web", value=str(info["website"]), inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="setinfo", description="[Admin] Configura la información pública del servidor.")
    @admin_only()
    async def setinfo(
        self,
        ctx: commands.Context,
        ip: Optional[str] = None,
        web: Optional[str] = None,
        descripcion: Optional[str] = None,
        color: Optional[str] = None,
    ) -> None:
        updates: dict[str, Any] = {}
        if ip is not None:
            updates["info.ip"] = ip.strip()[:80]
        if web is not None:
            updates["info.website"] = web.strip()[:120]
        if descripcion is not None:
            updates["info.description"] = descripcion.strip()[:500]
        if color is not None:
            try:
                value = parse_color_value(color)
            except Exception as exc:
                await ctx.send(embed=error_embed("Color no válido", str(exc)))
                return
            updates["appearance.color"] = f"#{value:06X}"
        if not updates:
            await ctx.send(
                embed=warn_embed(
                    "Nada que cambiar",
                    "Indica al menos uno: `ip:`, `web:`, `descripcion:` o `color:`.",
                )
            )
            return
        await db.set_configs(ctx.guild.id, updates)
        await ctx.send(
            embed=success_embed(
                "Información actualizada",
                "\n".join(f"• `{key}` = `{value}`" for key, value in updates.items()),
            )
        )

    @commands.hybrid_command(name="ping", description="Latencia del bot.")
    async def ping(self, ctx: commands.Context) -> None:
        latency = round(self.bot.latency * 1000)
        emoji = "🟢" if latency < 150 else "🟡" if latency < 350 else "🔴"
        await ctx.send(
            embed=base_embed(
                "🏓 Pong",
                f"{emoji} **{latency} ms** de latencia.\n"
                f"Base de datos: ✅ · IA: {'✅' if _ai_available() else '❌'}",
            )
        )

    @commands.hybrid_command(name="botinfo", description="Información sobre el bot.")
    async def botinfo(self, ctx: commands.Context) -> None:
        embed = base_embed(
            f"🤖 {settings.bot_name}",
            settings.bot_description,
        )
        embed.add_field(name="Prefijos", value=" ".join(settings.prefixes), inline=True)
        embed.add_field(name="Servidores", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(name="Comandos", value=str(len(self.bot.tree.get_commands())), inline=True)
        embed.add_field(name="Base de datos", value=f"SQLite · `{db.path.name}`", inline=True)
        embed.add_field(
            name="IA",
            value="Gemini disponible" if _ai_available() else "Sin GEMINI_API_KEY",
            inline=True,
        )
        embed.add_field(name="Versión", value="1.0.0", inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="serverinfo", description="Información del servidor.")
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        embed = base_embed(f"ℹ️ {guild.name}", str(guild.description or "Sin descripción."))
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.with_size(256).url)
        embed.add_field(name="Propietario", value=f"<@{guild.owner_id}>", inline=True)
        embed.add_field(name="Miembros", value=str(guild.member_count), inline=True)
        embed.add_field(name="Canales", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        embed.add_field(
            name="Creado",
            value=f"<t:{int(guild.created_at.timestamp())}:D>",
            inline=True,
        )
        embed.add_field(
            name="Nivel de verificación",
            value=str(guild.verification_level.name).title(),
            inline=True,
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="help", description="Lista todos los comandos disponibles.")
    async def help_command(self, ctx: commands.Context, seccion: Optional[str] = None) -> None:
        if seccion:
            match = next(
                (item for item in HELP_SECTIONS if seccion.lower() in item[0].lower()), None
            )
            if match:
                await ctx.send(embed=base_embed(match[0], match[1]))
                return
        embeds = [base_embed(title, body) for title, body in HELP_SECTIONS]
        for page, embed in enumerate(embeds, start=1):
            embed.set_footer(
                text=f"Página {page}/{len(embeds)} · Prefijos: {' '.join(settings.prefixes)}"
            )
        await Paginator(embeds, user_id=ctx.author.id).start(ctx)


def _ai_available() -> bool:
    from services.ai_client import ai

    return ai.available


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Setup(bot))
