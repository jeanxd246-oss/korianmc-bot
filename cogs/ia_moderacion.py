"""Moderación asistida por IA: detección de toxicidad con umbral configurable."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Optional

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from services.ai_client import ai
from services.embed_factory import moderation_log_embed
from services.notification import log_action
from utils.checks import admin_only
from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.validators import clean_text

log = get_logger("moderacion_ia")


def action_for(score: float) -> str:
    if score >= 0.8:
        return "silencio"
    if score >= 0.6:
        return "aviso"
    return "registro"


class IAModeracion(commands.Cog):
    """Analiza mensajes con Gemini y actúa según el umbral configurado."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._cooldown: dict[tuple[int, int], float] = {}

    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return

        cfg = await db.guild_config(message.guild.id)
        ai_cfg = cfg.get("ai") or {}
        if not ai_cfg.get("auto_moderation", False):
            return
        if not ai.available:
            return

        from utils.permissions import is_staff

        if is_staff(message.author, cfg):
            return

        content = (message.content or "").strip()
        if len(content) < 6:
            return

        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self._cooldown.get(key, 0.0) < 45:
            return
        self._cooldown[key] = now

        report = await ai.moderate(content)
        if report is None:
            return

        threshold = float(ai_cfg.get("moderation_threshold", 0.75))
        score = max(float(report["toxicidad"]), float(report["spam"]))
        await db.execute(
            "INSERT INTO ai_moderation_logs "
            "(guild_id, user_id, channel_id, message_id, classification, score, action, snippet) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(message.guild.id),
                str(message.author.id),
                str(message.channel.id),
                str(message.id),
                str(report["categoria"]),
                score,
                action_for(score),
                clean_text(content, 180),
            ),
        )
        if score < threshold:
            return

        action = action_for(score)
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            log.info("No se pudo borrar el mensaje moderado por IA.")

        await log_action(
            message.guild,
            "moderacion_ia",
            cfg=cfg,
            embed=moderation_log_embed(
                "automod",
                self.bot.user or message.author,
                message.author,
                f"[IA] {report['categoria']} ({score:.2f}): {report['detalle']}",
            ),
            user_id=message.author.id,
            target_id=message.author.id,
            channel_key="moderation",
        )

        if action == "silencio" and message.guild.me and message.guild.me.guild_permissions.moderate_members:
            minutes = 10
            try:
                await message.author.timeout(
                    discord.utils.utcnow() + timedelta(minutes=minutes),
                    reason=f"Moderación IA ({score:.2f})",
                )
            except discord.HTTPException as exc:
                log.warning("No se pudo aplicar el silencio por IA: %s", exc)
            await message.channel.send(
                embed=warn_embed(
                    "🤖 Moderación IA",
                    f"{message.author.mention} fue silenciado {minutes} minutos por "
                    f"contenido inapropiado (**{report['categoria']}**).",
                ),
                delete_after=20,
            )
        elif action == "aviso":
            try:
                await message.author.send(
                    f"🤖 El sistema de IA de **{message.guild.name}** detectó contenido inapropiado "
                    f"(**{report['categoria']}**).\n{report['detalle']}"
                )
            except (discord.Forbidden, discord.HTTPException):
                log.info("No se pudo avisar por DM tras la moderación IA.")

    # ------------------------------------------------------------------
    @commands.hybrid_group(name="iamod", description="Configuración de la moderación con IA.", invoke_without_command=True)
    @admin_only()
    async def iamod(self, ctx: commands.Context) -> None:
        await self.iamod_estado(ctx)

    @iamod.command(name="estado", description="Muestra la configuración actual de moderación IA.")
    @admin_only()
    async def iamod_estado(self, ctx: commands.Context) -> None:
        cfg = await db.guild_config(ctx.guild.id)
        ai_cfg = cfg.get("ai") or {}
        total = await db.scalar(
            "SELECT COUNT(*) FROM ai_moderation_logs WHERE guild_id = ?",
            (str(ctx.guild.id),),
            default=0,
        )
        embed = base_embed(
            "🤖 Moderación con IA",
            f"**Disponible:** {'sí (Gemini conectado)' if ai.available else 'no — ' + ai.reason}\n"
            f"**Activa:** {'sí' if ai_cfg.get('auto_moderation') else 'no'}\n"
            f"**Umbral:** {float(ai_cfg.get('moderation_threshold', 0.75)):.2f}\n"
            f"**Acciones:** registro < 0.60 · aviso ≥ 0.60 · silencio ≥ 0.80\n"
            f"**Registros totales:** {total}",
        )
        if not ai.available:
            embed.set_footer(text=ai.unavailable_notice.replace("\n", " "))
        await ctx.send(embed=embed)

    @iamod.command(name="on", description="Activa la moderación automática con IA.")
    @admin_only()
    async def iamod_on(self, ctx: commands.Context) -> None:
        if not ai.available:
            await ctx.send(
                embed=error_embed(
                    "IA no disponible",
                    f"No se puede activar: {ai.reason}. Configura `GEMINI_API_KEY` en `.env`.",
                )
            )
            return
        await db.set_config(ctx.guild.id, "ai.auto_moderation", True)
        await ctx.send(
            embed=success_embed(
                "Moderación IA activada",
                "Los mensajes se analizarán con Gemini y se sancionarán por encima del umbral.",
            )
        )

    @iamod.command(name="off", description="Desactiva la moderación automática con IA.")
    @admin_only()
    async def iamod_off(self, ctx: commands.Context) -> None:
        await db.set_config(ctx.guild.id, "ai.auto_moderation", False)
        await ctx.send(embed=success_embed("Moderación IA desactivada", "Ya no se analizan mensajes."))

    @iamod.command(name="umbral", description="Ajusta el umbral de toxicidad (0.0 - 1.0).")
    @admin_only()
    async def iamod_umbral(self, ctx: commands.Context, valor: float) -> None:
        if not 0.0 <= valor <= 1.0:
            await ctx.send(embed=error_embed("Valor no válido", "Usa un valor entre **0.0** y **1.0**."))
            return
        await db.set_config(ctx.guild.id, "ai.moderation_threshold", round(float(valor), 2))
        await ctx.send(
            embed=success_embed(
                "Umbral actualizado",
                f"A partir de **{valor:.2f}** se aplicará la acción correspondiente.",
            )
        )

    @iamod.command(name="log", description="[Staff] Últimos registros de la moderación IA.")
    @admin_only()
    async def iamod_log(
        self, ctx: commands.Context, usuario: Optional[discord.Member] = None, cantidad: int = 10
    ) -> None:
        limit = max(1, min(25, cantidad))
        if usuario:
            rows = await db.fetchall(
                "SELECT * FROM ai_moderation_logs WHERE guild_id = ? AND user_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (str(ctx.guild.id), str(usuario.id), limit),
            )
        else:
            rows = await db.fetchall(
                "SELECT * FROM ai_moderation_logs WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
                (str(ctx.guild.id), limit),
            )
        if not rows:
            await ctx.send(embed=base_embed("🤖 Registros IA", "No hay registros todavía."))
            return
        lines = [
            f"`{row['created_at']}` <@{row['user_id']}> · {row['classification']} "
            f"({float(row['score']):.2f}) · {row['action']} · {clean_text(str(row['snippet']), 60)}"
            for row in rows
        ]
        await ctx.send(embed=base_embed("🤖 Registros de moderación IA", "\n".join(lines)))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(IAModeracion(bot))
