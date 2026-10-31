"""Sugerencias de la comunidad: votos con botones, estados y revisión."""

from __future__ import annotations

from typing import Optional

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from services.embed_factory import suggestion_embed
from services.notification import log_action, send_template_dm
from utils.checks import ensure_staff_interaction
from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.helpers import Paginator
from utils.validators import clean_text, require_text

log = get_logger("sugerencias")

STATUSES = ("pendiente", "aprobada", "rechazada")


class SuggestionView(discord.ui.View):
    """Votos 👍/👎 y revisión de staff sobre la misma publicación."""

    def __init__(self, suggestion_id: int) -> None:
        super().__init__(timeout=None)
        self.suggestion_id = suggestion_id

        up = discord.ui.Button(
            label="A favor", emoji="👍", style=discord.ButtonStyle.success,
            custom_id=f"sug:{suggestion_id}:up",
        )
        up.callback = self._up
        down = discord.ui.Button(
            label="En contra", emoji="👎", style=discord.ButtonStyle.danger,
            custom_id=f"sug:{suggestion_id}:down",
        )
        down.callback = self._down
        approve = discord.ui.Button(
            label="Aprobar", style=discord.ButtonStyle.primary,
            custom_id=f"sug:{suggestion_id}:ok",
        )
        approve.callback = self._approve
        reject = discord.ui.Button(
            label="Rechazar", style=discord.ButtonStyle.secondary,
            custom_id=f"sug:{suggestion_id}:no",
        )
        reject.callback = self._reject
        self.add_item(up)
        self.add_item(down)
        self.add_item(approve)
        self.add_item(reject)

    # ------------------------------------------------------------------
    async def _refresh(self, interaction: discord.Interaction) -> None:
        row = await db.fetchone(
            "SELECT * FROM suggestions WHERE id = ? AND guild_id = ?",
            (self.suggestion_id, str(interaction.guild_id)),
        )
        if row is None or interaction.message is None:
            return
        author = interaction.client.get_user(int(row["user_id"]))
        embed = suggestion_embed(dict(row), author)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _vote(self, interaction: discord.Interaction, vote: int) -> None:
        user_id = str(interaction.user.id)
        existing = await db.fetchone(
            "SELECT vote FROM suggestion_votes WHERE suggestion_id = ? AND user_id = ?",
            (self.suggestion_id, user_id),
        )
        if existing is not None and int(existing["vote"]) == vote:
            # Segundo pulso sobre el mismo botón: se retira el voto.
            await db.execute(
                "DELETE FROM suggestion_votes WHERE suggestion_id = ? AND user_id = ?",
                (self.suggestion_id, user_id),
            )
            await self._refresh(interaction)
            return

        await db.execute(
            "DELETE FROM suggestion_votes WHERE suggestion_id = ? AND user_id = ?",
            (self.suggestion_id, user_id),
        )
        if existing is None or int(existing["vote"]) != vote:
            await db.execute(
                "INSERT INTO suggestion_votes (suggestion_id, user_id, vote) VALUES (?, ?, ?)",
                (self.suggestion_id, user_id, vote),
            )

        ups = await db.scalar(
            "SELECT COUNT(*) FROM suggestion_votes WHERE suggestion_id = ? AND vote = 1",
            (self.suggestion_id,),
            default=0,
        )
        downs = await db.scalar(
            "SELECT COUNT(*) FROM suggestion_votes WHERE suggestion_id = ? AND vote = -1",
            (self.suggestion_id,),
            default=0,
        )
        await db.execute(
            "UPDATE suggestions SET ups = ?, downs = ? WHERE id = ?",
            (int(ups), int(downs), self.suggestion_id),
        )
        await self._refresh(interaction)

    async def _up(self, interaction: discord.Interaction) -> None:
        await self._vote(interaction, 1)

    async def _down(self, interaction: discord.Interaction) -> None:
        await self._vote(interaction, -1)

    # ------------------------------------------------------------------
    async def _set_status(self, interaction: discord.Interaction, status: str) -> None:
        cfg = await db.guild_config(interaction.guild_id)
        if not await ensure_staff_interaction(interaction, cfg):
            return
        row = await db.fetchone(
            "SELECT * FROM suggestions WHERE id = ? AND guild_id = ?",
            (self.suggestion_id, str(interaction.guild_id)),
        )
        if row is None:
            await interaction.response.send_message("Esta sugerencia ya no existe.", ephemeral=True)
            return
        if str(row["status"]) != "pendiente":
            await interaction.response.send_message(
                f"Ya fue revisada ({row['status']}).", ephemeral=True
            )
            return

        await db.execute(
            "UPDATE suggestions SET status = ?, handled_by = ?, updated_at = datetime('now') "
            "WHERE id = ? AND guild_id = ?",
            (status, str(interaction.user.id), self.suggestion_id, str(interaction.guild_id)),
        )
        data = dict(row)
        data["status"] = status
        author = interaction.client.get_user(int(row["user_id"]))
        embed = suggestion_embed(data, author)
        embed.color = discord.Color.green() if status == "aprobada" else discord.Color.red()
        await interaction.response.edit_message(embed=embed, view=self)

        await log_action(
            interaction.guild,
            "sugerencia_revisada",
            cfg=cfg,
            embed=success_embed(
                "Sugerencia revisada",
                f"**#{self.suggestion_id}** → **{status}** por {interaction.user.mention}.",
            ),
            user_id=interaction.user.id,
            target_id=row["user_id"],
        )
        if (cfg.get("suggestions") or {}).get("dm_on_change", True):
            await send_template_dm(
                interaction.client,
                interaction.guild,
                int(row["user_id"]),
                "report_update",
                fallback="Tu sugerencia #{id} cambió a {status}.",
                id=self.suggestion_id,
                status=status,
            )

    async def _approve(self, interaction: discord.Interaction) -> None:
        await self._set_status(interaction, "aprobada")

    async def _reject(self, interaction: discord.Interaction) -> None:
        await self._set_status(interaction, "rechazada")


class Sugerencias(commands.Cog):
    """Panel de sugerencias con votación y estados."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def register_views(self) -> None:
        rows = await db.fetchall(
            "SELECT id FROM suggestions WHERE status = 'pendiente' ORDER BY id DESC LIMIT 150"
        )
        for row in rows:
            self.bot.add_view(SuggestionView(int(row["id"])))

    async def publish(self, interaction: discord.Interaction, content: str) -> None:
        cfg = await db.guild_config(interaction.guild_id)
        channel_id = (cfg.get("channels") or {}).get("suggestions")
        if not channel_id:
            await interaction.response.send_message(
                embed=error_embed(
                    "Canal sin configurar",
                    "El staff debe configurar el canal de sugerencias con `/setup suggestions`.",
                ),
                ephemeral=True,
            )
            return
        channel = interaction.guild.get_channel(int(channel_id)) if interaction.guild else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed("Canal no encontrado", "El canal de sugerencias ya no existe."),
                ephemeral=True,
            )
            return

        text = require_text(content, "La sugerencia", 10, 900)
        cursor = await db.execute(
            "INSERT INTO suggestions (guild_id, user_id, content, channel_id) VALUES (?, ?, ?, ?)",
            (str(interaction.guild_id), str(interaction.user.id), text, str(channel.id)),
        )
        suggestion_id = int(cursor.lastrowid or 0)
        data = {
            "id": suggestion_id,
            "user_id": str(interaction.user.id),
            "content": text,
            "status": "pendiente",
            "ups": 0,
            "downs": 0,
        }
        message = await channel.send(
            embed=suggestion_embed(data, interaction.user),
            view=SuggestionView(suggestion_id),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )
        await db.execute(
            "UPDATE suggestions SET message_id = ? WHERE id = ?", (str(message.id), suggestion_id)
        )
        await log_action(
            interaction.guild,
            "sugerencia_recibida",
            cfg=cfg,
            user_id=interaction.user.id,
            content=f"#{suggestion_id}: {text[:300]}",
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Sugerencia enviada",
                f"Se publicó con el identificador **#{suggestion_id}**. Vota con 👍/👎.",
            ),
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    @commands.hybrid_group(name="suggest", description="Sugerencias de la comunidad.", invoke_without_command=True)
    @commands.guild_only()
    async def suggest(self, ctx: commands.Context) -> None:
        await ctx.send(
            embed=base_embed(
                "💡 Sugerencias",
                "Usa `/suggest send <texto>` para proponer algo o `/suggest list` para verlas.",
            )
        )

    @suggest.command(name="send", description="Envía una sugerencia al panel.")
    @commands.guild_only()
    async def suggest_send(self, ctx: commands.Context, texto: str) -> None:
        if isinstance(ctx.interaction, discord.Interaction):
            await self.publish(ctx.interaction, texto)
            return
        try:
            text = require_text(texto, "La sugerencia", 10, 900)
        except Exception as exc:
            await ctx.send(embed=warn_embed("Sugerencia no válida", str(exc)))
            return
        await self._publish_from_context(ctx, text)

    async def _publish_from_context(self, ctx: commands.Context, text: str) -> None:
        cfg = await db.guild_config(ctx.guild.id)
        channel_id = (cfg.get("channels") or {}).get("suggestions")
        if not channel_id:
            await ctx.send(embed=error_embed("Canal sin configurar", "Configura `/setup suggestions`."))
            return
        channel = ctx.guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            await ctx.send(embed=error_embed("Canal no encontrado", "El canal ya no existe."))
            return
        cursor = await db.execute(
            "INSERT INTO suggestions (guild_id, user_id, content, channel_id) VALUES (?, ?, ?, ?)",
            (str(ctx.guild.id), str(ctx.author.id), text, str(channel.id)),
        )
        suggestion_id = int(cursor.lastrowid or 0)
        data = {
            "id": suggestion_id,
            "user_id": str(ctx.author.id),
            "content": text,
            "status": "pendiente",
            "ups": 0,
            "downs": 0,
        }
        message = await channel.send(
            embed=suggestion_embed(data, ctx.author), view=SuggestionView(suggestion_id)
        )
        await db.execute(
            "UPDATE suggestions SET message_id = ? WHERE id = ?", (str(message.id), suggestion_id)
        )
        await ctx.send(embed=success_embed("Sugerencia enviada", f"Publicada con el identificador **#{suggestion_id}**."))

    @suggest.command(name="list", description="Lista las sugerencias por estado.")
    @commands.guild_only()
    async def suggest_list(self, ctx: commands.Context, estado: Optional[str] = None) -> None:
        state = (estado or "pendiente").strip().lower()
        if state not in STATUSES:
            await ctx.send(
                embed=warn_embed("Estado no válido", f"Estados: {', '.join(STATUSES)}.")
            )
            return
        rows = await db.fetchall(
            "SELECT * FROM suggestions WHERE guild_id = ? AND status = ? ORDER BY id DESC LIMIT 40",
            (str(ctx.guild.id), state),
        )
        if not rows:
            await ctx.send(embed=base_embed("💡 Sugerencias", f"No hay sugerencias **{state}**."))
            return
        lines = [
            f"**#{row['id']}** {clean_text(row['content'], 90)} · 👍{row['ups']} 👎{row['downs']}"
            for row in rows
        ]
        embeds = [
            base_embed(f"💡 Sugerencias · {state}", "\n".join(lines[start : start + 15]))
            for start in range(0, len(lines), 15)
        ]
        for page, embed in enumerate(embeds, start=1):
            embed.set_footer(text=f"Página {page}/{len(embeds)}")
        await Paginator(embeds, user_id=ctx.author.id).start(ctx)

    @suggest.command(name="history", description="Muestra tus sugerencias y su estado.")
    @commands.guild_only()
    async def suggest_history(self, ctx: commands.Context) -> None:
        rows = await db.fetchall(
            "SELECT * FROM suggestions WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT 10",
            (str(ctx.guild.id), str(ctx.author.id)),
        )
        if not rows:
            await ctx.send(embed=base_embed("🗂️ Tus sugerencias", "Todavía no has enviado ninguna."))
            return
        lines = [
            f"**#{row['id']}** · {row['status']} · {clean_text(row['content'], 90)}" for row in rows
        ]
        await ctx.send(embed=base_embed("🗂️ Tus sugerencias", "\n".join(lines)))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Sugerencias(bot))
