"""Protección anti-raid: detección de uniones masivas, /raidmode y verificación."""

from __future__ import annotations

import time
from collections import deque
from datetime import timedelta
from typing import Any

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from services.embed_factory import moderation_log_embed
from services.notification import log_action
from utils.checks import admin_only
from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.timeformat import format_duration
from utils.validators import clean_text

log = get_logger("antiraid")

VERIFY_CUSTOM_ID = "verificacion:boton"


def account_age(member: discord.Member) -> timedelta:
    """Antigüedad de la cuenta (no existe `Member.age` en discord.py)."""
    return discord.utils.utcnow() - member.created_at


class VerifyView(discord.ui.View):
    """Botón persistente de verificación anti-raid."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label="Verificarme",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=VERIFY_CUSTOM_ID,
        )
        button.callback = self._verify
        self.add_item(button)

    async def _verify(self, interaction: discord.Interaction) -> None:
        cfg = await db.guild_config(interaction.guild_id)
        raid = cfg.get("antiraid") or {}
        role_id = (cfg.get("roles") or {}).get("verification")
        if not role_id:
            await interaction.response.send_message(
                embed=error_embed(
                    "Verificación no configurada",
                    "El staff aún no ha definido el rol de verificación (`/setup verification`).",
                ),
                ephemeral=True,
            )
            return
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Usa este botón dentro del servidor.", ephemeral=True)
            return
        role = interaction.guild.get_role(int(role_id)) if interaction.guild else None
        if role is None:
            await interaction.response.send_message(
                embed=error_embed("Rol no encontrado", "El rol de verificación ya no existe."),
                ephemeral=True,
            )
            return

        try:
            if member.timed_out:
                await member.timeout(None, reason="Verificación anti-raid completada")
            if role not in member.roles and interaction.guild and interaction.guild.me:
                if interaction.guild.me.top_role > role:
                    await member.add_roles(role, reason="Verificación anti-raid")
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                embed=error_embed("No se pudo verificar", str(exc)), ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=success_embed(
                "✅ Verificado",
                "Gracias por verificar tu cuenta. Ya puedes participar en el servidor.",
            ),
            ephemeral=True,
        )
        await log_action(
            interaction.guild,
            "verificacion",
            cfg=cfg,
            user_id=member.id,
            content=f"{member} se verificó (raidmode: {'sí' if raid.get('raidmode') else 'no'})",
        )


class Antiraid(commands.Cog):
    """Detección de raid y modo protección con verificación."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._joins: dict[int, deque[float]] = {}
        self._last_alert: dict[int, float] = {}

    async def register_views(self) -> None:
        self.bot.add_view(VerifyView())

    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        cfg = await db.guild_config(member.guild.id)
        raid = cfg.get("antiraid") or {}
        now = time.monotonic()

        # 1) Uniones masivas en ventana corta
        window = max(3, int(raid.get("window_seconds", 10)))
        bucket = [stamp for stamp in self._joins.get(member.guild.id, deque()) if now - stamp <= window]
        bucket.append(now)
        self._joins[member.guild.id] = deque(bucket, maxlen=50)

        account_age_delta = account_age(member)
        min_age_days = int(raid.get("min_account_age_days", 7))
        young_account = account_age_delta < timedelta(days=min_age_days)
        burst = len(bucket) >= max(3, int(raid.get("max_joins", 5)))

        # 2) Cuentas nuevas entrando en masa => alerta + bloqueo temporal
        if young_account and (burst or raid.get("raidmode", False)):
            await self._handle_suspicious(member, cfg, raid, bucket)
            return

        if burst:
            await self._alert(member.guild, cfg, len(bucket), window)

        # 3) Modo protección activo: toda cuenta nueva queda temporalmente retenida
        if raid.get("raidmode", False) and not young_account:
            await self._alert(member.guild, cfg, len(bucket), window)

    async def _handle_suspicious(
        self, member: discord.Member, cfg: dict[str, Any], raid: dict[str, Any], bucket: list[float]
    ) -> None:
        reason = "Anti-raid: cuenta nueva pendiente de verificación"
        timeout_done = False
        if member.guild.me and member.guild.me.guild_permissions.moderate_members:
            try:
                await member.timeout(discord.utils.utcnow() + timedelta(hours=1), reason=reason)
                timeout_done = True
            except discord.HTTPException as exc:
                log.warning("No se pudo retener a %s: %s", member, exc)

        await self._alert(member.guild, cfg, len(bucket), int(raid.get("window_seconds", 10)))

        await log_action(
            member.guild,
            "antiraid",
            cfg=cfg,
            embed=moderation_log_embed(
                "antiraid",
                self.bot.user or member,
                member,
                f"Cuenta de {format_duration(account_age(member).total_seconds())} retenida "
                f"({'silenciada' if timeout_done else 'sin sanción aplicada'})",
            ),
            user_id=member.id,
        )

        channel_id = (cfg.get("channels") or {}).get("verification")
        text = clean_text(raid.get("lock_message", ""), 300)
        try:
            await member.send(
                f"🛡️ Bienvenido a **{member.guild.name}**. Tu cuenta es reciente, así que "
                f"pasa por el canal de verificación para participar.\n{text}".strip()
            )
        except (discord.Forbidden, discord.HTTPException):
            log.info("No se pudo DM al miembro retenido.")
        if channel_id:
            channel = member.guild.get_channel(int(channel_id))
            if isinstance(channel, discord.TextChannel):
                await channel.send(
                    content=member.mention,
                    embed=base_embed(
                        "🛡️ Verificación pendiente",
                        f"**{member}** (cuenta de {format_duration(account_age(member).total_seconds())}) "
                        "debe verificarse para participar.",
                    ),
                    view=VerifyView(),
                )

    async def _alert(
        self, guild: discord.Guild, cfg: dict[str, Any], count: int, window: int
    ) -> None:
        now = time.monotonic()
        if now - self._last_alert.get(guild.id, 0.0) < 30:
            return
        self._last_alert[guild.id] = now
        await log_action(
            guild,
            "alerta_raid",
            cfg=cfg,
            embed=warn_embed(
                "🛡️ Posible raid detectado",
                f"**{count}** uniones en **{window}** segundos.\n"
                "Activa el modo protección con `/raidmode on` si es necesario.",
            ),
        )

    # ------------------------------------------------------------------
    @commands.hybrid_group(name="raidmode", description="Modo protección anti-raid.", invoke_without_command=True)
    @admin_only()
    async def raidmode(self, ctx: commands.Context) -> None:
        cfg = await db.guild_config(ctx.guild.id)
        raid = cfg.get("antiraid") or {}
        await ctx.send(
            embed=base_embed(
                "🛡️ Estado del anti-raid",
                f"**Modo protección:** {'ACTIVO' if raid.get('raidmode') else 'inactivo'}\n"
                f"**Detección anti-raid:** {'activa' if raid.get('enabled') else 'inactiva'}\n"
                f"**Umbral:** {raid.get('max_joins')} uniones / {raid.get('window_seconds')}s\n"
                f"**Edad mínima de cuenta:** {raid.get('min_account_age_days')} días\n\n"
                "Usa `/raidmode on` o `/raidmode off`.",
            )
        )

    @raidmode.command(name="on", description="Activa el modo protección anti-raid.")
    @admin_only()
    async def raidmode_on(self, ctx: commands.Context) -> None:
        await db.set_configs(
            ctx.guild.id, {"antiraid.raidmode": True, "antiraid.enabled": True}
        )
        cfg = await db.guild_config(ctx.guild.id)
        await log_action(
            ctx.guild,
            "raidmode_on",
            cfg=cfg,
            embed=warn_embed(
                "🛡️ Modo protección ACTIVADO",
                f"Activado por {ctx.author.mention}. Las cuentas nuevas deberán verificarse.",
            ),
            user_id=ctx.author.id,
        )
        await ctx.send(
            embed=success_embed(
                "🛡️ Raidmode activado",
                "Las cuentas nuevas quedan retenidas hasta verificarse con el botón del canal.",
            )
        )

    @raidmode.command(name="off", description="Desactiva el modo protección anti-raid.")
    @admin_only()
    async def raidmode_off(self, ctx: commands.Context) -> None:
        await db.set_configs(
            ctx.guild.id, {"antiraid.raidmode": False, "antiraid.enabled": False}
        )
        await log_action(
            ctx.guild,
            "raidmode_off",
            embed=success_embed(
                "🛡️ Modo protección DESACTIVADO",
                f"Desactivado por {ctx.author.mention}.",
            ),
            user_id=ctx.author.id,
        )
        await ctx.send(embed=success_embed("🛡️ Raidmode desactivado", "El servidor vuelve a la normalidad."))

    @raidmode.command(name="verify", description="Publica el panel de verificación en el canal.")
    @admin_only()
    async def raidmode_verify(self, ctx: commands.Context) -> None:
        embed = base_embed(
            "✅ Verificación",
            "Pulsa el botón para verificar tu cuenta y poder participar en el servidor.\n"
            "Es obligatorio si el servidor está en modo protección anti-raid.",
        )
        await ctx.send(embed=embed, view=VerifyView())

    @commands.hybrid_command(name="raidstatus", description="[Staff] Estado actual de la protección anti-raid.")
    async def raidstatus(self, ctx: commands.Context) -> None:
        cfg = await db.guild_config(ctx.guild.id)
        if not await ensure_staff_interaction_guard(ctx, cfg):
            return
        bucket = self._joins.get(ctx.guild.id, deque())
        embed = base_embed(
            "🛡️ Anti-raid",
            f"**Uniones recientes (30s):** {len(bucket)}\n"
            f"**Modo protección:** {'ACTIVO' if (cfg.get('antiraid') or {}).get('raidmode') else 'inactivo'}",
        )
        await ctx.send(embed=embed)


async def ensure_staff_interaction_guard(ctx: commands.Context, cfg: dict[str, Any]) -> bool:
    """Comprobación de staff para comandos híbridos que no llevan decorador."""
    from utils.permissions import is_staff

    if is_staff(ctx.author, cfg):
        return True
    await ctx.send(embed=error_embed("Permisos", "Necesitas el rol de staff para ver el estado."))
    return False


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Antiraid(bot))
