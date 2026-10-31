"""Postulaciones al equipo: select de puestos, formulario modal y revisión."""

from __future__ import annotations

import json

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from services.embed_factory import application_embed
from services.notification import log_action, send_template_dm
from utils.checks import ensure_staff_interaction, staff_only
from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.helpers import Paginator
from utils.validators import clean_text

log = get_logger("postulaciones")

PUESTOS = [
    ("staff", "🧑‍⚖️", "Staff", "Moderación y soporte del servidor"),
    ("developer", "👨‍💻", "Developer", "Bots, webs y herramientas"),
    ("disenador", "🎨", "Diseñador", "Arte, banners y recursos visuales"),
    ("owner", "👑", "Owner", "Dirección y gestión del proyecto"),
    ("creador", "🎥", "Creador de contenido", "Vídeos, streams y clips"),
    ("otro", "📌", "Otro", "Otra colaboración que quieras proponer"),
]

class ApplicationModal(discord.ui.Modal):
    """Formulario de postulación adaptado al puesto seleccionado."""

    def __init__(self, puesto_id: str, puesto_label: str) -> None:
        super().__init__(timeout=600)
        self.puesto_id = puesto_id
        self.puesto_label = puesto_label
        self.title = f"🧾 Postulación · {puesto_label}"[:45]

        self.experiencia = discord.ui.TextInput(
            label="Experiencia previa",
            style=discord.TextStyle.paragraph,
            placeholder="Cuéntanos dónde has trabajado antes...",
            required=True,
            min_length=10,
            max_length=600,
        )
        self.disponibilidad = discord.ui.TextInput(
            label="Disponibilidad",
            placeholder="Ej: tardes de lunes a viernes, 3 horas diarias",
            required=True,
            min_length=5,
            max_length=200,
        )
        self.motivacion = discord.ui.TextInput(
            label="¿Por qué deberíerte elegir?",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
        )
        self.add_item(self.experiencia)
        self.add_item(self.disponibilidad)
        self.add_item(self.motivacion)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("Postulaciones")
        if cog is None:
            await interaction.response.send_message(
                "El módulo de postulaciones no está cargado.", ephemeral=True
            )
            return
        answers = {
            "Experiencia": str(self.experiencia.value),
            "Disponibilidad": str(self.disponibilidad.value),
            "Motivación": str(self.motivacion.value or "No indicada"),
        }
        await cog.publish(interaction, self.puesto_id, self.puesto_label, answers)


class ApplicationSelect(discord.ui.Select):
    """Select de puestos: Staff, Developer, Diseñador, Owner, Creador y Otro."""

    def __init__(self) -> None:
        options = [
            discord.SelectOption(
                label=label,
                value=value,
                description=description,
                emoji=emoji,
            )
            for value, emoji, label, description in PUESTOS
        ]
        super().__init__(
            placeholder="Elige el puesto que quieres postular...",
            custom_id="postulacion:select",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        value = str(self.values[0])
        label = next((label for key, _, label, _ in PUESTOS if key == value), value)
        await interaction.response.send_modal(ApplicationModal(value, label))


class ApplicationPanelView(discord.ui.View):
    """Panel persistente con el select de puestos."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(ApplicationSelect())


class ApplicationReviewView(discord.ui.View):
    """Botones de staff para aprobar o rechazar una postulación."""

    def __init__(self, application_id: int) -> None:
        super().__init__(timeout=None)
        self.application_id = application_id
        approve = discord.ui.Button(
            label="Aprobar",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"postulacion:{application_id}:ok",
        )
        approve.callback = self._approve
        reject = discord.ui.Button(
            label="Rechazar",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"postulacion:{application_id}:no",
        )
        reject.callback = self._reject
        self.add_item(approve)
        self.add_item(reject)

    async def _handle(self, interaction: discord.Interaction, status: str) -> None:
        cfg = await db.guild_config(interaction.guild_id)
        if not await ensure_staff_interaction(interaction, cfg):
            return
        row = await db.fetchone(
            "SELECT * FROM applications WHERE id = ? AND guild_id = ?",
            (self.application_id, str(interaction.guild_id)),
        )
        if row is None:
            await interaction.response.send_message("Esta postulación ya no existe.", ephemeral=True)
            return
        if str(row["status"]) != "pendiente":
            await interaction.response.send_message(
                f"Ya fue revisada ({row['status']}).", ephemeral=True
            )
            return

        await db.execute(
            "UPDATE applications SET status = ?, handled_by = ?, updated_at = datetime('now') "
            "WHERE id = ? AND guild_id = ?",
            (status, str(interaction.user.id), self.application_id, str(interaction.guild_id)),
        )
        data = dict(row)
        data["status"] = status
        embed = application_embed(data)
        embed.color = discord.Color.green() if status == "aprobada" else discord.Color.red()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

        await log_action(
            interaction.guild,
            "postulacion_revisada",
            cfg=cfg,
            embed=success_embed(
                "Postulación revisada",
                f"**#{self.application_id}** ({row['puesto']}) → **{status}** por {interaction.user.mention}.",
            ),
            user_id=interaction.user.id,
            target_id=row["user_id"],
        )
        await send_template_dm(
            interaction.client,
            interaction.guild,
            int(row["user_id"]),
            "report_update",
            fallback="Tu postulación #{id} cambió a {status}.",
            id=self.application_id,
            status=status,
        )

    async def _approve(self, interaction: discord.Interaction) -> None:
        await self._handle(interaction, "aprobada")

    async def _reject(self, interaction: discord.Interaction) -> None:
        await self._handle(interaction, "rechazada")


class Postulaciones(commands.Cog):
    """Postulaciones al equipo Staff por puestos."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._panel_channel: dict[int, int] = {}

    async def register_views(self) -> None:
        self.bot.add_view(ApplicationPanelView())
        rows = await db.fetchall(
            "SELECT id FROM applications WHERE status = 'pendiente' ORDER BY id DESC LIMIT 100"
        )
        for row in rows:
            self.bot.add_view(ApplicationReviewView(int(row["id"])))

    async def publish(
        self,
        interaction: discord.Interaction,
        puesto_id: str,
        puesto_label: str,
        answers: dict[str, str],
    ) -> None:
        cfg = await db.guild_config(interaction.guild_id)
        channel_id = (cfg.get("channels") or {}).get("applications")
        if not channel_id:
            await interaction.response.send_message(
                embed=error_embed(
                    "Canal sin configurar",
                    "El staff debe definir el canal de postulaciones con `/setup all`.",
                ),
                ephemeral=True,
            )
            return
        channel = interaction.guild.get_channel(int(channel_id)) if interaction.guild else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed("Canal no encontrado", "El canal de postulaciones ya no existe."),
                ephemeral=True,
            )
            return

        cursor = await db.execute(
            "INSERT INTO applications (guild_id, user_id, puesto, answers, channel_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(interaction.guild_id),
                str(interaction.user.id),
                puesto_label,
                json.dumps(answers, ensure_ascii=False),
                str(channel.id),
            ),
        )
        application_id = int(cursor.lastrowid or 0)
        data = {
            "id": application_id,
            "guild_id": str(interaction.guild_id),
            "user_id": str(interaction.user.id),
            "puesto": puesto_label,
            "answers": json.dumps(answers, ensure_ascii=False),
            "status": "pendiente",
            "created_at": None,
        }
        message = await channel.send(
            embed=application_embed(data, interaction.user),
            view=ApplicationReviewView(application_id),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )
        await db.execute(
            "UPDATE applications SET message_id = ? WHERE id = ?", (str(message.id), application_id)
        )
        await log_action(
            interaction.guild,
            "postulacion_recibida",
            cfg=cfg,
            embed=warn_embed(
                "Nueva postulación",
                f"**{interaction.user}** postula a **{puesto_label}** (`#{application_id}`).",
            ),
            user_id=interaction.user.id,
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Postulación enviada",
                f"Recibimos tu postulación a **{puesto_label}** con el identificador `#{application_id}`.",
            ),
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    @commands.hybrid_group(
        name="postulacion",
        description="Postula a un puesto del equipo Staff.",
        invoke_without_command=True,
    )
    @commands.guild_only()
    async def postulacion(self, ctx: commands.Context) -> None:
        embed = base_embed(
            "🧾 Postulaciones abiertas",
            "Elige el puesto que quieres postular y completa el formulario.\n"
            "El staff revisará tu solicitud y te avisará por DM.",
        )
        await ctx.send(embed=embed, view=ApplicationPanelView())

    @postulacion.command(name="puestos", description="Lista los puestos disponibles.")
    @commands.guild_only()
    async def postulacion_puestos(self, ctx: commands.Context) -> None:
        lines = [f"{emoji} **{label}** — {description}" for _, emoji, label, description in PUESTOS]
        await ctx.send(embed=base_embed("📌 Puestos disponibles", "\n".join(lines)))

    @postulacion.command(name="list", description="[Staff] Lista las postulaciones pendientes.")
    @staff_only()
    async def postulacion_list(self, ctx: commands.Context) -> None:
        rows = await db.fetchall(
            "SELECT * FROM applications WHERE guild_id = ? AND status = 'pendiente' ORDER BY id DESC LIMIT 30",
            (str(ctx.guild.id),),
        )
        if not rows:
            await ctx.send(embed=base_embed("🧾 Postulaciones", "No hay postulaciones pendientes."))
            return
        lines = [
            f"**#{row['id']}** <@{row['user_id']}> — {row['puesto']} · {clean_text(str(row['answers'])[:80], 80)}"
            for row in rows
        ]
        embeds = [
            base_embed("🧾 Postulaciones pendientes", "\n".join(lines[start : start + 15]))
            for start in range(0, len(lines), 15)
        ]
        for page, embed in enumerate(embeds, start=1):
            embed.set_footer(text=f"Página {page}/{len(embeds)}")
        await Paginator(embeds, user_id=ctx.author.id).start(ctx)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Postulaciones(bot))
