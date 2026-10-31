"""Reaction roles: roles asignados al reaccionar un mensaje con un emoji."""

from __future__ import annotations

from typing import Optional

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from services.notification import log_action
from utils.checks import staff_only
from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.permissions import bot_can_manage_roles
from utils.validators import clean_text

log = get_logger("reaction_roles")


def _emoji_key(emoji: str) -> str:
    return str(emoji).strip()


class ReactionRoles(commands.Cog):
    """Asignación y retira de roles mediante reacciones."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._cache: dict[tuple[str, str, str], str] = {}

    async def _load_cache(self) -> None:
        rows = await db.fetchall(
            "SELECT guild_id, message_id, emoji, role_id FROM reaction_roles"
        )
        self._cache = {
            (str(row["guild_id"]), str(row["message_id"]), _emoji_key(row["emoji"])): str(row["role_id"])
            for row in rows
        }

    async def register_views(self) -> None:
        await self._load_cache()

    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle_reaction(payload, add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle_reaction(payload, add=False)

    async def _handle_reaction(self, payload: discord.RawReactionActionEvent, *, add: bool) -> None:
        if payload.guild_id is None:
            return
        if not self._cache:
            await self._load_cache()
        key = (str(payload.guild_id), str(payload.message_id), _emoji_key(str(payload.emoji)))
        role_id = self._cache.get(key)
        if role_id is None:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        role = guild.get_role(int(role_id))
        if role is None:
            return
        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return
        if not bot_can_manage_roles(guild, role):
            log.warning("El bot no puede gestionar el rol %s en %s", role.name, guild.name)
            return

        try:
            if add:
                if role not in member.roles:
                    await member.add_roles(role, reason="Reaction role")
            else:
                if role in member.roles:
                    await member.remove_roles(role, reason="Reaction role")
        except discord.HTTPException as exc:
            log.warning("No se pudo actualizar el rol por reacción: %s", exc)
            return

        cfg = await db.guild_config(guild.id)
        await log_action(
            guild,
            "reaction_role",
            cfg=cfg,
            user_id=member.id,
            content=f"{'+' if add else '-'} {role.name} ({payload.emoji})",
        )

    # ------------------------------------------------------------------
    @commands.hybrid_group(
        name="reactionroles",
        description="Gestiona los roles por reacción.",
        invoke_without_command=True,
    )
    @staff_only()
    async def reactionroles(self, ctx: commands.Context) -> None:
        await self._list(ctx)

    @reactionroles.command(name="crear", description="Publica un mensaje y asigna rol al reaccionar.")
    @staff_only()
    async def reactionroles_create(
        self,
        ctx: commands.Context,
        rol: discord.Role,
        emoji: str,
        canal: Optional[discord.TextChannel] = None,
        mensaje: Optional[str] = None,
    ) -> None:
        if rol >= ctx.guild.me.top_role:
            await ctx.send(
                embed=error_embed(
                    "Rol no asignable",
                    "Mi rol debe estar **por encima** del rol que quieres asignar.",
                )
            )
            return
        destination = canal or ctx.channel
        if not isinstance(destination, discord.TextChannel):
            await ctx.send(embed=error_embed("Canal no válido", "Especifica un canal de texto."))
            return
        text = clean_text(
            mensaje
            or f"Reacciona con {emoji} para recibir el rol **{rol.name}**.",
            400,
        )
        try:
            message = await destination.send(
                embed=base_embed("🎭 Obtén un rol", text)
            )
            await message.add_reaction(emoji)
        except discord.HTTPException as exc:
            await ctx.send(embed=error_embed("No se pudo publicar", str(exc)))
            return

        await db.execute(
            "INSERT INTO reaction_roles (guild_id, channel_id, message_id, emoji, role_id) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (guild_id, message_id, emoji) DO UPDATE SET role_id = excluded.role_id",
            (str(ctx.guild.id), str(destination.id), str(message.id), _emoji_key(emoji), str(rol.id)),
        )
        await self._load_cache()
        await log_action(
            ctx.guild,
            "reaction_role_creado",
            user_id=ctx.author.id,
            channel_id=destination.id,
            content=f"{emoji} → {rol.name} en {message.id}",
        )
        await ctx.send(
            embed=success_embed(
                "Reaction role creado",
                f"{emoji} → **{rol.mention}** en {destination.mention}.",
            )
        )

    @reactionroles.command(name="eliminar", description="Elimina un reaction role existente.")
    @staff_only()
    async def reactionroles_delete(
        self, ctx: commands.Context, id_mensaje: str, emoji: str
    ) -> None:
        cursor = await db.execute(
            "DELETE FROM reaction_roles WHERE guild_id = ? AND message_id = ? AND emoji = ?",
            (str(ctx.guild.id), str(id_mensaje), _emoji_key(emoji)),
        )
        await self._load_cache()
        if cursor.rowcount == 0:
            await ctx.send(
                embed=warn_embed(
                    "No encontrado",
                    f"No hay reaction role con mensaje `{id_mensaje}` y emoji {emoji}.",
                )
            )
            return
        await ctx.send(
            embed=success_embed(
                "Reaction role eliminado",
                f"Se quitó la relación {emoji} ↔ mensaje `{id_mensaje}`.",
            )
        )

    @reactionroles.command(name="lista", description="Lista los reaction roles configurados.")
    @staff_only()
    async def reactionroles_list(self, ctx: commands.Context) -> None:
        await self._list(ctx)

    async def _list(self, ctx: commands.Context) -> None:
        rows = await db.fetchall(
            "SELECT * FROM reaction_roles WHERE guild_id = ? ORDER BY id DESC LIMIT 25",
            (str(ctx.guild.id),),
        )
        if not rows:
            await ctx.send(
                embed=base_embed(
                    "🎭 Reaction roles",
                    "No hay configurados. Crea uno con `/reactionroles crear <rol> <emoji>`.",
                )
            )
            return
        lines = [
            f"Mensaje `{row['message_id']}` · {row['emoji']} → <@&{row['role_id']}>"
            for row in rows
        ]
        await ctx.send(embed=base_embed("🎭 Reaction roles", "\n".join(lines)))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRoles(bot))
