"""Sistema de niveles: XP por mensaje, 10 niveles con roles y anuncios."""

from __future__ import annotations

import random
import time
from collections import defaultdict, deque
from typing import Any, Optional

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from services.embed_factory import level_card_embed, level_up_embed, xp_needed
from services.notification import log_action
from utils.embeds import base_embed
from utils.helpers import Paginator
from utils.permissions import bot_can_manage_roles, channel_id, role_ids

log = get_logger("niveles")

MAX_LEVEL = 10
LEVEL_ROLE_NAMES = [
    "Level 1", "Level 2", "Level 3", "Level 4", "Level 5",
    "Level 6", "Level 7", "Level 8", "Level 9", "Level Demon",
]
LEVEL_ROLE_COLORS = [
    0x95A5A6, 0x1ABC9C, 0x2ECC71, 0x3498DB, 0x9B59B6,
    0xF1C40F, 0xE67E22, 0xE74C3C, 0xE91E63, 0x5865F2,
]


class Levels(commands.Cog):
    """Progreso por XP con roles automáticos por nivel."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._cooldown: dict[tuple[int, int], float] = {}
        self._recent: dict[tuple[int, int], deque[str]] = defaultdict(lambda: deque(maxlen=6))
        self._flood: dict[tuple[int, int], list[float]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Concesión de XP
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return

        cfg = await db.guild_config(message.guild.id)
        levels_cfg = cfg.get("levels") or {}
        if not levels_cfg.get("enabled", True):
            return

        content = (message.content or "").strip()
        if not content:
            return

        key = (message.guild.id, message.author.id)
        now = time.monotonic()

        # Cooldown entre mensajes
        cooldown = float(levels_cfg.get("cooldown_seconds", 60))
        if now - self._cooldown.get(key, 0.0) < cooldown:
            return

        # Mensajes demasiado cortos
        if len(content) < int(levels_cfg.get("min_chars", 5)):
            return

        # Mensajes repetidos (flood)
        normalized = " ".join(content.lower().split())
        history = self._recent[key]
        if normalized in history:
            return

        # Flood: muchos mensajes en poco tiempo
        stamps = [stamp for stamp in self._flood[key] if now - stamp < 8]
        stamps.append(now)
        self._flood[key] = stamps
        if len(stamps) > int(levels_cfg.get("flood_limit", 5)):
            return

        self._cooldown[key] = now
        history.append(normalized)

        # XP: base aleatoria + bonificación por mensajes largos y coherentes
        amount = random.randint(int(levels_cfg.get("xp_min", 15)), int(levels_cfg.get("xp_max", 35)))
        if len(content) >= int(levels_cfg.get("long_chars", 300)):
            amount += int(levels_cfg.get("bonus_long", 10))
            amount = int(amount * 1.15)  # mensajes coherentes valen más
        amount = self._diminishing_returns(key, amount, now)

        data = await db.add_xp(message.guild.id, message.author.id, amount)
        old_level = int(data.get("level", 0))
        xp = int(data.get("xp", 0))

        new_level = old_level
        while xp >= xp_needed(new_level) and new_level < MAX_LEVEL:
            xp -= xp_needed(new_level)
            new_level += 1

        if new_level != old_level:
            await db.execute(
                "UPDATE users SET level = ?, xp = ? WHERE guild_id = ? AND user_id = ?",
                (new_level, xp, str(message.guild.id), str(message.author.id)),
            )
            await self._handle_level_up(message.guild, message.author, new_level, xp, cfg)

    def _diminishing_returns(self, key: tuple[int, int], amount: int, now: float) -> int:
        """Reduce la ganancia si el usuario lleva poco tiempo ganando XP."""
        stamps = [stamp for stamp in self._flood[key] if now - stamp < 60]
        if len(stamps) <= 2:
            return amount
        return max(3, int(amount * 0.5))

    # ------------------------------------------------------------------
    # Subida de nivel
    # ------------------------------------------------------------------
    async def _handle_level_up(
        self, guild: discord.Guild, member: discord.Member, level: int, xp: int, cfg: dict[str, Any]
    ) -> None:
        role = await self._apply_level_role(guild, member, level, cfg)

        announce_channel = channel_id(cfg, "levels")
        if announce_channel and levels_announce(cfg):
            channel = guild.get_channel(announce_channel)
            if isinstance(channel, discord.abc.Messageable):
                try:
                    await channel.send(
                        embed=level_up_embed(member, level, role, xp, cfg),
                        allowed_mentions=discord.AllowedMentions(users=True, roles=True),
                    )
                except discord.HTTPException:
                    log.warning("No se pudo anunciar la subida de nivel en %s", guild.name)

        await log_action(
            guild,
            "subida_nivel",
            cfg=cfg,
            user_id=member.id,
            content=f"{member} alcanzó el nivel {level}",
        )

    async def _apply_level_role(
        self, guild: discord.Guild, member: discord.Member, level: int, cfg: dict[str, Any]
    ) -> Optional[discord.Role]:
        roles = await db.fetchall(
            "SELECT level, role_id FROM level_roles WHERE guild_id = ?", (str(guild.id),)
        )
        mapping: dict[int, int] = {}
        for row in roles:
            try:
                mapping[int(row["level"])] = int(row["role_id"])
            except (TypeError, ValueError):
                continue
        if not mapping:
            mapping = {
                index + 1: int(role_id)
                for index, role_id in enumerate(role_ids(cfg, "level"))
                if str(role_id).isdigit() and role_id
            }

        target_id = mapping.get(level)
        target_role = guild.get_role(target_id) if target_id else None

        to_remove = [
            guild.get_role(role_id)
            for configured_level, role_id in mapping.items()
            if configured_level <= MAX_LEVEL and guild.get_role(role_id) is not None and configured_level != level
        ]
        to_remove = [role for role in to_remove if role is not None]
        if to_remove and bot_can_manage_roles(guild, to_remove[0]):
            try:
                await member.remove_roles(*to_remove, reason=f"Subida al nivel {level}")
            except discord.HTTPException:
                log.warning("No se pudieron quitar los roles de nivel a %s", member)

        if target_role is not None and bot_can_manage_roles(guild, target_role):
            try:
                await member.add_roles(target_role, reason=f"Nivel {level} alcanzado")
                return target_role
            except discord.HTTPException:
                log.warning("No se pudo asignar %s a %s", target_role.name, member)
        return None

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------
    @commands.hybrid_group(name="level", description="Sistema de niveles: progreso y top.", invoke_without_command=True)
    @commands.guild_only()
    async def level(self, ctx: commands.Context) -> None:
        """Sin subcomando: muestra tu propia ficha de nivel."""
        await self._render_level(ctx, None)

    @level.command(name="view", description="Muestra la ficha de nivel de un usuario.")
    async def level_view(self, ctx: commands.Context, usuario: Optional[discord.Member] = None) -> None:
        await self._render_level(ctx, usuario)

    async def _render_level(self, ctx: commands.Context, usuario: Optional[discord.Member]) -> None:
        target = usuario or ctx.author
        data = await db.ensure_user(ctx.guild.id, target.id)
        embed = level_card_embed(target, data, await db.guild_config(ctx.guild.id))
        await ctx.send(embed=embed)

    @level.command(name="top", description="Top de niveles del servidor.")
    async def level_top(self, ctx: commands.Context) -> None:
        rows = await db.fetchall(
            "SELECT user_id, xp, level, messages FROM users WHERE guild_id = ? "
            "ORDER BY level DESC, xp DESC LIMIT 60",
            (str(ctx.guild.id),),
        )
        if not rows:
            await ctx.send(embed=base_embed("📊 Top de niveles", "Todavía no hay progreso registrado."))
            return
        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for index, row in enumerate(rows):
            prefix = medals[index] if index < 3 else f"`#{index + 1}`"
            lines.append(
                f"{prefix} <@{row['user_id']}> — **Nivel {row['level']}** · {row['xp']} XP"
            )
        embeds = [
            base_embed("📊 Top de niveles", "\n".join(lines[start : start + 15]))
            for start in range(0, len(lines), 15)
        ]
        for page, embed in enumerate(embeds, start=1):
            embed.set_footer(text=f"Página {page}/{len(embeds)}")
        paginator = Paginator(embeds, user_id=ctx.author.id)
        await paginator.start(ctx)


def levels_announce(cfg: dict[str, Any]) -> bool:
    return bool((cfg.get("levels") or {}).get("announce_level_up", True))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Levels(bot))
