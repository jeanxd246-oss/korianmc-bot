"""Bienvenida y despedida: embed con datos del usuario, auto-rol y DM."""

from __future__ import annotations

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from services.embed_factory import leave_embed, welcome_embed
from services.notification import log_action, send_welcome_dm
from utils.permissions import channel_id

log = get_logger("bienvenida")


class Welcome(commands.Cog):
    """Bienvenida, despedida y auto-asignación de rol."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        cfg = await db.guild_config(member.guild.id)

        welcome_channel = channel_id(cfg, "welcome")
        if welcome_channel:
            channel = member.guild.get_channel(welcome_channel)
            if isinstance(channel, discord.abc.Messageable):
                try:
                    await channel.send(embed=welcome_embed(member, cfg))
                except discord.HTTPException:
                    log.warning("No se pudo enviar la bienvenida en %s", member.guild.name)

        # Auto-rol
        autorole_id = (cfg.get("roles") or {}).get("autorole")
        if autorole_id:
            role = member.guild.get_role(int(autorole_id))
            if role is not None and self._bot_can_manage(member.guild, role):
                try:
                    await member.add_roles(role, reason="Auto-rol al entrar al servidor")
                except discord.HTTPException:
                    log.warning("No se pudo asignar el auto-rol a %s", member)

        await log_action(
            member.guild,
            "miembro_entra",
            cfg=cfg,
            user_id=member.id,
            embed=welcome_embed(member, cfg),
        )

        await send_welcome_dm(self.bot, member.guild, member.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        cfg = await db.guild_config(member.guild.id)
        await log_action(
            member.guild,
            "miembro_sale",
            cfg=cfg,
            user_id=member.id,
            embed=leave_embed(member, cfg),
        )

    def _bot_can_manage(self, guild: discord.Guild, role: discord.Role) -> bool:
        me = guild.me
        if me is None:
            return False
        return me.guild_permissions.manage_roles and me.top_role > role


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))
