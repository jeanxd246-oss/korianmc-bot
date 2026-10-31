"""Moderación: sanciones, advertencias y expiración de sanciones temporales."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from services.embed_factory import moderation_log_embed
from services.notification import log_action
from utils.checks import staff_only
from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.helpers import ConfirmView, Paginator
from utils.timeformat import discord_timestamp, format_duration, from_sql, parse_duration, to_sql, utcnow
from utils.validators import clean_text

log = get_logger("moderacion")

MAX_TIMEOUT_MINUTES = 40320  # 28 días, límite de Discord
ACTION_ICONS = {"ban": "🔨", "tempban": "⏳", "kick": "👢", "timeout": "🔇", "unmute": "🔊", "warn": "⚠️"}


def _reason_or_default(reason: Optional[str]) -> str:
    return clean_text(reason, 400) or "Sin motivo indicado"


async def _register_case(
    guild: discord.Guild,
    *,
    user: discord.abc.User,
    moderator: discord.abc.User,
    action: str,
    reason: str,
    duration_minutes: Optional[int] = None,
    expires_at: Optional[str] = None,
) -> int:
    cursor = await db.execute(
        "INSERT INTO moderation_cases "
        "(guild_id, user_id, user_tag, moderator_id, action, reason, duration_minutes, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(guild.id),
            str(user.id),
            str(user),
            str(moderator.id),
            action,
            reason,
            duration_minutes,
            expires_at,
        ),
    )
    return int(cursor.lastrowid or 0)


class Moderation(commands.Cog):
    """Comandos de moderación con validaciones, cooldowns y registro."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._cooldowns: dict[tuple[int, int], float] = {}

    # ------------------------------------------------------------------
    # Validaciones
    # ------------------------------------------------------------------
    def _hierarchy_ok(self, ctx: commands.Context, member: discord.Member) -> tuple[bool, str]:
        if member.id == ctx.guild.me.id:
            return False, "No puedo sancionarme a mí mismo."
        if member.top_role >= ctx.guild.me.top_role:
            return False, "Tiene un rol igual o superior al mío, no puedo actuar sobre esa cuenta."
        if member.id == ctx.guild.owner_id:
            return False, "No se puede sancionar al propietario del servidor."
        if ctx.author.id != ctx.guild.owner_id and member.top_role >= ctx.author.top_role:
            return False, "Tiene un rol igual o superior al tuyo, no puedes sancionarlo."
        return True, ""

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    @commands.hybrid_command(name="ban", description="Banea a un usuario del servidor.")
    @staff_only()
    @commands.bot_has_permissions(ban_members=True)
    async def ban(
        self,
        ctx: commands.Context,
        usuario: discord.Member,
        razon: Optional[str] = None,
        borrar_dias: Optional[int] = None,
    ) -> None:
        ok, problem = self._hierarchy_ok(ctx, usuario)
        if not ok:
            await ctx.send(embed=error_perm(problem))
            return
        days = max(0, min(7, borrar_dias or 0))
        reason = _reason_or_default(razon)

        async def _confirm(interaction: discord.Interaction) -> None:
            try:
                await interaction.guild.ban(
                    user=usuario, reason=reason, delete_message_seconds=days * 86400
                )
            except discord.HTTPException as exc:
                await interaction.response.edit_message(
                    embed=error_embed("No se pudo banear", f"Discord devolvió un error: {exc}"),
                    view=None,
                )
                return
            case_id = await _register_case(
                interaction.guild, user=usuario, moderator=interaction.user,
                action="ban", reason=reason,
            )
            cfg = await db.guild_config(interaction.guild_id)
            await log_action(
                interaction.guild,
                "ban",
                cfg=cfg,
                embed=moderation_log_embed("ban", interaction.user, usuario, reason),
                user_id=interaction.user.id,
                target_id=usuario.id,
            )
            try:
                await usuario.send(
                    f"🔨 Fuiste baneado de **{interaction.guild.name}**.\n**Motivo:** {reason}"
                )
            except (discord.Forbidden, discord.HTTPException):
                log.info("No se pudo avisar por DM al usuario baneado.")
            await interaction.response.edit_message(
                embed=success_embed(
                    "Usuario baneado",
                    f"**{usuario}** fue baneado (caso `#{case_id}`).\n**Motivo:** {reason}",
                ),
                view=None,
            )

        view = ConfirmView(
            user_id=ctx.author.id,
            on_confirm=_confirm,
            confirm_label="Banear",
            cancel_label="Cancelar",
        )
        message = await ctx.send(
            embed=warn_embed(
                "⚠️ Confirmar baneo",
                f"Vas a banear a **{usuario}** (`{usuario.id}`).\n**Motivo:** {reason}\n"
                f"Se eliminarán mensajes de los últimos **{days}** día(s).",
            ),
            view=view,
        )
        view.message = message

    @commands.hybrid_command(name="tempban", description="Banea con duración y se levanta solo.")
    @staff_only()
    @commands.bot_has_permissions(ban_members=True)
    async def tempban(
        self,
        ctx: commands.Context,
        usuario: discord.Member,
        duracion: str,
        razon: Optional[str] = None,
    ) -> None:
        ok, problem = self._hierarchy_ok(ctx, usuario)
        if not ok:
            await ctx.send(embed=error_perm(problem))
            return
        seconds = parse_duration(duracion)
        if seconds is None or seconds < 60:
            await ctx.send(
                embed=error_embed("Duración no válida", "Usa formatos como `30m`, `12h` o `7d`.")
            )
            return
        minutes = max(1, min(MAX_TIMEOUT_MINUTES * 4, seconds // 60))
        reason = _reason_or_default(razon)
        expires_at = to_sql(from_sql(utcnow()) + timedelta(minutes=minutes))

        async def _confirm(interaction: discord.Interaction) -> None:
            try:
                await interaction.guild.ban(user=usuario, reason=f"[Tempban] {reason}")
            except discord.HTTPException as exc:
                await interaction.response.edit_message(
                    embed=error_embed("No se pudo banear", str(exc)), view=None
                )
                return
            case_id = await _register_case(
                interaction.guild, user=usuario, moderator=interaction.user,
                action="tempban", reason=reason, duration_minutes=minutes, expires_at=expires_at,
            )
            cfg = await db.guild_config(interaction.guild_id)
            await log_action(
                interaction.guild,
                "tempban",
                cfg=cfg,
                embed=moderation_log_embed(
                    "tempban", interaction.user, usuario, reason, format_duration(minutes * 60)
                ),
                user_id=interaction.user.id,
                target_id=usuario.id,
            )
            await interaction.response.edit_message(
                embed=success_embed(
                    "Baneo temporal",
                    f"**{usuario}** baneado durante **{format_duration(minutes * 60)}** "
                    f"(caso `#{case_id}`, se levanta {discord_timestamp(from_sql(expires_at))}).",
                ),
                view=None,
            )

        view = ConfirmView(user_id=ctx.author.id, on_confirm=_confirm, confirm_label="Tempban")
        message = await ctx.send(
            embed=warn_embed(
                "⚠️ Confirmar baneo temporal",
                f"**{usuario}** durante **{format_duration(minutes * 60)}**.\n**Motivo:** {reason}",
            ),
            view=view,
        )
        view.message = message

    @commands.hybrid_command(name="kick", description="Expulsa a un usuario del servidor.")
    @staff_only()
    @commands.bot_has_permissions(kick_members=True)
    async def kick(
        self, ctx: commands.Context, usuario: discord.Member, razon: Optional[str] = None
    ) -> None:
        ok, problem = self._hierarchy_ok(ctx, usuario)
        if not ok:
            await ctx.send(embed=error_perm(problem))
            return
        reason = _reason_or_default(razon)
        try:
            await usuario.kick(reason=reason)
        except discord.HTTPException as exc:
            await ctx.send(embed=error_embed("No se pudo expulsar", str(exc)))
            return
        await _register_case(
            ctx.guild, user=usuario, moderator=ctx.author, action="kick", reason=reason
        )
        cfg = await db.guild_config(ctx.guild.id)
        await log_action(
            ctx.guild,
            "kick",
            cfg=cfg,
            embed=moderation_log_embed("kick", ctx.author, usuario, reason),
            user_id=ctx.author.id,
            target_id=usuario.id,
        )
        await ctx.send(
            embed=success_embed("Usuario expulsado", f"**{usuario}** fue expulsado.\n**Motivo:** {reason}")
        )

    @commands.hybrid_command(name="timeout", description="Silencia a un usuario temporalmente.")
    @staff_only()
    @commands.bot_has_permissions(moderate_members=True)
    async def timeout(
        self,
        ctx: commands.Context,
        usuario: discord.Member,
        duracion: str,
        razon: Optional[str] = None,
    ) -> None:
        ok, problem = self._hierarchy_ok(ctx, usuario)
        if not ok:
            await ctx.send(embed=error_perm(problem))
            return
        seconds = parse_duration(duracion)
        if seconds is None or seconds < 60:
            await ctx.send(
                embed=error_embed("Duración no válida", "Usa formatos como `10m`, `1h` o `1d`.")
            )
            return
        minutes = max(1, min(MAX_TIMEOUT_MINUTES, seconds // 60))
        reason = _reason_or_default(razon)
        try:
            await usuario.timeout(timedelta(minutes=minutes), reason=reason)
        except discord.HTTPException as exc:
            await ctx.send(embed=error_embed("No se pudo silenciar", str(exc)))
            return
        expires_at = to_sql(from_sql(utcnow()) + timedelta(minutes=minutes))
        await _register_case(
            ctx.guild, user=usuario, moderator=ctx.author, action="timeout",
            reason=reason, duration_minutes=minutes, expires_at=expires_at,
        )
        cfg = await db.guild_config(ctx.guild.id)
        await log_action(
            ctx.guild,
            "timeout",
            cfg=cfg,
            embed=moderation_log_embed(
                "timeout", ctx.author, usuario, reason, format_duration(minutes * 60)
            ),
            user_id=ctx.author.id,
            target_id=usuario.id,
        )
        await ctx.send(
            embed=success_embed(
                "Usuario silenciado",
                f"**{usuario}** está silenciado **{format_duration(minutes * 60)}**.\n**Motivo:** {reason}",
            )
        )

    @commands.hybrid_command(name="unmute", description="Quita el silencio a un usuario.")
    @staff_only()
    @commands.bot_has_permissions(moderate_members=True)
    async def unmute(self, ctx: commands.Context, usuario: discord.Member, razon: Optional[str] = None) -> None:
        try:
            await usuario.timeout(None, reason=_reason_or_default(razon))
        except discord.HTTPException as exc:
            await ctx.send(embed=error_embed("No se pudo quitar el silencio", str(exc)))
            return
        await db.execute(
            "UPDATE moderation_cases SET active = 0 WHERE guild_id = ? AND user_id = ? "
            "AND action IN ('timeout') AND active = 1",
            (str(ctx.guild.id), str(usuario.id)),
        )
        cfg = await db.guild_config(ctx.guild.id)
        await log_action(
            ctx.guild,
            "unmute",
            cfg=cfg,
            embed=moderation_log_embed("unmute", ctx.author, usuario, _reason_or_default(razon)),
            user_id=ctx.author.id,
            target_id=usuario.id,
        )
        await ctx.send(embed=success_embed("Silencio retirado", f"**{usuario}** ya puede volver a hablar."))

    # ------------------------------------------------------------------
    # Advertencias
    # ------------------------------------------------------------------
    @commands.hybrid_group(name="warn", description="Advertencias de moderación.", invoke_without_command=True)
    @staff_only()
    async def warn(self, ctx: commands.Context) -> None:
        await ctx.send(
            embed=base_embed(
                "⚠️ Advertencias",
                "Usa `/warn add @usuario <motivo>`, `/warn list @usuario` o `/warn clear @usuario`.",
            )
        )

    @warn.command(name="add", description="Añade una advertencia a un usuario.")
    @staff_only()
    async def warn_add(self, ctx: commands.Context, usuario: discord.Member, motivo: str) -> None:
        if usuario.bot:
            await ctx.send(embed=error_perm("No se puede advertir a un bot."))
            return
        if usuario.id == ctx.author.id:
            await ctx.send(embed=error_perm("No puedes advertirte a ti mismo."))
            return
        reason = clean_text(motivo, 300)
        if len(reason) < 3:
            await ctx.send(embed=error_perm("El motivo debe tener al menos 3 caracteres."))
            return
        cursor = await db.execute(
            "INSERT INTO warns (guild_id, user_id, moderator_id, reason) VALUES (?, ?, ?, ?)",
            (str(ctx.guild.id), str(usuario.id), str(ctx.author.id), reason),
        )
        await db.execute(
            "UPDATE users SET warnings = warnings + 1 WHERE guild_id = ? AND user_id = ?",
            (str(ctx.guild.id), str(usuario.id)),
        )
        total = await db.scalar(
            "SELECT COUNT(*) FROM warns WHERE guild_id = ? AND user_id = ? AND active = 1",
            (str(ctx.guild.id), str(usuario.id)),
            default=0,
        )
        cfg = await db.guild_config(ctx.guild.id)
        await log_action(
            ctx.guild,
            "warn",
            cfg=cfg,
            embed=moderation_log_embed("warn", ctx.author, usuario, reason),
            user_id=ctx.author.id,
            target_id=usuario.id,
        )
        await send_warn_dm(self.bot, ctx.guild, usuario, reason, cursor.lastrowid)
        await ctx.send(
            embed=success_embed(
                "Advertencia registrada",
                f"**{usuario}** ahora tiene **{total}** advertencia(s) activa(s).\n**Motivo:** {reason}",
            )
        )

    @warn.command(name="list", description="Lista las advertencias de un usuario.")
    @staff_only()
    async def warn_list(self, ctx: commands.Context, usuario: discord.Member) -> None:
        rows = await db.fetchall(
            "SELECT * FROM warns WHERE guild_id = ? AND user_id = ? AND active = 1 ORDER BY id DESC",
            (str(ctx.guild.id), str(usuario.id)),
        )
        if not rows:
            await ctx.send(embed=base_embed("⚠️ Advertencias", f"**{usuario}** no tiene advertencias."))
            return
        lines = [
            f"**#{row['id']}** · <@{row['moderator_id']}> · {clean_text(row['reason'], 90)} · {row['created_at']}"
            for row in rows
        ]
        embeds = [
            base_embed(f"⚠️ Advertencias de {usuario.display_name}", "\n".join(lines[start : start + 15]))
            for start in range(0, len(lines), 15)
        ]
        for page, embed in enumerate(embeds, start=1):
            embed.set_footer(text=f"Página {page}/{len(embeds)}")
        await Paginator(embeds, user_id=ctx.author.id).start(ctx)

    @warn.command(name="clear", description="Elimina todas las advertencias de un usuario.")
    @staff_only()
    async def warn_clear(self, ctx: commands.Context, usuario: discord.Member) -> None:
        count = await db.scalar(
            "SELECT COUNT(*) FROM warns WHERE guild_id = ? AND user_id = ? AND active = 1",
            (str(ctx.guild.id), str(usuario.id)),
            default=0,
        )
        if not count:
            await ctx.send(embed=warn_embed("Nada que borrar", f"**{usuario}** no tiene advertencias."))
            return

        async def _confirm(interaction: discord.Interaction) -> None:
            await db.execute(
                "UPDATE warns SET active = 0 WHERE guild_id = ? AND user_id = ? AND active = 1",
                (str(interaction.guild_id), str(usuario.id)),
            )
            await db.execute(
                "UPDATE users SET warnings = 0 WHERE guild_id = ? AND user_id = ?",
                (str(interaction.guild_id), str(usuario.id)),
            )
            await interaction.response.edit_message(
                embed=success_embed(
                    "Advertencias borradas",
                    f"Se eliminaron **{count}** advertencia(s) de **{usuario}**.",
                ),
                view=None,
            )

        view = ConfirmView(user_id=ctx.author.id, on_confirm=_confirm, confirm_label="Borrar todas")
        message = await ctx.send(
            embed=warn_embed(
                "¿Borrar advertencias?",
                f"Se eliminarán **{count}** advertencia(s) de **{usuario}**. No se puede deshacer.",
            ),
            view=view,
        )
        view.message = message

    @commands.hybrid_command(name="warn-historial", description="Historial completo de advertencias de un usuario.")
    @staff_only()
    async def warn_historial(self, ctx: commands.Context, usuario: discord.Member) -> None:
        await self._send_history(ctx, usuario)

    @commands.hybrid_command(name="history", description="Alias de /warn-historial.")
    @staff_only()
    async def history(self, ctx: commands.Context, usuario: discord.Member) -> None:
        await self._send_history(ctx, usuario)

    async def _send_history(self, ctx: commands.Context, usuario: discord.Member) -> None:
        rows = await db.fetchall(
            "SELECT * FROM warns WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT 50",
            (str(ctx.guild.id), str(usuario.id)),
        )
        cases = await db.fetchall(
            "SELECT * FROM moderation_cases WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT 20",
            (str(ctx.guild.id), str(usuario.id)),
        )
        if not rows and not cases:
            await ctx.send(
                embed=base_embed("🗂️ Historial", f"**{usuario}** no tiene historial de moderación.")
            )
            return
        lines: list[str] = []
        for row in cases:
            icon = ACTION_ICONS.get(row["action"], "⚖️")
            extra = f" · hasta {discord_timestamp(from_sql(row['expires_at']))}" if row["expires_at"] else ""
            lines.append(f"{icon} `#{row['id']}` {row['action']}{extra} · {clean_text(str(row['reason']), 70)}")
        for row in rows:
            lines.append(
                f"⚠️ `#{row['id']}` warn · {clean_text(row['reason'], 70)} · "
                f"{'activa' if int(row['active']) else 'retirada'}"
            )
        embed = base_embed(
            f"🗂️ Historial de {usuario.display_name}",
            "\n".join(lines)[:3900] or "Sin registros.",
        )
        await ctx.send(embed=embed)


def error_perm(message: str) -> discord.Embed:
    return error_embed("No permitido", message)


async def send_warn_dm(
    bot: commands.Bot,
    guild: discord.Guild,
    member: discord.Member,
    reason: str,
    warn_id: Any,
) -> bool:
    """Avisa por DM de una advertencia con texto propio (sin plantilla)."""
    try:
        await member.send(
            f"⚠️ Recibiste una advertencia en **{guild.name}** (ID `#{warn_id}`).\n"
            f"**Motivo:** {reason}"
        )
        return True
    except (discord.Forbidden, discord.HTTPException) as exc:
        log.info("No se pudo avisar por DM de la advertencia: %s", exc)
        return False


async def process_expired_cases(bot: commands.Bot) -> int:
    """Levanta sanciones vencidas: tempban, timeout y casos caducados."""
    rows = await db.fetchall(
        "SELECT * FROM moderation_cases WHERE active = 1 AND expires_at IS NOT NULL AND expires_at <= ?",
        (utcnow(),),
    )
    processed = 0
    for row in rows:
        guild = bot.get_guild(int(row["guild_id"]))
        if guild is None:
            await db.execute("UPDATE moderation_cases SET active = 0 WHERE id = ?", (row["id"],))
            continue
        try:
            if str(row["action"]) == "tempban":
                await guild.unban(discord.Object(id=int(row["user_id"])), reason="Tempban finalizado")
                await log_action(
                    guild,
                    "tempban_expirado",
                    embed=success_embed(
                        "Baneo levantado",
                        f"El baneo temporal de `<@{row['user_id']}>` (caso `#{row['id']}`) terminó.",
                    ),
                )
                processed += 1
            elif str(row["action"]) == "timeout":
                member = guild.get_member(int(row["user_id"]))
                if member is not None:
                    await member.timeout(None, reason="Sanción temporal finalizada")
                    processed += 1
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            log.warning("No se pudo levantar la sanción #%s: %s", row["id"], exc)
        except Exception:
            log.exception("Error al procesar la sanción #%s", row["id"])
        await db.execute("UPDATE moderation_cases SET active = 0 WHERE id = ?", (row["id"],))
    return processed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
