"""Encuestas con botones, resultados en vivo y cierre automático programado."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from services.embed_factory import poll_embed
from services.notification import log_action
from utils.checks import staff_only
from utils.embeds import base_embed, error_embed, success_embed
from utils.helpers import Paginator
from utils.timeformat import discord_timestamp, parse_duration, to_sql, utcnow
from utils.validators import clean_text

log = get_logger("encuestas")

MAX_OPTIONS = 5


async def _tally(poll_id: int, option_count: int) -> list[int]:
    rows = await db.fetchall(
        "SELECT option_index, COUNT(*) AS total FROM poll_votes "
        "WHERE poll_id = ? GROUP BY option_index",
        (poll_id,),
    )
    counts = [0] * option_count
    for row in rows:
        index = int(row["option_index"])
        if 0 <= index < option_count:
            counts[index] = int(row["total"])
    return counts


class PollView(discord.ui.View):
    """Un botón por opción; los resultados se recalculan en cada voto."""

    def __init__(self, poll_id: int, options: list[str]) -> None:
        super().__init__(timeout=None)
        self.poll_id = poll_id
        self.options = options
        for index, option in enumerate(options[:MAX_OPTIONS]):
            button = discord.ui.Button(
                label=clean_text(option, 80),
                emoji=f"{index + 1}️⃣",
                style=discord.ButtonStyle.primary,
                custom_id=f"poll:{poll_id}:opt:{index}",
            )
            button.callback = self._make_callback(index)
            self.add_item(button)

    def _make_callback(self, index: int):
        async def callback(interaction: discord.Interaction) -> None:
            await self._vote(interaction, index)

        return callback

    async def _vote(self, interaction: discord.Interaction, index: int) -> None:
        poll = await db.fetchone(
            "SELECT * FROM polls WHERE id = ? AND guild_id = ?",
            (self.poll_id, str(interaction.guild_id)),
        )
        if poll is None:
            await interaction.response.send_message("Esta encuesta ya no existe.", ephemeral=True)
            return
        if str(poll["status"]) != "abierta":
            await interaction.response.send_message("La encuesta está cerrada.", ephemeral=True)
            return

        user_id = str(interaction.user.id)
        existing = await db.fetchone(
            "SELECT option_index FROM poll_votes WHERE poll_id = ? AND user_id = ?",
            (self.poll_id, user_id),
        )
        if existing is not None and int(existing["option_index"]) == index:
            await db.execute(
                "DELETE FROM poll_votes WHERE poll_id = ? AND user_id = ?",
                (self.poll_id, user_id),
            )
            action = "voto retirado"
        else:
            await db.execute(
                "DELETE FROM poll_votes WHERE poll_id = ? AND user_id = ?",
                (self.poll_id, user_id),
            )
            await db.execute(
                "INSERT INTO poll_votes (poll_id, user_id, option_index) VALUES (?, ?, ?)",
                (self.poll_id, user_id, index),
            )
            action = f"voto registrado: {clean_text(self.options[index], 40)}"

        counts = await _tally(self.poll_id, len(self.options))
        embed = poll_embed(
            str(poll["question"]),
            self.options,
            counts,
            from_sql(poll.get("ends_at")),
            closed=False,
        )
        embed.set_footer(text=f"{interaction.user.display_name}: {action}")
        if interaction.response.is_done():
            await interaction.followup.edit_message(
                message_id=interaction.message.id, embed=embed, view=self
            )
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        # Las encuestas son persistentes: no se deshabilitan al agotar el tiempo.
        return None


def from_sql(value: Any) -> Optional[datetime]:
    from utils.timeformat import from_sql as parser

    return parser(value)


class Encuestas(commands.Cog):
    """Encuestas con votación por botones y cierre programado."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def register_views(self) -> None:
        rows = await db.fetchall(
            "SELECT id, options FROM polls WHERE status = 'abierta' ORDER BY id DESC LIMIT 50"
        )
        for row in rows:
            try:
                options = list(json.loads(row["options"]))
            except (ValueError, TypeError):
                continue
            if options:
                self.bot.add_view(PollView(int(row["id"]), [str(option) for option in options]))

    # ------------------------------------------------------------------
    async def close_poll(self, guild_id: Any, poll_id: int, closed_by: Any = None) -> bool:
        poll = await db.fetchone(
            "SELECT * FROM polls WHERE id = ? AND guild_id = ?", (poll_id, str(guild_id))
        )
        if poll is None or str(poll["status"]) != "abierta":
            return False
        await db.execute(
            "UPDATE polls SET status = 'cerrada', closed_at = ? WHERE id = ?",
            (utcnow(), poll_id),
        )
        try:
            options = [str(item) for item in json.loads(poll["options"])]
        except (ValueError, TypeError):
            options = []
        counts = await _tally(poll_id, len(options))
        embed = poll_embed(str(poll["question"]), options, counts, None, closed=True)
        embed.add_field(
            name="Total de votos", value=str(sum(counts)), inline=True
        )
        await self._edit_poll_message(poll, embed)
        return True

    async def _edit_poll_message(self, poll: dict[str, Any], embed: discord.Embed) -> None:
        guild = self.bot.get_guild(int(poll["guild_id"]))
        if guild is None:
            return
        channel = guild.get_channel(int(poll["channel_id"] or 0))
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(int(poll["message_id"]))
            await message.edit(embed=embed, view=None)
        except (discord.NotFound, discord.HTTPException, discord.Forbidden) as exc:
            log.warning("No se pudo cerrar la encuesta #%s: %s", poll["id"], exc)

    # ------------------------------------------------------------------
    @commands.hybrid_group(name="poll", description="Encuestas con botones y resultados en vivo.", invoke_without_command=True)
    @commands.guild_only()
    async def poll(self, ctx: commands.Context) -> None:
        rows = await db.fetchall(
            "SELECT id, question, ends_at FROM polls WHERE guild_id = ? AND status = 'abierta' ORDER BY id DESC LIMIT 10",
            (str(ctx.guild.id),),
        )
        if not rows:
            await ctx.send(embed=base_embed("📊 Encuestas", "No hay encuestas abiertas."))
            return
        lines = [
            f"**#{row['id']}** {clean_text(row['question'], 80)} · cierra {discord_timestamp(from_sql(row['ends_at']), 'R')}"
            for row in rows
        ]
        await ctx.send(embed=base_embed("📊 Encuestas abiertas", "\n".join(lines)))

    @poll.command(name="create", description="Crea una encuesta con hasta 5 opciones.")
    @commands.guild_only()
    async def poll_create(
        self,
        ctx: commands.Context,
        pregunta: str,
        opcion1: str,
        opcion2: str,
        opcion3: Optional[str] = None,
        opcion4: Optional[str] = None,
        opcion5: Optional[str] = None,
        duracion: Optional[str] = None,
    ) -> None:
        options = [
            clean_text(option, 100)
            for option in (opcion1, opcion2, opcion3, opcion4, opcion5)
            if option and clean_text(option, 100)
        ]
        if len(options) < 2:
            await ctx.send(embed=error_embed("Faltan opciones", "Necesitas al menos 2 opciones."))
            return
        if len(set(option.lower() for option in options)) != len(options):
            await ctx.send(embed=error_embed("Opciones duplicadas", "Las opciones deben ser distintas."))
            return

        cfg = await db.guild_config(ctx.guild.id)
        channel_id = (cfg.get("channels") or {}).get("polls") or ctx.channel.id
        channel = ctx.guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            await ctx.send(embed=error_embed("Canal no válido", "El canal de encuestas no existe."))
            return

        seconds = parse_duration(duracion) or 3600
        minimum = 60
        maximum = 30 * 86400
        seconds = max(minimum, min(maximum, seconds))
        ends_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)

        cursor = await db.execute(
            "INSERT INTO polls (guild_id, channel_id, question, options, ends_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(ctx.guild.id),
                str(channel.id),
                clean_text(pregunta, 300),
                json.dumps(options, ensure_ascii=False),
                to_sql(ends_at),
                str(ctx.author.id),
            ),
        )
        poll_id = int(cursor.lastrowid or 0)
        embed = poll_embed(clean_text(pregunta, 300), options, [0] * len(options), ends_at, closed=False)
        embed.add_field(name="Creada por", value=ctx.author.mention, inline=True)
        message = await channel.send(embed=embed, view=PollView(poll_id, options))
        await db.execute(
            "UPDATE polls SET message_id = ? WHERE id = ?", (str(message.id), poll_id)
        )
        await log_action(
            ctx.guild,
            "encuesta_creada",
            cfg=cfg,
            user_id=ctx.author.id,
            channel_id=channel.id,
            content=f"#{poll_id}: {pregunta[:200]}",
        )
        await ctx.send(
            embed=success_embed(
                "Encuesta creada",
                f"ID `#{poll_id}` en {channel.mention}. Cierra {discord_timestamp(ends_at, 'R')}.",
            ),
            ephemeral=True,
        )

    @poll.command(name="end", description="[Staff] Cierra una encuesta y muestra resultados.")
    @staff_only()
    async def poll_end(self, ctx: commands.Context, id_encuesta: int) -> None:
        closed = await self.close_poll(ctx.guild.id, int(id_encuesta), ctx.author.id)
        if not closed:
            await ctx.send(
                embed=error_embed("Encuesta no encontrada", f"No hay encuesta abierta `#{id_encuesta}`.")
            )
            return
        await log_action(
            ctx.guild,
            "encuesta_cerrada",
            user_id=ctx.author.id,
            content=f"#{id_encuesta}",
        )
        await ctx.send(embed=success_embed("Encuesta cerrada", f"Se publicaron los resultados de `#{id_encuesta}`."))

    @poll.command(name="list", description="Lista todas las encuestas.")
    @commands.guild_only()
    async def poll_list(self, ctx: commands.Context) -> None:
        rows = await db.fetchall(
            "SELECT id, question, status, ends_at FROM polls WHERE guild_id = ? ORDER BY id DESC LIMIT 30",
            (str(ctx.guild.id),),
        )
        if not rows:
            await ctx.send(embed=base_embed("📊 Encuestas", "No hay encuestas todavía."))
            return
        lines = [
            f"**#{row['id']}** · {row['status']} · {clean_text(row['question'], 80)}" for row in rows
        ]
        embeds = [
            base_embed("📊 Encuestas", "\n".join(lines[start : start + 15]))
            for start in range(0, len(lines), 15)
        ]
        for page, embed in enumerate(embeds, start=1):
            embed.set_footer(text=f"Página {page}/{len(embeds)}")
        await Paginator(embeds, user_id=ctx.author.id).start(ctx)


async def process_due_polls(bot: commands.Bot) -> int:
    """Cierra automáticamente las encuestas cuyo plazo venció."""
    rows = await db.fetchall(
        "SELECT id, guild_id FROM polls WHERE status = 'abierta' AND ends_at IS NOT NULL AND ends_at <= ?",
        (utcnow(),),
    )
    closed = 0
    cog = bot.get_cog("Encuestas")
    if cog is None:
        return 0
    for row in rows:
        try:
            if await cog.close_poll(int(row["guild_id"]), int(row["id"])):
                closed += 1
        except Exception:
            log.exception("Error al cerrar la encuesta #%s", row["id"])
    return closed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Encuestas(bot))
