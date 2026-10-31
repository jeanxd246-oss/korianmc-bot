"""Reportes de usuarios: formulario con modal, revisión de staff y estados."""

from __future__ import annotations

from typing import Optional

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from services.embed_factory import report_embed
from services.notification import log_action, send_template_dm
from utils.checks import ensure_staff_interaction, staff_only
from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.helpers import Paginator
from utils.validators import clean_text, require_text

log = get_logger("reportes")

STATUSES = ("pendiente", "aprobada", "rechazada")


class ReportModal(discord.ui.Modal, title="📝 Enviar reporte"):
    """Formulario de reporte: motivo obligatorio y evidencia opcional."""

    motivo = discord.ui.TextInput(
        label="¿Qué ocurrió?",
        style=discord.TextStyle.paragraph,
        placeholder="Describe el motivo del reporte...",
        required=True,
        min_length=8,
        max_length=800,
    )
    evidencia = discord.ui.TextInput(
        label="Evidencia (opcional)",
        style=discord.TextStyle.paragraph,
        placeholder="Enlaces, mensajes o capturas...",
        required=False,
        max_length=500,
    )

    def __init__(self, target: discord.Member) -> None:
        super().__init__()
        self.target = target
        self.title = f"📝 Reportar a {target.display_name}"[:45]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("Reportes")
        if cog is None:
            await interaction.response.send_message(
                "El módulo de reportes no está cargado.", ephemeral=True
            )
            return
        await cog.publish(
            interaction,
            target=self.target,
            reason=str(self.motivo.value),
            evidence=str(self.evidencia.value or ""),
        )


class ReportReviewView(discord.ui.View):
    """Botones de revisión para staff: aprobar o descartar un reporte."""

    def __init__(self, report_id: int) -> None:
        super().__init__(timeout=None)
        self.report_id = report_id
        approve = discord.ui.Button(
            label="Aprobar",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"report:{report_id}:ok",
        )
        approve.callback = self._approve
        reject = discord.ui.Button(
            label="Descartar",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"report:{report_id}:no",
        )
        reject.callback = self._reject
        self.add_item(approve)
        self.add_item(reject)

    async def _handle(self, interaction: discord.Interaction, status: str) -> None:
        cfg = await db.guild_config(interaction.guild_id)
        if not await ensure_staff_interaction(interaction, cfg):
            return
        report = await db.fetchone(
            "SELECT * FROM reports WHERE id = ? AND guild_id = ?",
            (self.report_id, str(interaction.guild_id)),
        )
        if report is None:
            await interaction.response.send_message("Este reporte ya no existe.", ephemeral=True)
            return
        if str(report["status"]) != "pendiente":
            await interaction.response.send_message(
                f"Este reporte ya fue revisado ({report['status']}).", ephemeral=True
            )
            return

        await db.execute(
            "UPDATE reports SET status = ?, handled_by = ?, updated_at = datetime('now') "
            "WHERE id = ? AND guild_id = ?",
            (status, str(interaction.user.id), self.report_id, str(interaction.guild_id)),
        )
        data = dict(report)
        data["status"] = status
        embed = report_embed(data)
        embed.title = f"📢 Reporte #{self.report_id} · {status.title()}"
        embed.color = discord.Color.green() if status == "aprobada" else discord.Color.red()

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

        await log_action(
            interaction.guild,
            "reporte_revisado",
            cfg=cfg,
            embed=success_embed(
                "Reporte revisado",
                f"**#{self.report_id}** → **{status}** por {interaction.user.mention}",
            ),
            user_id=interaction.user.id,
            target_id=report["reporter_id"],
        )
        await send_template_dm(
            interaction.client,
            interaction.guild,
            int(report["reporter_id"]),
            "report_update",
            fallback="Tu reporte #{id} cambió a {status}.",
            id=self.report_id,
            status=status,
        )

    async def _approve(self, interaction: discord.Interaction) -> None:
        await self._handle(interaction, "aprobada")

    async def _reject(self, interaction: discord.Interaction) -> None:
        await self._handle(interaction, "rechazada")


class Reportes(commands.Cog):
    """Envío, revisión e historial de reportes."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def register_views(self) -> None:
        rows = await db.fetchall(
            "SELECT id FROM reports WHERE status = 'pendiente' ORDER BY id DESC LIMIT 100"
        )
        for row in rows:
            self.bot.add_view(ReportReviewView(int(row["id"])))

    # ------------------------------------------------------------------
    async def publish(
        self,
        interaction: discord.Interaction,
        *,
        target: discord.Member,
        reason: str,
        evidence: str,
    ) -> None:
        cfg = await db.guild_config(interaction.guild_id)
        channel_id = (cfg.get("channels") or {}).get("reports")
        if not channel_id:
            await interaction.response.send_message(
                embed=error_embed(
                    "Canal sin configurar",
                    "El staff aún no ha definido el canal de reportes con `/setup moderation`.",
                ),
                ephemeral=True,
            )
            return
        channel = interaction.guild.get_channel(int(channel_id)) if interaction.guild else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed("Canal no encontrado", "El canal de reportes ya no existe."),
                ephemeral=True,
            )
            return

        clean_reason = require_text(reason, "El motivo", 8, 800)
        cursor = await db.execute(
            "INSERT INTO reports (guild_id, reporter_id, target_id, target_tag, reason, evidence, channel_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(interaction.guild_id),
                str(interaction.user.id),
                str(target.id),
                str(target),
                clean_reason,
                clean_text(evidence, 500) or None,
                str(channel.id),
            ),
        )
        report_id = int(cursor.lastrowid or 0)
        data = {
            "id": report_id,
            "reporter_id": str(interaction.user.id),
            "target_id": str(target.id),
            "target_tag": str(target),
            "reason": clean_reason,
            "evidence": evidence,
            "status": "pendiente",
            "created_at": None,
        }
        message = await channel.send(
            embed=report_embed(data, interaction.user),
            view=ReportReviewView(report_id),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )
        await db.execute(
            "UPDATE reports SET message_id = ? WHERE id = ?", (str(message.id), report_id)
        )
        await log_action(
            interaction.guild,
            "reporte_recibido",
            cfg=cfg,
            embed=warn_embed(
                "Nuevo reporte",
                f"**#{report_id}** de {interaction.user.mention} contra **{target}**.",
            ),
            user_id=interaction.user.id,
            target_id=target.id,
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Reporte enviado",
                f"Tu reporte **#{report_id}** fue enviado al staff. Te avisaremos por DM cuando se revise.",
            ),
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    @commands.hybrid_group(name="report", description="Sistema de reportes de usuarios.", invoke_without_command=True)
    @commands.guild_only()
    async def report(self, ctx: commands.Context) -> None:
        await ctx.send(
            embed=base_embed(
                "📢 Reportes",
                "Usa `/report send @usuario` para abrir el formulario,\n"
                "`/report list` para verlos (staff) o `/report history @usuario` para el historial.",
            )
        )

    @report.command(name="send", description="Abre el formulario de reporte contra un usuario.")
    @commands.guild_only()
    async def report_send(self, ctx: commands.Context, usuario: discord.Member) -> None:
        if usuario.id == ctx.author.id:
            await ctx.send(embed=error_embed("No permitido", "No puedes reportarte a ti mismo."))
            return
        if usuario.bot:
            await ctx.send(embed=error_embed("No permitido", "No se puede reportar a un bot."))
            return
        if isinstance(ctx.interaction, discord.Interaction):
            await ctx.interaction.response.send_modal(ReportModal(usuario))
            return
        try:
            await ctx.author.send("Abre el formulario de reporte desde un comando `/report send` en el servidor.")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send(
                embed=warn_embed(
                    "Formulario por slash",
                    f"Usa `/report send usuario:{usuario}` para abrir el formulario de **{usuario.display_name}**.",
                )
            )
        else:
            await ctx.send(
                embed=success_embed(
                    "Formulario enviado",
                    f"Te he enviado un aviso; usa `/report send usuario:{usuario}` para completarlo.",
                )
            )

    @report.command(name="list", description="[Staff] Lista los reportes por estado.")
    @staff_only()
    async def report_list(self, ctx: commands.Context, estado: Optional[str] = None) -> None:
        state = (estado or "pendiente").strip().lower()
        if state not in STATUSES:
            await ctx.send(
                embed=warn_embed(
                    "Estado no válido",
                    f"Estados disponibles: {', '.join(STATUSES)}.",
                )
            )
            return
        rows = await db.fetchall(
            "SELECT * FROM reports WHERE guild_id = ? AND status = ? ORDER BY id DESC LIMIT 50",
            (str(ctx.guild.id), state),
        )
        if not rows:
            await ctx.send(embed=base_embed("📢 Reportes", f"No hay reportes en estado **{state}**."))
            return
        embeds = []
        for start in range(0, len(rows), 5):
            lines = [
                f"**#{row['id']}** → <@{row['target_id']}> · {clean_text(row['reason'], 90)} · "
                f"por <@{row['reporter_id']}>"
                for row in rows[start : start + 5]
            ]
            embed = base_embed(f"📢 Reportes · {state}", "\n".join(lines))
            embeds.append(embed)
        for page, embed in enumerate(embeds, start=1):
            embed.set_footer(text=f"Página {page}/{len(embeds)}")
        await Paginator(embeds, user_id=ctx.author.id).start(ctx)

    @report.command(name="history", description="Historial de reportes de un usuario.")
    @commands.guild_only()
    async def report_history(self, ctx: commands.Context, usuario: Optional[discord.Member] = None) -> None:
        target = usuario
        if target is None:
            rows = await db.fetchall(
                "SELECT * FROM reports WHERE guild_id = ? AND reporter_id = ? ORDER BY id DESC LIMIT 10",
                (str(ctx.guild.id), str(ctx.author.id)),
            )
        else:
            from utils.permissions import is_staff

            cfg = await db.guild_config(ctx.guild.id)
            if not is_staff(ctx.author, cfg):
                await ctx.send(
                    embed=error_embed(
                        "Permisos", "Necesitas el rol de staff para ver reportes de otros."
                    )
                )
                return
            rows = await db.fetchall(
                "SELECT * FROM reports WHERE guild_id = ? AND (reporter_id = ? OR target_id = ?) "
                "ORDER BY id DESC LIMIT 15",
                (str(ctx.guild.id), str(target.id), str(target.id)),
            )
        if not rows:
            await ctx.send(embed=base_embed("📑 Historial", "No hay reportes registrados."))
            return
        lines = [
            f"**#{row['id']}** · {row['status']} · contra <@{row['target_id']}> · {clean_text(row['reason'], 80)}"
            for row in rows
        ]
        embed = base_embed("📑 Historial de reportes", "\n".join(lines))
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Reportes(bot))
