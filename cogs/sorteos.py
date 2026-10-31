"""Sorteos: participación con botón, cierre programado, reroll y listado."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from services.embed_factory import giveaway_embed
from services.giveaway_runner import end_giveaway, list_active, participants, reroll, refresh
from services.notification import log_action
from utils.checks import staff_only
from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.helpers import ConfirmView, Paginator
from utils.timeformat import discord_timestamp, from_sql, parse_duration, to_sql
from utils.validators import clean_text, parse_int

log = get_logger("sorteos_cog")


class GiveawayView(discord.ui.View):
    """Botón persistente «Participar» de un sorteo."""

    def __init__(self, giveaway_id: int) -> None:
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        button = discord.ui.Button(
            label="Participar",
            emoji="🎉",
            style=discord.ButtonStyle.success,
            custom_id=f"giveaway:{giveaway_id}:join",
        )
        button.callback = self._join
        self.add_item(button)

    async def _join(self, interaction: discord.Interaction) -> None:
        giveaway = await db.fetchone(
            "SELECT * FROM giveaways WHERE id = ? AND guild_id = ?",
            (self.giveaway_id, str(interaction.guild_id)),
        )
        if giveaway is None or str(giveaway["status"]) != "activa":
            await interaction.response.send_message(
                "Este sorteo ya finalizó.", ephemeral=True
            )
            return

        user_id = str(interaction.user.id)
        existing = await db.fetchone(
            "SELECT 1 FROM giveaway_participants WHERE giveaway_id = ? AND user_id = ?",
            (self.giveaway_id, user_id),
        )
        if existing is not None:
            await db.execute(
                "DELETE FROM giveaway_participants WHERE giveaway_id = ? AND user_id = ?",
                (self.giveaway_id, user_id),
            )
            await interaction.response.send_message(
                "✋ Saliste del sorteo. Pulsa de nuevo para volver a participar.", ephemeral=True
            )
        else:
            await db.execute(
                "INSERT INTO giveaway_participants (giveaway_id, user_id) VALUES (?, ?)",
                (self.giveaway_id, user_id),
            )
            await interaction.response.send_message(
                "🎉 ¡Estás participando! Te avisaremos si ganas.", ephemeral=True
            )
        await refresh(interaction.client, self.giveaway_id)


class Sorteos(commands.Cog):
    """Creación y gestión de sorteos."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def register_views(self) -> None:
        rows = await db.fetchall(
            "SELECT id FROM giveaways WHERE status = 'activa' ORDER BY id DESC LIMIT 100"
        )
        for row in rows:
            self.bot.add_view(GiveawayView(int(row["id"])))

    # ------------------------------------------------------------------
    @commands.hybrid_group(name="giveaway", description="Sorteos del servidor.", invoke_without_command=True)
    @commands.guild_only()
    async def giveaway(self, ctx: commands.Context) -> None:
        rows = await list_active(ctx.guild.id)
        if not rows:
            await ctx.send(embed=base_embed("🎉 Sorteos", "No hay sorteos activos. Usa `/giveaway create`."))
            return
        lines = [
            f"**#{row['id']}** {clean_text(row['prize'], 60)} · "
            f"termina {discord_timestamp(from_sql(row['ends_at']), 'R')}"
            for row in rows
        ]
        await ctx.send(embed=base_embed("🎉 Sorteos activos", "\n".join(lines)))

    @giveaway.command(name="create", description="Crea un sorteo con botón de participación.")
    @commands.guild_only()
    async def giveaway_create(
        self,
        ctx: commands.Context,
        premio: str,
        ganadores: int = 1,
        duracion: str = "10m",
        canal: Optional[discord.TextChannel] = None,
    ) -> None:
        cfg = await db.guild_config(ctx.guild.id)
        prize = clean_text(premio, 200)
        if not prize:
            await ctx.send(embed=error_embed("Premio vacío", "Indica un premio para el sorteo."))
            return
        winners = parse_int(ganadores, "El número de ganadores", minimum=1, maximum=20)
        seconds = parse_duration(duracion)
        if seconds is None:
            await ctx.send(
                embed=error_embed(
                    "Duración no válida",
                    "Usa formatos como `10m`, `2h` o `1d`.",
                )
            )
            return
        giveaways_cfg = cfg.get("giveaways") or {}
        minimum = int(giveaways_cfg.get("min_duration_minutes", 1)) * 60
        maximum = int(giveaways_cfg.get("max_duration_days", 30)) * 86400
        seconds = max(minimum, min(maximum, seconds))
        ends_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)

        destination = canal or ctx.channel
        if not isinstance(destination, discord.TextChannel):
            await ctx.send(embed=error_embed("Canal no válido", "Especifica un canal de texto."))
            return

        cursor = await db.execute(
            "INSERT INTO giveaways (guild_id, channel_id, prize, winners_count, ends_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(ctx.guild.id),
                str(destination.id),
                prize,
                winners,
                to_sql(ends_at),
                str(ctx.author.id),
            ),
        )
        giveaway_id = int(cursor.lastrowid or 0)
        embed = giveaway_embed(prize, winners, ends_at, 0, ctx.author.id)
        embed.add_field(
            name="Organizador", value=ctx.author.mention, inline=True
        )
        message = await destination.send(
            embed=embed, view=GiveawayView(giveaway_id)
        )
        await db.execute(
            "UPDATE giveaways SET message_id = ? WHERE id = ?", (str(message.id), giveaway_id)
        )
        await log_action(
            ctx.guild,
            "sorteo_creado",
            cfg=cfg,
            user_id=ctx.author.id,
            channel_id=destination.id,
            content=f"#{giveaway_id}: {prize} ({winners} ganador(es))",
        )
        await ctx.send(
            embed=success_embed(
                "Sorteo creado",
                f"ID `#{giveaway_id}` en {destination.mention}.\n"
                f"Termina {discord_timestamp(ends_at, 'R')}.",
            ),
            ephemeral=True,
        )

    @giveaway.command(name="end", description="[Staff] Finaliza un sorteo y elige ganadores.")
    @staff_only()
    async def giveaway_end(self, ctx: commands.Context, id_sorteo: int) -> None:
        giveaway = await db.fetchone(
            "SELECT * FROM giveaways WHERE id = ? AND guild_id = ?",
            (int(id_sorteo), str(ctx.guild.id)),
        )
        if giveaway is None or str(giveaway["status"]) != "activa":
            await ctx.send(
                embed=error_embed("Sorteo no encontrado", f"No hay sorteo activo `#{id_sorteo}`.")
            )
            return

        async def _confirm(interaction: discord.Interaction) -> None:
            winners = await end_giveaway(interaction.client, int(id_sorteo))
            if winners is None:
                await interaction.response.edit_message(
                    embed=error_embed("Sorteo no disponible", "El sorteo ya había finalizado."),
                    view=None,
                )
                return
            text = (
                "🎉 **Sorteo finalizado**\n"
                + (", ".join(f"<@{uid}>" for uid in winners) if winners else "Sin participantes: no hay ganador.")
            )
            await interaction.response.edit_message(
                embed=success_embed("Sorteo finalizado", text), view=None
            )

        pool_size = len(await participants(int(id_sorteo)))
        view = ConfirmView(
            user_id=ctx.author.id,
            on_confirm=_confirm,
            confirm_label="Finalizar sorteo",
        )
        message = await ctx.send(
            embed=warn_embed(
                "¿Finalizar sorteo?",
                f"Vas a cerrar **#{id_sorteo}** ({clean_text(giveaway['prize'], 80)}) "
                f"y sortear entre {pool_size} participante(s).",
            ),
            view=view,
        )
        view.message = message

    @giveaway.command(name="reroll", description="[Staff] Vuelve a sortear los ganadores.")
    @staff_only()
    async def giveaway_reroll(self, ctx: commands.Context, id_sorteo: int) -> None:
        winners = await reroll(ctx.bot, int(id_sorteo))
        if winners is None:
            await ctx.send(embed=error_embed("Sorteo no encontrado", f"No existe `#{id_sorteo}`."))
            return
        text = ", ".join(f"<@{uid}>" for uid in winners) if winners else "Sin participantes válidos."
        await ctx.send(embed=success_embed("Nuevo sorteo", f"Ganadores: {text}"))

    @giveaway.command(name="info", description="Muestra el detalle de un sorteo.")
    @commands.guild_only()
    async def giveaway_info(self, ctx: commands.Context, id_sorteo: int) -> None:
        giveaway = await db.fetchone(
            "SELECT * FROM giveaways WHERE id = ? AND guild_id = ?",
            (int(id_sorteo), str(ctx.guild.id)),
        )
        if giveaway is None:
            await ctx.send(embed=error_embed("Sorteo no encontrado", f"No existe `#{id_sorteo}`."))
            return
        pool = await participants(int(id_sorteo))
        embed = giveaway_embed(
            str(giveaway["prize"]),
            int(giveaway["winners_count"]),
            from_sql(giveaway.get("ends_at")),
            len(pool),
            giveaway.get("created_by"),
            ended=str(giveaway["status"]) != "activa",
        )
        embed.add_field(name="Estado", value=str(giveaway["status"]).title(), inline=True)
        embed.add_field(name="ID", value=f"`#{giveaway['id']}`", inline=True)
        if giveaway.get("winner_ids"):
            try:
                winners = json.loads(giveaway["winner_ids"])
            except (ValueError, TypeError):
                winners = []
            if winners:
                embed.add_field(
                    name="Ganadores",
                    value=", ".join(f"<@{uid}>" for uid in winners),
                    inline=False,
                )
        await ctx.send(embed=embed)

    @giveaway.command(name="list", description="[Staff] Lista todos los sorteos.")
    @staff_only()
    async def giveaway_list(self, ctx: commands.Context) -> None:
        rows = await db.fetchall(
            "SELECT id, prize, status, ends_at FROM giveaways WHERE guild_id = ? ORDER BY id DESC LIMIT 30",
            (str(ctx.guild.id),),
        )
        if not rows:
            await ctx.send(embed=base_embed("🎉 Sorteos", "Todavía no hay sorteos."))
            return
        lines = [
            f"**#{row['id']}** · {row['status']} · {clean_text(row['prize'], 60)}" for row in rows
        ]
        embeds = [
            base_embed("🎉 Sorteos", "\n".join(lines[start : start + 15]))
            for start in range(0, len(lines), 15)
        ]
        for page, embed in enumerate(embeds, start=1):
            embed.set_footer(text=f"Página {page}/{len(embeds)}")
        await Paginator(embeds, user_id=ctx.author.id).start(ctx)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Sorteos(bot))
