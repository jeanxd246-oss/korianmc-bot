"""Puntos de actividad: economía ligera con anti-spam, premios y top."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Optional

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from utils.checks import staff_only
from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.helpers import Paginator
from utils.validators import parse_int

log = get_logger("puntos")


class Points(commands.Cog):
    """Puntos por actividad con protección anti-spam y transferencias."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._cooldown: dict[tuple[int, int], float] = {}
        self._recent: dict[tuple[int, int], deque[str]] = defaultdict(lambda: deque(maxlen=5))
        self._flood: dict[tuple[int, int], list[float]] = defaultdict(list)
        self._recent_gain: dict[tuple[int, int], list[int]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Ganancia de puntos
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return

        content = (message.content or "").strip()
        if not content:
            return

        cfg = await db.guild_config(message.guild.id)
        points_cfg = cfg.get("points") or {}
        if not points_cfg.get("enabled", True):
            return

        key = (message.guild.id, message.author.id)
        now = time.monotonic()

        cooldown = float(points_cfg.get("cooldown_seconds", 45))
        if now - self._cooldown.get(key, 0.0) < cooldown:
            return

        if len(content) < int(points_cfg.get("min_chars", 5)):
            return

        normalized = " ".join(content.lower().split())
        if normalized in self._recent[key]:
            return

        stamps = [stamp for stamp in self._flood[key] if now - stamp < 10]
        stamps.append(now)
        self._flood[key] = stamps
        if len(stamps) > 6:
            return

        self._cooldown[key] = now
        self._recent[key].append(normalized)

        amount = int(points_cfg.get("per_message", 2))
        if len(content) >= int(points_cfg.get("long_chars", 250)):
            amount += int(points_cfg.get("bonus_long", 3))
        amount = self._diminishing(key, amount, now)
        if amount <= 0:
            return

        await db.add_points(message.guild.id, message.author.id, amount, "actividad en el servidor")

    def _diminishing(self, key: tuple[int, int], amount: int, now: float) -> int:
        """Menos puntos si el usuario encadena mensajes sin pausa."""
        recent = [stamp for stamp in self._flood[key] if now - stamp < 45]
        if len(recent) <= 3:
            return amount
        if len(recent) <= 5:
            return max(1, amount // 2)
        return 1

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------
    @commands.hybrid_group(name="points", description="Sistema de puntos: consulta, top y transferencias.", invoke_without_command=True)
    @commands.guild_only()
    async def points(self, ctx: commands.Context) -> None:
        await self.points_view(ctx, None)

    @points.command(name="view", description="Muestra tus puntos o los de otro usuario.")
    async def points_view(self, ctx: commands.Context, usuario: Optional[discord.Member] = None) -> None:
        target = usuario or ctx.author
        data = await db.ensure_user(ctx.guild.id, target.id)
        transactions = await db.fetchall(
            "SELECT amount, reason, created_at FROM points_transactions "
            "WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT 5",
            (str(ctx.guild.id), str(target.id)),
        )
        embed = base_embed(f"🪙 Puntos de {target.display_name}")
        embed.set_thumbnail(url=target.display_avatar.with_size(256).url)
        embed.add_field(name="Puntos", value=f"**{data.get('points', 0)}**", inline=True)
        embed.add_field(name="Nivel", value=f"**{data.get('level', 0)}**", inline=True)
        embed.add_field(name="Mensajes", value=f"**{data.get('messages', 0)}**", inline=True)
        if transactions:
            lines = [
                f"{'➕' if row['amount'] >= 0 else '➖'} {abs(row['amount'])} — {row['reason']}"
                for row in transactions
            ]
            embed.add_field(name="Últimos movimientos", value="\n".join(lines)[:1000], inline=False)
        await ctx.send(embed=embed)

    @points.command(name="top", description="Top de puntos del servidor.")
    async def points_top(self, ctx: commands.Context) -> None:
        rows = await db.fetchall(
            "SELECT user_id, points FROM users WHERE guild_id = ? AND points > 0 "
            "ORDER BY points DESC LIMIT 60",
            (str(ctx.guild.id),),
        )
        if not rows:
            await ctx.send(embed=base_embed("🏆 Top de puntos", "Nadie tiene puntos todavía."))
            return
        entries: list[tuple[Any, int]] = []
        for row in rows:
            user = ctx.guild.get_member(int(row["user_id"])) or self.bot.get_user(int(row["user_id"]))
            entries.append((user or row["user_id"], int(row["points"])))
        medals = ["🥇", "🥈", "🥉"]
        lines = [
            f"{medals[index] if index < 3 else f'`#{index + 1}`'} **{getattr(user, 'display_name', None) or user}** — {points} pts"
            for index, (user, points) in enumerate(entries)
        ]
        embeds = [
            base_embed("🏆 Top de puntos", "\n".join(lines[start : start + 15]))
            for start in range(0, len(lines), 15)
        ]
        for page, embed in enumerate(embeds, start=1):
            embed.set_footer(text=f"Página {page}/{len(embeds)}")
        await Paginator(embeds, user_id=ctx.author.id).start(ctx)

    @points.command(name="pay", description="Transfiere puntos a otro usuario.")
    @commands.guild_only()
    async def points_pay(
        self,
        ctx: commands.Context,
        usuario: discord.Member,
        cantidad: str,
        motivo: Optional[str] = None,
    ) -> None:
        if usuario.id == ctx.author.id:
            await ctx.send(embed=error_embed("No permitido", "No puedes transferirte puntos a ti mismo."))
            return
        if usuario.bot:
            await ctx.send(embed=error_embed("No permitido", "No se puede pagar a un bot."))
            return
        cfg = await db.guild_config(ctx.guild.id)
        limit = int((cfg.get("points") or {}).get("pay_limit", 5000))
        try:
            amount = parse_int(cantidad, "La cantidad", minimum=1, maximum=limit)
        except Exception as exc:
            await ctx.send(embed=warn_embed("Cantidad no válida", str(exc)))
            return

        sender = await db.ensure_user(ctx.guild.id, ctx.author.id)
        if int(sender.get("points", 0)) < amount:
            await ctx.send(
                embed=error_embed(
                    "Puntos insuficientes",
                    f"Tienes **{sender.get('points', 0)}** puntos y quieres enviar **{amount}**.",
                )
            )
            return

        reason = (motivo or f"Transferencia de {ctx.author}").strip()[:120]
        await db.add_points(ctx.guild.id, ctx.author.id, -amount, f"pago a {usuario}: {reason}", ctx.author.id)
        new_balance = await db.add_points(
            ctx.guild.id, usuario.id, amount, f"pago de {ctx.author}: {reason}", ctx.author.id
        )
        embed = success_embed(
            "Transferencia realizada",
            f"**{ctx.author.mention}** pagó **{amount}** puntos a **{usuario.mention}**.\n"
            f"**Motivo:** {reason}\n**Saldo de {usuario.display_name}:** {new_balance}",
        )
        await ctx.send(embed=embed)

    @points.command(name="add", description="[Staff] Otorga puntos a un usuario.")
    @staff_only()
    async def points_add(
        self,
        ctx: commands.Context,
        usuario: discord.Member,
        cantidad: int,
        motivo: Optional[str] = None,
    ) -> None:
        try:
            amount = parse_int(cantidad, "La cantidad", minimum=1, maximum=1_000_000)
        except Exception as exc:
            await ctx.send(embed=warn_embed("Cantidad no válida", str(exc)))
            return
        new_balance = await db.add_points(
            ctx.guild.id,
            usuario.id,
            amount,
            (motivo or "premio del staff").strip()[:120],
            ctx.author.id,
        )
        await ctx.send(
            embed=success_embed(
                "Puntos añadidos",
                f"**{usuario.mention}** recibió **{amount}** puntos.\n**Saldo:** {new_balance}",
            )
        )

    @points.command(name="remove", description="[Staff] Quita puntos a un usuario.")
    @staff_only()
    async def points_remove(
        self,
        ctx: commands.Context,
        usuario: discord.Member,
        cantidad: int,
        motivo: Optional[str] = None,
    ) -> None:
        try:
            amount = parse_int(cantidad, "La cantidad", minimum=1, maximum=1_000_000)
        except Exception as exc:
            await ctx.send(embed=warn_embed("Cantidad no válida", str(exc)))
            return
        new_balance = await db.add_points(
            ctx.guild.id,
            usuario.id,
            -amount,
            (motivo or "sanción del staff").strip()[:120],
            ctx.author.id,
        )
        await ctx.send(
            embed=success_embed(
                "Puntos retirados",
                f"Se quitaron **{amount}** puntos a **{usuario.mention}**.\n**Saldo:** {new_balance}",
            )
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Points(bot))
