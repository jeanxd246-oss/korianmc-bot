"""Automoderación: spam, enlaces, invitaciones, mayúsculas, menciones y palabras."""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Any, Optional

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from services.embed_factory import moderation_log_embed
from services.notification import log_action
from utils.anonymizer import filter_words
from utils.checks import staff_only
from utils.embeds import base_embed, success_embed, warn_embed
from utils.validators import caps_percentage

log = get_logger("automod")

INVITE_RE = re.compile(
    r"(discord\.(gg|io|me|com/invite)|discordapp\.com/invite)/[a-zA-Z0-9-]+", re.IGNORECASE
)
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)


class Automod(commands.Cog):
    """Reglas configurables con `/setup moderation` y castigo progresivo."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._recent: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        self._strikes: dict[tuple[int, int], int] = defaultdict(int)
        self._last_action: dict[tuple[int, int], float] = {}

    # ------------------------------------------------------------------
    # Detección
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if message.channel.category and message.channel.category.name.lower().startswith("ticket"):
            return
        if isinstance(message.author, discord.Member) and message.author.guild_permissions.manage_messages:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return

        cfg = await db.guild_config(message.guild.id)
        rules = cfg.get("automod") or {}
        if not rules.get("enabled", True):
            return

        content = (message.content or "").strip()
        if not content:
            return

        violation = self._evaluate(
            content,
            message.guild,
            message.author,
            len(message.mentions) + len(message.role_mentions),
            rules,
        )
        if violation is None:
            return
        label, detail = violation

        await log_action(
            message.guild,
            "automod",
            cfg=cfg,
            embed=moderation_log_embed(
                "automod",
                self.bot.user or message.author,
                message.author,
                f"{label}: {detail}",
            ),
            user_id=message.author.id,
            channel_id=message.channel.id,
            channel_key="moderation",
        )
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            log.info("No se pudo borrar el mensaje que activó el automod.")

        await self._punish(message, cfg, rules, label)

    def _evaluate(
        self,
        content: str,
        guild: discord.Guild,
        author: discord.abc.User,
        mention_count: int,
        rules: dict[str, Any],
    ) -> Optional[tuple[str, str]]:
        key = (guild.id, author.id)
        now = time.monotonic()

        # Palabras bloqueadas
        blocked = filter_words(content, rules.get("blocked_words") or [])
        if blocked:
            return "palabra_bloqueada", f"contenido filtrado ({', '.join(blocked[:3])})"

        # Invitaciones de Discord
        if rules.get("anti_invites", True) and INVITE_RE.search(content):
            return "invitacion", "enlace de invitación a otro servidor"

        # Enlaces genéricos
        if rules.get("anti_links", False) and URL_RE.search(content):
            return "enlace", "enlace externo no permitido"

        # Mensajes repetidos (spam)
        if rules.get("anti_spam", True):
            window = max(2, int(rules.get("spam_window", 6)))
            history = [stamp for stamp in self._recent[key] if now - stamp <= window]
            history.append(now)
            self._recent[key] = deque(history, maxlen=40)
            limit = max(3, int(rules.get("spam_messages", 5)))
            if len(history) > limit:
                return "spam", f"{len(history)} mensajes en {window}s"

        # Mayúsculas
        if rules.get("anti_caps", True):
            minimum = int(rules.get("caps_min", 12))
            threshold = float(rules.get("caps_pct", 70))
            if len(content) >= minimum and caps_percentage(content) >= threshold:
                return "mayusculas", f"{caps_percentage(content)}% de mayúsculas"

        # Menciones masivas
        if rules.get("anti_massmention", True):
            limit = int(rules.get("max_mentions", 5))
            total = mention_count
            if "@everyone" in content or "@here" in content or total > limit:
                return "menciones", f"{total} menciones en un solo mensaje"

        return None

    # ------------------------------------------------------------------
    # Castigo progresivo
    # ------------------------------------------------------------------
    async def _punish(
        self, message: discord.Message, cfg: dict[str, Any], rules: dict[str, Any], label: str
    ) -> None:
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self._last_action.get(key, 0.0) < 60:
            return
        self._last_action[key] = now
        self._strikes[key] += 1

        action = str(rules.get("action", "delete"))
        warn_after = int(rules.get("warn_after", 3))
        mute_minutes = int(rules.get("mute_minutes", 10))

        if action == "warn" or self._strikes[key] >= warn_after:
            try:
                await message.author.send(
                    f"🤖 El automod de **{message.guild.name}** detectó: **{label}**.\n"
                    "Evita repetirlo o podrías recibir una sanción."
                )
            except (discord.Forbidden, discord.HTTPException):
                log.info("No se pudo avisar por DM al usuario moderado.")

        if action in {"mute", "timeout"} and message.guild.me and message.guild.me.guild_permissions.moderate_members:
            try:
                await message.author.timeout(
                    discord.utils.utcnow() + timedelta(minutes=mute_minutes),
                    reason=f"Automod: {label}",
                )
            except discord.HTTPException as exc:
                log.warning("No se pudo aplicar timeout desde automod: %s", exc)
            await log_action(
                message.guild,
                "automod_timeout",
                cfg=cfg,
                embed=warn_embed(
                    "🤖 Automod sancionó",
                    f"{message.author.mention} silenciado {mute_minutes} min por **{label}**.",
                ),
                user_id=message.author.id,
                target_id=message.author.id,
            )

    # ------------------------------------------------------------------
    @commands.hybrid_command(name="automod", description="[Staff] Prueba las reglas de automoderación con un texto.")
    @staff_only()
    async def automod_test(self, ctx: commands.Context, texto: str) -> None:
        cfg = await db.guild_config(ctx.guild.id)
        rules = cfg.get("automod") or {}
        result = self._evaluate(
            texto,
            ctx.guild,
            ctx.author,
            len(ctx.message.mentions) + len(ctx.message.role_mentions),
            rules,
        )
        if result is None:
            await ctx.send(
                embed=success_embed(
                    "✅ Mensaje permitido",
                    "Con la configuración actual ese texto pasaría el filtro.",
                )
            )
            return
        label, detail = result
        await ctx.send(
            embed=warn_embed(
                "🤖 Mensaje bloqueado",
                f"Regla: **{label}** · {detail}\n\nAcción configurada: `{rules.get('action', 'delete')}`.",
            )
        )

    @commands.hybrid_command(name="automod-estado", description="[Staff] Muestra las reglas activas.")
    @staff_only()
    async def automod_status(self, ctx: commands.Context) -> None:
        cfg = await db.guild_config(ctx.guild.id)
        rules = cfg.get("automod") or {}
        embed = base_embed(
            "🤖 Automoderación",
            "\n".join(
                [
                    f"**Activa:** {'sí' if rules.get('enabled', True) else 'no'}",
                    f"**Anti-spam:** {'sí' if rules.get('anti_spam') else 'no'} · "
                    f"{rules.get('spam_messages')} msgs / {rules.get('spam_window')}s",
                    f"**Anti-enlaces:** {'sí' if rules.get('anti_links') else 'no'}",
                    f"**Anti-invitaciones:** {'sí' if rules.get('anti_invites') else 'no'}",
                    f"**Anti-mayúsculas:** {'sí' if rules.get('anti_caps') else 'no'} · "
                    f"{rules.get('caps_pct')}% en {rules.get('caps_min')}+ caracteres",
                    f"**Anti-menciones:** {'sí' if rules.get('anti_massmention') else 'no'} · "
                    f"máx. {rules.get('max_mentions')}",
                    f"**Acción:** `{rules.get('action', 'delete')}` · "
                    f"aviso a las {rules.get('warn_after')} · silencio {rules.get('mute_minutes')} min",
                    f"**Palabras bloqueadas:** "
                    f"{', '.join(rules.get('blocked_words') or []) or 'ninguna'}",
                ]
            ),
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Automod(bot))
