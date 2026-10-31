"""Sistema de tickets completo.

Reglas obligatorias que implementa este módulo:
- Panel con select de categorías (7 categorías).
- Modal de RAZÓN OBLIGATORIO: sin razón no se crea el ticket.
- Canal privado ticket-usuario con acceso solo para creador y staff.
- Embed inicial con usuario, categoría, razón y fecha.
- Botones: cerrar, avisar staff, transcripción y eliminar.
- Al cerrar: guardar quién/fecha/motivo → transcripción .html → logs → DM →
  ELIMINAR el canal automáticamente, con confirmación por botón antes.
- Protecciones: 1 ticket por usuario, cooldown y permisos de staff.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from services import transcript as transcript_service
from services.embed_factory import ticket_close_embed, ticket_open_embed
from services.notification import log_action
from utils.checks import staff_only
from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.helpers import ConfirmView
from utils.permissions import channel_id, role_ids
from utils.timeformat import from_sql, utcnow
from utils.validators import clean_text, require_text, slugify

log = get_logger("tickets")

PANEL_SELECT_ID = "tickets:panel:select"
MIN_REASON = 8


def _default_categories() -> list[dict[str, Any]]:
    from core.config import guild_defaults

    return list((guild_defaults().get("tickets") or {}).get("categories", []))


# ---------------------------------------------------------------------------
# Modal de razón (obligatoria)
# ---------------------------------------------------------------------------
class TicketReasonModal(discord.ui.Modal, title="Razón del ticket"):
    """El usuario DEBE indicar por qué abre el ticket."""

    reason = discord.ui.TextInput(
        label="Razón del ticket:",
        style=discord.TextStyle.paragraph,
        placeholder="Explica brevemente por qué necesitas ayuda...",
        required=True,
        min_length=MIN_REASON,
        max_length=800,
    )

    def __init__(self, category_id: str, category_label: str) -> None:
        super().__init__(timeout=600)
        self.category_id = category_id
        self.category_label = category_label

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("Tickets")
        if cog is None:
            await interaction.response.send_message(
                "El módulo de tickets no está cargado.", ephemeral=True
            )
            return
        await cog.create_ticket(
            interaction,
            category_id=self.category_id,
            category_label=self.category_label,
            reason=str(self.reason.value),
        )


class TicketCloseModal(discord.ui.Modal, title="Razón del cierre"):
    """Motivo de cierre (opcional) que queda registrado en logs y transcripción."""

    reason = discord.ui.TextInput(
        label="¿Por qué cierras el ticket?",
        style=discord.TextStyle.paragraph,
        placeholder="Atendido, sin respuesta, duplicado...",
        required=False,
        max_length=500,
    )

    def __init__(self, ticket_id: int) -> None:
        super().__init__(timeout=300)
        self.ticket_id = ticket_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        reason = clean_text(str(self.reason.value or ""), 500) or "Sin motivo indicado"
        await start_close_confirmation(interaction, self.ticket_id, reason)


# ---------------------------------------------------------------------------
# Panel con select de categorías
# ---------------------------------------------------------------------------
class TicketPanelSelect(discord.ui.Select):
    """Select persistente con las 7 categorías de ticket."""

    def __init__(self) -> None:
        categories = _default_categories()
        options = [
            discord.SelectOption(
                label=category["label"],
                value=category["id"],
                description=category.get("description", "")[:100],
                emoji=category.get("emoji"),
            )
            for category in categories[:25]
        ]
        super().__init__(
            placeholder="🎫 Selecciona la categoría de tu ticket...",
            custom_id=PANEL_SELECT_ID,
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("Tickets")
        if cog is None:
            await interaction.response.send_message(
                "El módulo de tickets no está cargado.", ephemeral=True
            )
            return
        await cog.open_reason_modal(interaction, str(self.values[0]))


class TicketPanelView(discord.ui.View):
    """Panel persistente del canal de tickets."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(TicketPanelSelect())


# ---------------------------------------------------------------------------
# Botones dentro del ticket
# ---------------------------------------------------------------------------
class TicketActionsView(discord.ui.View):
    """🔒 Cerrar · 🔔 Avisar staff · 📄 Transcripción · 🗑️ Eliminar."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        for label, emoji, custom_id, callback in (
            ("Cerrar ticket", "🔒", "ticket:cerrar", self._close),
            ("Avisar staff", "🔔", "ticket:avisar", self._notify),
            ("Transcripción", "📄", "ticket:transcript", self._transcript),
            ("Eliminar", "🗑️", "ticket:eliminar", self._delete),
        ):
            button = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, custom_id=custom_id)
            button.callback = callback
            self.add_item(button)

    async def _load_ticket(self, interaction: discord.Interaction) -> Optional[dict[str, Any]]:
        return await db.fetchone(
            "SELECT * FROM tickets WHERE channel_id = ? AND guild_id = ?",
            (str(interaction.channel_id), str(interaction.guild_id)),
        )

    async def _close(self, interaction: discord.Interaction) -> None:
        cfg = await db.guild_config(interaction.guild_id)
        ticket = await self._load_ticket(interaction)
        if ticket is None:
            await interaction.response.send_message(
                "Este canal ya no está vinculado a un ticket.", ephemeral=True
            )
            return
        if not await self._can_manage(interaction, cfg, ticket):
            return
        await interaction.response.send_modal(TicketCloseModal(int(ticket["id"])))

    async def _notify(self, interaction: discord.Interaction) -> None:
        cfg = await db.guild_config(interaction.guild_id)
        mentions = " ".join(f"<@&{role_id}>" for role_id in role_ids(cfg, "staff"))
        if not mentions:
            await interaction.response.send_message(
                "No hay roles de staff configurados (`/setup all`).", ephemeral=True
            )
            return
        await interaction.response.send_message(
            content=f"{mentions} — {interaction.user.mention} pide atención en este ticket.",
            allowed_mentions=discord.AllowedMentions(roles=True, users=True),
        )
        await db.log_event(
            interaction.guild_id,
            "ticket_aviso",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            content="Aviso al staff",
        )

    async def _transcript(self, interaction: discord.Interaction) -> None:
        cfg = await db.guild_config(interaction.guild_id)
        ticket = await self._load_ticket(interaction)
        if ticket is None:
            await interaction.response.send_message(
                "Este canal ya no está vinculado a un ticket.", ephemeral=True
            )
            return
        if not await self._can_manage(interaction, cfg, ticket):
            return
        await interaction.response.defer(ephemeral=True)
        path = await transcript_service.create_transcript(
            interaction.guild, ticket, closed_by=str(interaction.user), close_reason="En curso"
        )
        if path is None:
            await interaction.followup.send("No pude generar la transcripción.", ephemeral=True)
            return
        await transcript_service.deliver_transcript(
            interaction.client, interaction.guild, ticket, path, cfg
        )
        await interaction.followup.send(
            embed=success_embed(
                "📄 Transcripción generada",
                f"Se envió al canal de logs (`{Path(path).name}`).",
            ),
            ephemeral=True,
        )

    async def _delete(self, interaction: discord.Interaction) -> None:
        cfg = await db.guild_config(interaction.guild_id)
        ticket = await self._load_ticket(interaction)
        if ticket is None:
            await interaction.response.send_message(
                "Este canal ya no está vinculado a un ticket.", ephemeral=True
            )
            return
        if not await self._can_manage(interaction, cfg, ticket):
            return

        async def _confirm(confirm_interaction: discord.Interaction) -> None:
            await close_ticket(
                confirm_interaction.client,
                confirm_interaction.guild,
                int(ticket["id"]),
                closed_by=confirm_interaction.user,
                reason="Eliminado por el staff",
                delete_channel=True,
            )
            try:
                await confirm_interaction.response.edit_message(
                    embed=success_embed(
                        "🗑️ Ticket eliminado",
                        f"El ticket `#{ticket['id']}` se cerró, se guardó la transcripción y "
                        "el canal se eliminó.",
                    ),
                    view=None,
                )
            except discord.HTTPException:
                log.info("No se pudo confirmar la eliminación del ticket #%s", ticket["id"])

        view = ConfirmView(
            user_id=interaction.user.id,
            on_confirm=_confirm,
            confirm_label="Eliminar canal",
            cancel_label="Mantener",
        )
        await interaction.response.send_message(
            embed=warn_embed(
                "¿Eliminar el ticket?",
                "Se generará la transcripción, se enviará al canal de logs y "
                "**el canal se borrará automáticamente**. Esta acción no se puede deshacer.",
            ),
            view=view,
            ephemeral=True,
        )

    async def _can_manage(
        self, interaction: discord.Interaction, cfg: dict[str, Any], ticket: dict[str, Any]
    ) -> bool:
        from utils.permissions import is_staff

        if interaction.user.id == int(ticket["user_id"]) or is_staff(interaction.user, cfg):
            return True
        await interaction.response.send_message(
            "Solo el creador del ticket o el staff pueden gestionarlo.", ephemeral=True
        )
        return False


# ---------------------------------------------------------------------------
# Confirmación de cierre compartida por comando y botones
# ---------------------------------------------------------------------------
async def start_close_confirmation(
    interaction: discord.Interaction, ticket_id: int, reason: str
) -> None:
    """Muestra el botón de confirmación antes de cerrar y borrar el canal."""

    async def _confirm(confirm_interaction: discord.Interaction) -> None:
        await close_ticket(
            confirm_interaction.client,
            confirm_interaction.guild,
            ticket_id,
            closed_by=confirm_interaction.user,
            reason=reason,
            delete_channel=True,
        )
        try:
            await confirm_interaction.response.edit_message(
                embed=success_embed(
                    "🔒 Ticket cerrado",
                    "Se guardó el cierre, se envió la transcripción y el canal se eliminó.",
                ),
                view=None,
            )
        except discord.HTTPException:
            log.info("No se pudo confirmar el cierre del ticket #%s", ticket_id)

    view = ConfirmView(
        user_id=interaction.user.id,
        on_confirm=_confirm,
        confirm_label="Cerrar y eliminar",
        cancel_label="Cancelar",
    )
    await interaction.response.send_message(
        embed=warn_embed(
            "⚠️ Confirmar cierre",
            f"Vas a cerrar el ticket `#{ticket_id}`.\n**Motivo:** {reason}\n"
            "Se generará la transcripción y **el canal se eliminará automáticamente**.",
        ),
        view=view,
        ephemeral=True,
    )


async def close_ticket(
    bot: commands.Bot,
    guild: Optional[discord.Guild],
    ticket_id: int,
    *,
    closed_by: discord.abc.User,
    reason: str,
    delete_channel: bool,
) -> bool:
    """Flujo completo de cierre: registro, transcripción, logs, DM y borrado."""
    if guild is None:
        return False
    ticket = await db.fetchone(
        "SELECT * FROM tickets WHERE id = ? AND guild_id = ?", (ticket_id, str(guild.id))
    )
    if ticket is None:
        log.warning("No se encontró el ticket #%s para cerrar", ticket_id)
        return False

    cfg = await db.guild_config(guild.id)
    await db.execute(
        "UPDATE tickets SET status = 'cerrada', closed_at = ?, closed_by = ?, close_reason = ? "
        "WHERE id = ?",
        (utcnow(), str(closed_by.id), clean_text(reason, 500), ticket_id),
    )
    ticket = await db.fetchone("SELECT * FROM tickets WHERE id = ?", (ticket_id,)) or ticket

    path = await transcript_service.create_transcript(guild, ticket, str(closed_by), reason)
    if path is not None:
        await transcript_service.deliver_transcript(bot, guild, ticket, path, cfg)

    await log_action(
        guild,
        "ticket_cerrado",
        cfg=cfg,
        embed=ticket_close_embed(cfg, ticket, closed_by, reason),
        user_id=closed_by.id,
        target_id=ticket["user_id"],
        channel_id=ticket.get("channel_id"),
    )

    if delete_channel:
        channel = guild.get_channel(int(ticket["channel_id"])) if ticket.get("channel_id") else None
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.delete(reason=f"Ticket #{ticket_id} cerrado por {closed_by}")
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("No se pudo eliminar el canal del ticket #%s: %s", ticket_id, exc)
                try:
                    await channel.send(
                        embed=warn_embed(
                            "No pude eliminar este canal",
                            "Comprueba mis permisos de **Gestionar canales**; el ticket quedó "
                            "registrado como cerrado.",
                        )
                    )
                except discord.HTTPException:
                    log.info("Tampoco se pudo avisar dentro del canal del ticket #%s", ticket_id)
    return True


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class Tickets(commands.Cog):
    """Creación, gestión y cierre de tickets."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.open_channels: set[int] = set()

    async def register_views(self) -> None:
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketActionsView())
        rows = await db.fetchall(
            "SELECT channel_id FROM tickets WHERE status = 'abierta' AND channel_id IS NOT NULL"
        )
        self.open_channels = {int(row["channel_id"]) for row in rows if row["channel_id"]}

    # ------------------------------------------------------------------
    # Captura de mensajes para la transcripción
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or not message.channel.id:
            return
        if message.channel.id not in self.open_channels:
            return
        ticket = await db.fetchone(
            "SELECT id FROM tickets WHERE channel_id = ? AND status = 'abierta'",
            (str(message.channel.id),),
        )
        if ticket is None:
            self.open_channels.discard(message.channel.id)
            return
        content = message.content or ""
        if not content and message.embeds:
            embed = message.embeds[0]
            content = embed.description or embed.title or "[embed]"
        if not content and message.attachments:
            content = "[adjunto] " + ", ".join(attachment.url for attachment in message.attachments[:3])
        if not content:
            content = "[mensaje sin texto]"
        await db.execute(
            "INSERT INTO ticket_messages (ticket_id, author_id, author_name, content, attachments, is_bot) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                int(ticket["id"]),
                str(message.author.id),
                str(message.author),
                clean_text(content, 3000),
                ", ".join(a.url for a in message.attachments[:5]) or None,
                1 if message.author.bot else 0,
            ),
        )

    # ------------------------------------------------------------------
    # Apertura
    # ------------------------------------------------------------------
    async def open_reason_modal(self, interaction: discord.Interaction, category_id: str) -> None:
        cfg = await db.guild_config(interaction.guild_id)
        categories = (cfg.get("tickets") or {}).get("categories") or _default_categories()
        category = next(
            (item for item in categories if str(item.get("id")) == category_id),
            {"id": category_id, "label": category_id.title(), "emoji": "🎫"},
        )

        blocked = await self._blocked_reason(interaction, cfg)
        if blocked:
            await interaction.response.send_message(
                embed=error_embed("No puedes abrir un ticket", blocked), ephemeral=True
            )
            return

        await interaction.response.send_modal(
            TicketReasonModal(str(category["id"]), str(category["label"]))
        )

    async def _blocked_reason(
        self, interaction: discord.Interaction, cfg: dict[str, Any]
    ) -> str:
        existing = await db.fetchone(
            "SELECT id FROM tickets WHERE guild_id = ? AND user_id = ? AND status = 'abierta'",
            (str(interaction.guild_id), str(interaction.user.id)),
        )
        if existing is not None:
            return f"Ya tienes el ticket `#{existing['id']}` abierto. Ciérralo antes de abrir otro."

        tickets_cfg = cfg.get("tickets") or {}
        cooldown = int(tickets_cfg.get("cooldown_seconds", 120))
        if cooldown > 0:
            last = await db.fetchone(
                "SELECT created_at FROM tickets WHERE guild_id = ? AND user_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (str(interaction.guild_id), str(interaction.user.id)),
            )
            if last and last.get("created_at"):
                created = from_sql(last["created_at"])
                if created is not None:
                    elapsed = (datetime.now(timezone.utc) - created).total_seconds()
                    if elapsed < cooldown:
                        remaining = int(cooldown - elapsed) + 1
                        return f"Espera **{remaining} segundos** antes de abrir otro ticket."
        return ""

    async def create_ticket(
        self,
        interaction: discord.Interaction,
        *,
        category_id: str,
        category_label: str,
        reason: str,
    ) -> None:
        cfg = await db.guild_config(interaction.guild_id)
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Los tickets solo funcionan en servidores.", ephemeral=True
            )
            return

        clean_reason = require_text(reason, "La razón del ticket", MIN_REASON, 800)
        blocked = await self._blocked_reason(interaction, cfg)
        if blocked:
            await interaction.response.send_message(
                embed=error_embed("No se pudo abrir el ticket", blocked), ephemeral=True
            )
            return

        overwrites = self._overwrites(guild, cfg, interaction.user)
        category_id_config = (cfg.get("categories") or {}).get("tickets")
        category = guild.get_channel(int(category_id_config)) if category_id_config else None
        category = category if isinstance(category, discord.CategoryChannel) else None

        name = f"ticket-{slugify(interaction.user.display_name, 40)}"
        try:
            channel = await guild.create_text_channel(
                name,
                overwrites=overwrites,
                category=category,
                reason=f"Ticket de {interaction.user} · {category_label}",
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed(
                    "Sin permisos",
                    "No pude crear el canal: revisa que el bot tenga permiso de "
                    "**Gestionar canales** y esté por encima del rol @everyone.",
                ),
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                embed=error_embed("Error de Discord", f"No se pudo crear el canal: {exc}"),
                ephemeral=True,
            )
            return

        cursor = await db.execute(
            "INSERT INTO tickets (guild_id, channel_id, user_id, category, category_label, reason, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'abierta')",
            (
                str(guild.id),
                str(channel.id),
                str(interaction.user.id),
                category_id,
                category_label,
                clean_reason,
            ),
        )
        ticket_id = int(cursor.lastrowid or 0)
        self.open_channels.add(channel.id)

        embed = ticket_open_embed(interaction.user, cfg, category_label, clean_reason, ticket_id)
        await channel.send(
            content=interaction.user.mention,
            embed=embed,
            view=TicketActionsView(),
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        await db.execute(
            "INSERT INTO ticket_messages (ticket_id, author_id, author_name, content, is_bot) "
            "VALUES (?, ?, ?, ?, 1)",
            (
                ticket_id,
                str(self.bot.user.id if self.bot.user else 0),
                str(self.bot.user or "KorianMC"),
                f"Ticket abierto · {category_label} · Razón: {clean_reason}",
            ),
        )

        await log_action(
            guild,
            "ticket_abierto",
            cfg=cfg,
            embed=base_embed(
                f"🎫 Ticket #{ticket_id}",
                f"**Usuario:** {interaction.user.mention}\n"
                f"**Categoría:** {category_label}\n"
                f"**Razón:** {clean_text(clean_reason, 500)}\n"
                f"**Canal:** {channel.mention}",
            ),
            user_id=interaction.user.id,
            channel_id=channel.id,
        )
        await interaction.response.send_message(
            embed=success_embed(
                "🎫 Ticket creado",
                f"Tu ticket está listo en {channel.mention} (`#{ticket_id}`).",
            ),
            ephemeral=True,
        )

    def _overwrites(
        self, guild: discord.Guild, cfg: dict[str, Any], member: discord.Member
    ) -> dict[Any, discord.PermissionOverwrite]:
        everyone = guild.default_role
        overwrites: dict[Any, discord.PermissionOverwrite] = {
            everyone: discord.PermissionOverwrite(
                view_channel=False, send_messages=False, read_message_history=False
            ),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
        }
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                manage_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )
        for role_id in role_ids(cfg, "staff") + role_ids(cfg, "admin"):
            role = guild.get_role(role_id)
            if role is not None and role not in overwrites:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True,
                    manage_messages=True,
                )
        return overwrites

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------
    @commands.hybrid_group(name="ticket", description="Sistema de tickets.", invoke_without_command=True)
    @commands.guild_only()
    async def ticket(self, ctx: commands.Context) -> None:
        cfg = await db.guild_config(ctx.guild.id)
        panel_channel = channel_id(cfg, "tickets")
        embed = base_embed(
            "🎫 Tickets",
            f"Abre tu ticket con `/ticket create` o usa el panel "
            f"(<#{panel_channel}>).\n"
            "`/ticket close` · `/ticket transcript` · `/ticket claim`",
        )
        await ctx.send(embed=embed)

    @ticket.command(name="create", description="Abre un ticket de soporte.")
    @commands.guild_only()
    async def ticket_create(
        self,
        ctx: commands.Context,
        categoria: Optional[str] = None,
        razon: Optional[str] = None,
    ) -> None:
        if categoria and razon:
            cfg = await db.guild_config(ctx.guild.id)
            categories = (cfg.get("tickets") or {}).get("categories") or _default_categories()
            category = next(
                (item for item in categories if str(item.get("id")).lower() == categoria.lower()),
                None,
            )
            if category is None:
                await ctx.send(
                    embed=error_embed(
                        "Categoría no válida",
                        "Categorías: " + ", ".join(str(item["id"]) for item in categories),
                    )
                )
                return
            blocked = await self._blocked_reason_command(ctx, cfg)
            if blocked:
                await ctx.send(embed=error_embed("No puedes abrir un ticket", blocked))
                return
            if isinstance(ctx.interaction, discord.Interaction):
                await self.create_ticket(
                    ctx.interaction,
                    category_id=str(category["id"]),
                    category_label=str(category["label"]),
                    reason=razon,
                )
                return
            result = await self._create_from_prefix(
                ctx, str(category["id"]), str(category["label"]), razon
            )
            await ctx.send(result)
            return

        await ctx.send(
            embed=base_embed(
                "🎫 Nuevo ticket",
                "Selecciona la categoría para abrir tu ticket. Se te pedirá la **razón** "
                "(obligatoria) antes de crearlo.",
            ),
            view=TicketPanelView(),
        )

    @ticket.command(name="close", description="Cierra un ticket (con confirmación).")
    @commands.guild_only()
    async def ticket_close(
        self, ctx: commands.Context, razon: Optional[str] = None
    ) -> None:
        cfg = await db.guild_config(ctx.guild.id)
        ticket = await db.fetchone(
            "SELECT * FROM tickets WHERE channel_id = ? AND status = 'abierta'",
            (str(ctx.channel.id),),
        )
        if ticket is None:
            await ctx.send(
                embed=error_embed(
                    "Sin ticket", "Este canal no tiene un ticket abierto. Entra al canal del ticket."
                )
            )
            return
        from utils.permissions import is_staff

        if not is_staff(ctx.author, cfg) and int(ticket["user_id"]) != ctx.author.id:
            await ctx.send(
                embed=error_embed(
                    "Permisos", "Solo el creador del ticket o el staff pueden cerrarlo."
                )
            )
            return
        reason = clean_text(razon, 500) or "Sin motivo indicado"
        if isinstance(ctx.interaction, discord.Interaction):
            await start_close_confirmation(ctx.interaction, int(ticket["id"]), reason)
            return
        await ctx.send(
            embed=warn_embed(
                "Cierra con el botón",
                f"Usa `/ticket close razon:{reason}` desde slash para recibir el botón de confirmación, "
                "o pulsa 🔒 dentro del canal del ticket.",
            )
        )

    @ticket.command(name="transcript", description="Genera la transcripción del ticket actual.")
    @commands.guild_only()
    async def ticket_transcript(self, ctx: commands.Context) -> None:
        cfg = await db.guild_config(ctx.guild.id)
        ticket = await db.fetchone(
            "SELECT * FROM tickets WHERE channel_id = ?", (str(ctx.channel.id),)
        )
        if ticket is None:
            await ctx.send(
                embed=error_embed("Sin ticket", "Este canal no tiene un ticket registrado.")
            )
            return
        from utils.permissions import is_staff

        if not is_staff(ctx.author, cfg) and int(ticket["user_id"]) != ctx.author.id:
            await ctx.send(embed=error_embed("Permisos", "No puedes pedir esta transcripción."))
            return
        if isinstance(ctx.interaction, discord.Interaction):
            await ctx.interaction.response.defer(ephemeral=True)
        path = await transcript_service.create_transcript(
            ctx.guild, ticket, closed_by=str(ctx.author), close_reason="Transcripción manual"
        )
        if path is None:
            await ctx.send(embed=error_embed("Error", "No se pudo generar la transcripción."))
            return
        await transcript_service.deliver_transcript(
            self.bot, ctx.guild, ticket, path, cfg
        )
        await ctx.send(
            embed=success_embed(
                "📄 Transcripción generada",
                f"Enviada al canal de logs (`{Path(path).name}`).",
            )
        )

    @ticket.command(name="claim", description="[Staff] Reclama un ticket para atenderlo.")
    @staff_only()
    async def ticket_claim(self, ctx: commands.Context) -> None:
        ticket = await db.fetchone(
            "SELECT * FROM tickets WHERE channel_id = ? AND status = 'abierta'",
            (str(ctx.channel.id),),
        )
        if ticket is None:
            await ctx.send(embed=error_embed("Sin ticket", "Este canal no tiene un ticket abierto."))
            return
        if ticket.get("staff_id"):
            await ctx.send(
                embed=warn_embed(
                    "Ya reclamado",
                    f"Este ticket ya está asignado a <@{ticket['staff_id']}>.",
                )
            )
            return
        await db.execute(
            "UPDATE tickets SET staff_id = ? WHERE id = ?", (str(ctx.author.id), int(ticket["id"]))
        )
        await ctx.send(
            embed=success_embed(
                "🤝 Ticket reclamado",
                f"{ctx.author.mention} se encargará de este ticket.",
            )
        )
        await log_action(
            ctx.guild,
            "ticket_reclamado",
            user_id=ctx.author.id,
            target_id=ticket["user_id"],
            channel_id=ctx.channel.id,
            content=f"#{ticket['id']} por {ctx.author}",
        )

    # ------------------------------------------------------------------
    async def _blocked_reason_command(
        self, ctx: commands.Context, cfg: dict[str, Any]
    ) -> str:
        existing = await db.fetchone(
            "SELECT id FROM tickets WHERE guild_id = ? AND user_id = ? AND status = 'abierta'",
            (str(ctx.guild.id), str(ctx.author.id)),
        )
        if existing is not None:
            return f"Ya tienes el ticket `#{existing['id']}` abierto."
        tickets_cfg = cfg.get("tickets") or {}
        cooldown = int(tickets_cfg.get("cooldown_seconds", 120))
        last = await db.fetchone(
            "SELECT created_at FROM tickets WHERE guild_id = ? AND user_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (str(ctx.guild.id), str(ctx.author.id)),
        )
        if cooldown > 0 and last and last.get("created_at"):
            created = from_sql(last["created_at"])
            if created is not None:
                elapsed = (datetime.now(timezone.utc) - created).total_seconds()
                if elapsed < cooldown:
                    return f"Espera **{int(cooldown - elapsed) + 1} segundos** antes de abrir otro ticket."
        return ""

    async def _create_from_prefix(
        self, ctx: commands.Context, category_id: str, category_label: str, reason: str
    ) -> str:
        """Versión sin interacción para prefijos: devuelve el texto de respuesta."""
        cfg = await db.guild_config(ctx.guild.id)
        blocked = await self._blocked_reason_command(ctx, cfg)
        if blocked:
            return f"❌ {blocked}"
        clean_reason = require_text(reason, "La razón del ticket", MIN_REASON, 800)
        channel = await ctx.guild.create_text_channel(
            f"ticket-{slugify(ctx.author.display_name, 40)}",
            overwrites=self._overwrites(ctx.guild, cfg, ctx.author),
            reason=f"Ticket de {ctx.author} · {category_label}",
        )
        cursor = await db.execute(
            "INSERT INTO tickets (guild_id, channel_id, user_id, category, category_label, reason, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'abierta')",
            (
                str(ctx.guild.id),
                str(channel.id),
                str(ctx.author.id),
                category_id,
                category_label,
                clean_reason,
            ),
        )
        ticket_id = int(cursor.lastrowid or 0)
        self.open_channels.add(channel.id)
        await channel.send(
            content=ctx.author.mention,
            embed=ticket_open_embed(ctx.author, cfg, category_label, clean_reason, ticket_id),
            view=TicketActionsView(),
        )
        await log_action(
            ctx.guild,
            "ticket_abierto",
            cfg=cfg,
            user_id=ctx.author.id,
            channel_id=channel.id,
            content=f"#{ticket_id} {category_label}: {clean_reason}",
        )
        return f"✅ Ticket `#{ticket_id}` creado en {channel.mention}."


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tickets(bot))
