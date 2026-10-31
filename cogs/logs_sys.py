"""Registro completo: mensajes, miembros, sanciones y cambios del servidor."""

from __future__ import annotations

from typing import Any, Optional

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from services.notification import log_action
from utils.embeds import base_embed, error_embed, warn_embed
from utils.timeformat import discord_timestamp
from utils.validators import clean_text

log = get_logger("logs")

MAX_CONTENT = 1000


def _snapshot(message: discord.Message) -> str:
    text = message.content or ""
    if not text and message.embeds:
        text = f"[embed] {message.embeds[0].title or message.embeds[0].description or ''}"
    if not text and message.attachments:
        text = f"[adjunto] {message.attachments[0].url}"
    return clean_text(text, MAX_CONTENT)


class LogsSys(commands.Cog):
    """Listeners de auditoría enviados al canal de logs configurado."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _channel(self, guild: discord.Guild, cfg: dict[str, Any]) -> Optional[discord.TextChannel]:
        from utils.permissions import channel_id

        target = channel_id(cfg, "logs")
        if not target:
            return None
        channel = guild.get_channel(target)
        return channel if isinstance(channel, discord.TextChannel) else None

    # ------------------------------------------------------------------
    # Mensajes
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        cfg = await db.guild_config(message.guild.id)
        channel = await self._channel(message.guild, cfg)
        if channel is None:
            return
        embed = base_embed("🗑️ Mensaje eliminado")
        embed.add_field(name="Autor", value=message.author.mention, inline=True)
        embed.add_field(name="Canal", value=message.channel.mention, inline=True)
        embed.add_field(name="Contenido", value=_snapshot(message) or "*Sin contenido*", inline=False)
        await channel.send(embed=embed)
        await log_action(
            message.guild,
            "mensaje_borrado",
            cfg=cfg,
            user_id=message.author.id,
            channel_id=getattr(message.channel, "id", None),
            content=_snapshot(message),
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.guild is None or before.author.bot:
            return
        if before.content == after.content or not before.content:
            return
        cfg = await db.guild_config(before.guild.id)
        channel = await self._channel(before.guild, cfg)
        if channel is None:
            return
        embed = base_embed("✏️ Mensaje editado")
        embed.add_field(name="Autor", value=before.author.mention, inline=True)
        embed.add_field(name="Canal", value=before.channel.mention, inline=True)
        embed.add_field(name="Antes", value=clean_text(before.content, 500) or "-", inline=False)
        embed.add_field(name="Ahora", value=clean_text(after.content, 500) or "-", inline=False)
        await channel.send(embed=embed)
        await log_action(
            before.guild,
            "mensaje_editado",
            cfg=cfg,
            user_id=before.author.id,
            channel_id=getattr(before.channel, "id", None),
            content=f"{clean_text(before.content, 400)} → {clean_text(after.content, 400)}",
        )

    # ------------------------------------------------------------------
    # Miembros
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User | discord.Member) -> None:
        cfg = await db.guild_config(guild.id)
        channel = await self._channel(guild, cfg)
        if channel is None:
            return
        entry = None
        try:
            async for audit in guild.audit_logs(limit=5, action=discord.AuditLogAction.ban):
                if audit.target.id == user.id:
                    entry = audit
                    break
        except discord.HTTPException:
            log.info("No se pudo leer el registro de auditoría de baneos.")
        embed = error_embed("🔨 Ban registrado")
        embed.add_field(name="Usuario", value=f"{user} (`{user.id}`)", inline=True)
        if entry is not None:
            embed.add_field(name="Moderador", value=entry.user.mention if entry.user else "—", inline=True)
            embed.add_field(name="Motivo", value=clean_text(entry.reason, 300) or "Sin motivo", inline=False)
        await channel.send(embed=embed)
        await log_action(guild, "ban_log", cfg=cfg, user_id=user.id, content=str(entry.reason or ""))

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        cfg = await db.guild_config(guild.id)
        channel = await self._channel(guild, cfg)
        if channel is None:
            return
        embed = base_embed("🔓 Unban")
        embed.add_field(name="Usuario", value=f"{user} (`{user.id}`)", inline=True)
        await channel.send(embed=embed)
        await log_action(guild, "unban_log", cfg=cfg, user_id=user.id, content=str(user))

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.guild is None:
            return
        cfg = await db.guild_config(before.guild.id)
        channel = await self._channel(before.guild, cfg)
        if channel is None:
            return

        # Silencios
        if before.timed_out is False and after.timed_out is True:
            until = after.timed_out_until
            embed = warn_embed("🔇 Silencio aplicado")
            embed.add_field(name="Usuario", value=after.mention, inline=True)
            if until:
                embed.add_field(name="Hasta", value=discord_timestamp(until.astimezone(tz=None)), inline=True)
            await channel.send(embed=embed)
            await log_action(before.guild, "timeout_log", cfg=cfg, user_id=after.id, content=str(until))
        elif before.timed_out is True and after.timed_out is False:
            embed = base_embed("🔊 Silencio retirado")
            embed.add_field(name="Usuario", value=after.mention, inline=True)
            await channel.send(embed=embed)
            await log_action(before.guild, "untimeout_log", cfg=cfg, user_id=after.id, content=str(after))

        # Cambios de apodo
        if before.nick != after.nick:
            embed = base_embed("👤 Apodo cambiado")
            embed.add_field(name="Usuario", value=after.mention, inline=True)
            embed.add_field(name="Antes", value=before.nick or before.name, inline=True)
            embed.add_field(name="Ahora", value=after.nick or after.name, inline=True)
            await channel.send(embed=embed)

        # Cambios de roles
        added = [role for role in after.roles if role not in before.roles]
        removed = [role for role in before.roles if role not in after.roles]
        if added or removed:
            lines = []
            if added:
                lines.append(f"+ {', '.join(role.mention for role in added)}")
            if removed:
                lines.append(f"- {', '.join(role.mention for role in removed)}")
            embed = base_embed("🎭 Roles actualizados")
            embed.add_field(name="Usuario", value=after.mention, inline=True)
            embed.add_field(name="Cambios", value="\n".join(lines)[:1000], inline=False)
            await channel.send(embed=embed)

    # ------------------------------------------------------------------
    # Estructura del servidor
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        cfg = await db.guild_config(channel.guild.id)
        embed = base_embed("➕ Canal creado")
        embed.add_field(name="Canal", value=f"{channel.mention or channel.name}", inline=True)
        embed.add_field(name="Tipo", value=str(channel.type).replace("_", " "), inline=True)
        await log_action(channel.guild, "canal_creado", cfg=cfg, embed=embed, channel_id=channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        cfg = await db.guild_config(channel.guild.id)
        embed = base_embed("➖ Canal eliminado")
        embed.add_field(name="Canal", value=f"#{getattr(channel, 'name', '?')}", inline=True)
        await log_action(channel.guild, "canal_borrado", cfg=cfg, embed=embed, channel_id=channel.id)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        cfg = await db.guild_config(role.guild.id)
        embed = base_embed("🎖️ Rol creado")
        embed.add_field(name="Rol", value=role.mention, inline=True)
        embed.add_field(name="Color", value=str(role.color), inline=True)
        await log_action(role.guild, "rol_creado", cfg=cfg, embed=embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        cfg = await db.guild_config(role.guild.id)
        embed = base_embed("🗑️ Rol eliminado")
        embed.add_field(name="Rol", value=role.name, inline=True)
        await log_action(role.guild, "rol_borrado", cfg=cfg, embed=embed)

    # ------------------------------------------------------------------
    # Comando de consulta
    # ------------------------------------------------------------------
    @commands.hybrid_command(name="logs", description="[Staff] Muestra los últimos eventos registrados.")
    @commands.guild_only()
    async def logs_command(self, ctx: commands.Context, cantidad: int = 10) -> None:
        from utils.checks import staff_check

        try:
            await staff_check(ctx)
        except Exception:
            await ctx.send(embed=error_embed("Permisos", "Necesitas el rol de staff para ver los logs."))
            return
        limit = max(1, min(25, cantidad))
        rows = await db.fetchall(
            "SELECT event, user_id, content, created_at FROM logs "
            "WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
            (str(ctx.guild.id), limit),
        )
        if not rows:
            await ctx.send(embed=base_embed("📜 Logs", "No hay eventos registrados todavía."))
            return
        lines = [
            f"`{row['created_at']}` **{row['event']}**"
            f"{' · <@' + str(row['user_id']) + '>' if row['user_id'] else ''}"
            f"{' · ' + clean_text(str(row['content']), 70) if row['content'] else ''}"
            for row in rows
        ]
        await ctx.send(embed=base_embed("📜 Últimos eventos", "\n".join(lines)))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LogsSys(bot))
