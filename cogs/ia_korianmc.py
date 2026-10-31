"""KorianMC IA: asistente que responde en el canal de soporte sin comandos.

Funcionalidad:
- Escucha el canal de soporte configurado y clasifica la intención
  (PREGUNTA, QUEJA, SPAM, NO_APLICA).
- Responde con Gemini usando la base de conocimiento del servidor.
- Mantiene contexto de conversación (últimos N mensajes).
- Ofrece «Escalar a humano» cuando no sabe la respuesta.
- Cooldown, anti-duplicado y cuota diaria por usuario.
- Sin GEMINI_API_KEY la respuesta es un aviso elegante y el bot sigue
  funcionando con normalidad.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import aiohttp
import discord
from discord.ext import commands

from core.config import settings
from core.database import db
from core.logging_cfg import get_logger
from services import knowledge
from services.ai_client import ai
from services.ai_limits import limiter
from utils.checks import admin_only
from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.permissions import channel_id, role_ids
from utils.validators import clean_text, is_valid_url, require_text

log = get_logger("korianmc_ia")

ESCALATE_CUSTOM_ID = "ia:escalar"
PAGE_SIZE = 4


class EscalarView(discord.ui.View):
    """Botón persistente «Escalar a humano» bajo cada respuesta de la IA."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label="Escalar a humano",
            emoji="🙋",
            style=discord.ButtonStyle.primary,
            custom_id=ESCALATE_CUSTOM_ID,
        )
        button.callback = self._escalate
        self.add_item(button)

    async def _escalate(self, interaction: discord.Interaction) -> None:
        cfg = await db.guild_config(interaction.guild_id)
        if not interaction.message:
            await interaction.response.send_message(
                "No se pudo identificar la conversación.", ephemeral=True
            )
            return
        row = await db.fetchone(
            "SELECT conversation_id FROM ai_messages WHERE message_id = ?",
            (str(interaction.message.id),),
        )
        mentions = " ".join(f"<@&{role_id}>" for role_id in role_ids(cfg, "staff"))
        target = None
        if row is not None:
            target = await db.fetchone(
                "SELECT * FROM ai_conversations WHERE id = ?", (row["conversation_id"],)
            )
        detail = ""
        if target is not None:
            detail = f"Conversación `#{target['id']}` de <@{target['user_id']}>."
            channel = interaction.guild.get_channel(int(target["channel_id"])) if interaction.guild else None
            if isinstance(channel, discord.TextChannel):
                detail += f" Original: {channel.jump_url}"

        if not mentions:
            await interaction.response.send_message(
                embed=warn_embed(
                    "Sin staff configurado",
                    "Aún no hay roles de staff configurados. Usa `/setup all` para definirlos.",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            content=mentions,
            embed=base_embed(
                "🙋 Escalado a un humano",
                f"{interaction.user.mention} pide ayuda de una persona del equipo.\n{detail}",
            ),
            allowed_mentions=discord.AllowedMentions(roles=True, users=True),
        )
        await db.log_event(
            interaction.guild_id,
            "ia_escalado",
            user_id=interaction.user.id,
            content=str(target["id"]) if target else "sin conversación",
        )


class IAKorianMC(commands.Cog):
    """El sistema custom KorianMC IA."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_notice: dict[int, float] = {}
        self._last_status: dict[int, float] = {}

    async def register_views(self) -> None:
        self.bot.add_view(EscalarView())

    # ------------------------------------------------------------------
    # Utilidades de conversación
    # ------------------------------------------------------------------
    async def _conversation(self, guild_id: Any, user_id: Any, channel_id: Any) -> dict[str, Any]:
        row = await db.fetchone(
            "SELECT * FROM ai_conversations WHERE guild_id = ? AND user_id = ? AND channel_id = ?",
            (str(guild_id), str(user_id), str(channel_id)),
        )
        if row is not None:
            await db.execute(
                "UPDATE ai_conversations SET last_message_at = datetime('now') WHERE id = ?",
                (row["id"],),
            )
            return row
        cursor = await db.execute(
            "INSERT INTO ai_conversations (guild_id, user_id, channel_id) VALUES (?, ?, ?)",
            (str(guild_id), str(user_id), str(channel_id)),
        )
        return {
            "id": cursor.lastrowid,
            "guild_id": str(guild_id),
            "user_id": str(user_id),
            "channel_id": str(channel_id),
        }

    async def _context(self, conversation_id: int, limit: int) -> str:
        rows = await db.fetchall(
            "SELECT role, content FROM ai_messages WHERE conversation_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        )
        lines: list[str] = []
        for row in reversed(rows):
            who = "Usuario" if row["role"] == "user" else "KorianMC IA"
            lines.append(f"{who}: {clean_text(str(row['content']), 400)}")
        return "\n".join(lines)

    async def _remember(
        self, conversation_id: int, role: str, content: str, message_id: Any = None
    ) -> None:
        await db.execute(
            "INSERT INTO ai_messages (conversation_id, role, content, message_id) VALUES (?, ?, ?, ?)",
            (conversation_id, role, clean_text(content, 1800), str(message_id) if message_id else None),
        )

    # ------------------------------------------------------------------
    # Respuesta automática del canal de soporte
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        cfg = await db.guild_config(message.guild.id)
        ai_cfg = cfg.get("ai") or {}
        if not ai_cfg.get("enabled", True):
            return
        support_id = channel_id(cfg, "ai_support")
        if not support_id or message.channel.id != support_id:
            return
        content = (message.content or "").strip()
        if not content or len(content) < 3:
            return
        if content.startswith(tuple(settings.prefixes)):
            return

        intent = await ai.classify(content)
        mentioned = self.bot.user is not None and self.bot.user.id in message.mentions

        if intent == "SPAM":
            await db.log_event(
                message.guild.id,
                "ia_spam",
                user_id=message.author.id,
                channel_id=message.channel.id,
                content=clean_text(content, 300),
            )
            return
        if intent == "NO_APLICA" and not mentioned:
            return

        allowed, reason = limiter.check(message.guild.id, message.author.id, content, cfg)
        if not allowed:
            if reason.startswith("cooldown") and self._cooling(message, reason):
                return
            if reason in {"duplicado", "cuota"} and self._quiet(message):
                return
            if reason == "processing":
                return
            await self._send_notice(message, self._rate_text(reason))
            return
        if not limiter.start_processing(message.guild.id, message.author.id):
            return

        try:
            conversation = await self._conversation(
                message.guild.id, message.author.id, message.channel.id
            )
            history = await self._context(
                int(conversation["id"]), int(ai_cfg.get("context_messages", 6))
            )
            base = await knowledge.knowledge_block(message.guild.id)
            personality = str(ai_cfg.get("personality", "cercano, breve y profesional"))

            system = (
                f"Eres KorianMC IA, el asistente oficial del servidor Discord "
                f"«{message.guild.name}». Personalidad: {personality}. "
                "Respondes SIEMPRE en español, con brevedad y sin inventar funciones "
                "que el servidor no tenga. Si no sabes la respuesta o el asunto requiere "
                "una persona, invita a usar el botón «Escalar a humano».\n\n"
                f"BASE DE CONOCIMIENTO DEL SERVIDOR:\n{base}"
            )
            prompt = (
                f"HISTORIAL RECIENTE DE LA CONVERSACIÓN:\n{history or '(sin mensajes previos)'}\n\n"
                f"MENSAJE ACTUAL de {message.author.display_name} "
                f"(intención clasificada como {intent}):\n{content[:2000]}"
            )

            answer = await ai.chat(prompt, system)
            if answer is None:
                await self._send_notice(message, ai.unavailable_notice, force=True)
                return

            limiter.register(message.guild.id, message.author.id, content)
            await self._remember(conversation["id"], "user", content)
            await self._remember(conversation["id"], "assistant", answer)

            embed = base_embed(f"🤖 KorianMC IA · {intent.title()}", clean_text(answer, 3900))
            embed.set_thumbnail(url=message.author.display_avatar.with_size(128).url)
            if intent == "QUEJA":
                embed.add_field(
                    name="¿Quieres que te atienda una persona?",
                    value="Pulsa el botón de abajo y el staff recibirá tu aviso.",
                    inline=False,
                )
            view = EscalarView() if (ai_cfg.get("escalate", True) and role_ids(cfg, "staff")) else None
            await message.reply(embed=embed, view=view, mention_author=False)
        except Exception:
            log.exception("Error en la respuesta automática de KorianMC IA")
            await self._send_notice(
                message,
                "🤖 **KorianMC IA** tuvo un problema técnico. Vuelve a intentarlo en unos segundos.",
                force=True,
            )
        finally:
            limiter.stop_processing(message.guild.id, message.author.id)

    # ------------------------------------------------------------------
    def _cooling(self, message: discord.Message, reason: str) -> bool:
        key = (message.channel.id, message.author.id)
        now = time.monotonic()
        if now - self._last_status.get(key, 0.0) < 60:
            return True
        self._last_status[key] = now
        return False

    def _quiet(self, message: discord.Message) -> bool:
        key = (message.channel.id, message.author.id + 10_000)
        now = time.monotonic()
        if now - self._last_status.get(key, 0.0) < 120:
            return True
        self._last_status[key] = now
        return False

    def _rate_text(self, reason: str) -> str:
        if reason.startswith("cooldown"):
            seconds = reason.split(":", 1)[-1]
            return (
                f"⏳ Espera **{seconds} segundos** antes de volver a preguntarme "
                "(estoy limitando el abuso)."
            )
        if reason == "cuota":
            return "📊 Alcanzaste la cuota diaria de mensajes con la IA. Vuelve mañana o pide ayuda al staff."
        if reason == "duplicado":
            return "🔁 Me acabas de hacer una pregunta muy parecida: consulta la respuesta anterior."
        return "🤖 KorianMC IA no puede responderte ahora mismo."

    async def _send_notice(self, message: discord.Message, text: str, force: bool = False) -> None:
        key = message.channel.id
        now = time.monotonic()
        if not force and now - self._last_notice.get(key, 0.0) < 120:
            return
        self._last_notice[key] = now
        try:
            await message.channel.send(
                embed=warn_embed("KorianMC IA", text), delete_after=30
            )
        except discord.HTTPException:
            log.info("No se pudo enviar el aviso de KorianMC IA.")

    # ------------------------------------------------------------------
    # Comandos /ia
    # ------------------------------------------------------------------
    @commands.hybrid_group(name="ia", description="KorianMC IA: chat, resumen, traducción y más.", invoke_without_command=True)
    @commands.guild_only()
    async def ia(self, ctx: commands.Context, texto: str) -> None:
        await self.ia_chat(ctx, texto)

    @ia.command(name="chat", description="Habla con KorianMC IA.")
    @commands.guild_only()
    async def ia_chat(self, ctx: commands.Context, texto: str) -> None:
        prompt = require_text(texto, "El mensaje", 2, 1500)
        if not ai.available:
            await ctx.send(embed=warn_embed("IA no disponible", ai.unavailable_notice), ephemeral=True)
            return
        await ctx.channel.typing()
        cfg = await db.guild_config(ctx.guild.id)
        ai_cfg = cfg.get("ai") or {}
        conversation = await self._conversation(ctx.guild.id, ctx.author.id, ctx.channel.id)
        history = await self._context(
            int(conversation["id"]), int(ai_cfg.get("context_messages", 6))
        )
        base = await knowledge.knowledge_block(ctx.guild.id)
        system = (
            f"Eres KorianMC IA del servidor «{ctx.guild.name}». Personalidad: "
            f"{ai_cfg.get('personality', 'cercano, breve y profesional')}. Responde en español y sé breve.\n\n"
            f"BASE DE CONOCIMIENTO:\n{base}"
        )
        answer = await ai.chat(
            f"HISTORIAL:\n{history or '(sin mensajes previos)'}\n\nUsuario: {prompt}", system
        )
        if answer is None:
            await ctx.send(embed=error_embed("IA no disponible", ai.unavailable_notice))
            return
        await self._remember(conversation["id"], "user", prompt)
        await self._remember(conversation["id"], "assistant", answer)
        embed = base_embed("🤖 KorianMC IA", clean_text(answer, 3900))
        await ctx.send(embed=embed, view=EscalarView())

    @ia.command(name="resumir", description="Resume un texto o una página web.")
    @commands.guild_only()
    async def ia_resumir(self, ctx: commands.Context, texto: str) -> None:
        source = texto.strip()
        if is_valid_url(source):
            source = await self._fetch_url(source)
            if not source:
                await ctx.send(
                    embed=error_embed(
                        "No pude leer la página",
                        "La URL no devolvió contenido legible. Pega el texto directamente.",
                    )
                )
                return
        if len(source) < 40:
            await ctx.send(
                embed=error_embed("Texto muy corto", "Pega al menos unas líneas para resumir.")
            )
            return
        if not ai.available:
            await ctx.send(embed=warn_embed("IA no disponible", ai.unavailable_notice))
            return
        await ctx.channel.typing()
        summary = await ai.summarize(source)
        if summary is None:
            await ctx.send(embed=error_embed("IA no disponible", ai.unavailable_notice))
            return
        await ctx.send(embed=base_embed("📄 Resumen", clean_text(summary, 3900)))

    @ia.command(name="traducir", description="Traduce un texto a otro idioma.")
    @commands.guild_only()
    async def ia_traducir(
        self, ctx: commands.Context, texto: str, idioma: str = "inglés"
    ) -> None:
        text = require_text(texto, "El texto", 1, 1500)
        target = clean_text(idioma, 40) or "inglés"
        if not ai.available:
            await ctx.send(embed=warn_embed("IA no disponible", ai.unavailable_notice))
            return
        await ctx.channel.typing()
        translated = await ai.translate(text, target)
        if translated is None:
            await ctx.send(embed=error_embed("IA no disponible", ai.unavailable_notice))
            return
        embed = base_embed(f"🌐 Traducción al {target}", clean_text(translated, 3900))
        embed.add_field(name="Original", value=clean_text(text, 900), inline=False)
        await ctx.send(embed=embed)

    @ia.command(name="imagen", description="Genera una imagen con IA.")
    @commands.guild_only()
    async def ia_imagen(self, ctx: commands.Context, prompt: str) -> None:
        text = require_text(prompt, "La descripción", 3, 500)
        if not settings.gemini_available:
            await ctx.send(
                embed=warn_embed(
                    "IA no disponible",
                    "Falta `GEMINI_API_KEY` en el archivo `.env` para generar imágenes.",
                )
            )
            return
        await ctx.channel.typing()
        image = await ai.generate_image(text)
        if image is None:
            await ctx.send(
                embed=error_embed(
                    "No se pudo generar la imagen",
                    "El modelo de imágenes no está disponible con esta clave o la API falló.",
                )
            )
            return
        file = discord.File(bytes(image), filename="korianmc.png")
        embed = base_embed("🎨 Imagen generada", clean_text(text, 500))
        embed.set_image(url="attachment://korianmc.png")
        await ctx.send(embed=embed, file=file)

    @ia.command(name="moderar", description="Analiza un texto con IA (toxicidad y spam).")
    @commands.guild_only()
    async def ia_moderar(self, ctx: commands.Context, texto: str) -> None:
        text = require_text(texto, "El texto", 3, 1500)
        if not ai.available:
            await ctx.send(embed=warn_embed("IA no disponible", ai.unavailable_notice))
            return
        await ctx.channel.typing()
        report = await ai.moderate(text)
        if report is None:
            await ctx.send(
                embed=error_embed(
                    "No se pudo analizar",
                    "Gemini no devolvió un análisis válido. Inténtalo de nuevo.",
                )
            )
            return
        score = max(float(report["toxicidad"]), float(report["spam"]))
        verdict = "🟢 Apto" if score < 0.6 else "🟠 Cuestionable" if score < 0.8 else "🔴 Bloqueable"
        embed = base_embed(
            "🛡️ Análisis de moderación",
            f"**Resultado:** {verdict}\n"
            f"**Toxicidad:** {report['toxicidad']:.2f}\n"
            f"**Spam:** {report['spam']:.2f}\n"
            f"**Categoría:** {report['categoria']}\n"
            f"**Detalle:** {report['detalle'] or 'Sin observaciones'}",
        )
        await ctx.send(embed=embed)

    @ia.command(name="soporte", description="Resuelve una duda frecuente del servidor con IA.")
    @commands.guild_only()
    async def ia_soporte(self, ctx: commands.Context, pregunta: str) -> None:
        question = require_text(pregunta, "La pregunta", 3, 600)
        local = await knowledge.answer(ctx.guild.id, question)
        if local:
            embed = base_embed("💡 Soporte del servidor", clean_text(local, 3900))
            embed.set_footer(text="Respuesta de la base de conocimiento (FAQ)")
            await ctx.send(embed=embed)
            return
        if not ai.available:
            await ctx.send(embed=warn_embed("IA no disponible", ai.unavailable_notice))
            return
        await ctx.channel.typing()
        rules = await knowledge.support_prompt(ctx.guild.id)
        system = (
            f"Eres el soporte del servidor «{ctx.guild.name}». Responde en español, breve y "
            f"exacto. Si no está en las reglas, dilo y sugiere escalar a un humano.\n\nREGLAS:\n{rules}"
        )
        answer = await ai.chat(question, system)
        if answer is None:
            await ctx.send(embed=error_embed("IA no disponible", ai.unavailable_notice))
            return
        await ctx.send(embed=base_embed("🛟 Soporte IA", clean_text(answer, 3900)), view=EscalarView())

    # ------------------------------------------------------------------
    @commands.hybrid_group(
        name="iaconfig",
        description="[Admin] Configura KorianMC IA.",
        invoke_without_command=True,
    )
    @admin_only()
    async def iaconfig(
        self,
        ctx: commands.Context,
        canal: Optional[discord.TextChannel] = None,
        personalidad: Optional[str] = None,
        cooldown: Optional[int] = None,
        cuota: Optional[int] = None,
    ) -> None:
        updates: dict[str, Any] = {}
        if canal is not None:
            updates["channels.ai_support"] = str(canal.id)
        if personalidad is not None:
            updates["ai.personality"] = clean_text(personalidad, 200)
        if cooldown is not None:
            if not 0 <= cooldown <= 600:
                await ctx.send(
                    embed=error_embed("Cooldown no válido", "Usa un valor entre **0** y **600** segundos.")
                )
                return
            updates["ai.cooldown_seconds"] = int(cooldown)
        if cuota is not None:
            if not 0 <= cuota <= 500:
                await ctx.send(
                    embed=error_embed("Cuota no válida", "Usa un valor entre **0** y **500** al día.")
                )
                return
            updates["ai.max_daily_messages"] = int(cuota)
        if not updates:
            cfg = await db.guild_config(ctx.guild.id)
            ai_cfg = cfg.get("ai") or {}
            support = channel_id(cfg, "ai_support")
            support_text = f"<#{support}>" if support else "sin configurar"
            embed = base_embed(
                "🤖 Configuración de KorianMC IA",
                f"**Disponible:** {'sí' if ai.available else 'no — ' + ai.reason}\n"
                f"**Canal de soporte:** {support_text}\n"
                f"**Personalidad:** {ai_cfg.get('personality', '—')}\n"
                f"**Cooldown:** {ai_cfg.get('cooldown_seconds')}s\n"
                f"**Contexto:** {ai_cfg.get('context_messages')} mensajes\n"
                f"**Cuota diaria:** {ai_cfg.get('max_daily_messages')}\n"
                f"**Escalado a humano:** {'activado' if ai_cfg.get('escalate') else 'desactivado'}\n"
                f"**Moderación IA:** {'activada' if ai_cfg.get('auto_moderation') else 'desactivada'} "
                f"(umbral {ai_cfg.get('moderation_threshold')})",
            )
            await ctx.send(embed=embed)
            return
        await db.set_configs(ctx.guild.id, updates)
        await ctx.send(
            embed=success_embed(
                "Configuración guardada",
                "\n".join(f"• `{key}` = `{value}`" for key, value in updates.items()),
            )
        )

    @iaconfig.command(name="reset", description="[Admin] Reinicia los límites de IA de un usuario.")
    @admin_only()
    async def iaconfig_reset(self, ctx: commands.Context, usuario: discord.Member) -> None:
        limiter.reset_user(ctx.guild.id, usuario.id)
        await ctx.send(
            embed=success_embed(
                "Límites reiniciados",
                f"**{usuario.display_name}** puede volver a usar KorianMC IA sin restricciones.",
            )
        )

    # ------------------------------------------------------------------
    async def _fetch_url(self, url: str) -> str:
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers={"User-Agent": "KorianMC-Bot/1.0"}) as response:
                    if response.status != 200:
                        return ""
                    html = await response.text(errors="ignore")
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            log.warning("No se pudo leer la URL: %s", exc)
            return ""
        import re

        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = " ".join(text.split())
        return text[:8000]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(IAKorianMC(bot))
