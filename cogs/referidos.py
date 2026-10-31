"""Sistema de referidos: código de invitación, canje y bonificaciones."""

from __future__ import annotations

import secrets
from typing import Any

import discord
from discord.ext import commands

from core.database import db
from core.logging_cfg import get_logger
from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.helpers import Paginator
from utils.validators import clean_text

log = get_logger("referidos")

CODE_PREFIX = "KOR"
CODE_LENGTH = 6


async def ensure_referral_code(guild_id: Any, user_id: Any) -> str:
    """Genera y guarda un código de referido si el usuario aún no tiene."""
    row = await db.fetchone(
        "SELECT referral_code FROM users WHERE guild_id = ? AND user_id = ?",
        (str(guild_id), str(user_id)),
    )
    if row and row.get("referral_code"):
        return str(row["referral_code"])
    code = f"{CODE_PREFIX}-{secrets.token_hex(CODE_LENGTH // 2).upper()}"
    await db.ensure_user(guild_id, user_id)
    await db.execute(
        "UPDATE users SET referral_code = ? WHERE guild_id = ? AND user_id = ?",
        (code, str(guild_id), str(user_id)),
    )
    return code


class Referidos(commands.Cog):
    """Códigos de invitación con bonificación de puntos."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_group(name="referir", description="Sistema de referidos.", invoke_without_command=True)
    @commands.guild_only()
    async def referir(self, ctx: commands.Context) -> None:
        code = await ensure_referral_code(ctx.guild.id, ctx.author.id)
        invited = await db.scalar(
            "SELECT COUNT(*) FROM referrals WHERE guild_id = ? AND referrer_id = ?",
            (str(ctx.guild.id), str(ctx.author.id)),
            default=0,
        )
        cfg = await db.guild_config(ctx.guild.id)
        bonus = int((cfg.get("points") or {}).get("referral_bonus", 100))
        invite = await self._invite_link(ctx.guild)
        embed = base_embed(
            "🔗 Tu código de referido",
            f"**Código:** `{code}`\n"
            f"**Usos:** {invited}\n"
            f"**Recompensa:** {bonus} puntos por cada amigo que canjee tu código.\n\n"
            f"Invitación: {invite}\n"
            f"Tu amigo debe escribir: `/referir canjear codigo:{code}`",
        )
        await ctx.send(embed=embed)

    @referir.command(name="canjear", description="Canjea el código de referido de un amigo.")
    @commands.guild_only()
    async def referir_canjear(self, ctx: commands.Context, codigo: str) -> None:
        code = clean_text(codigo, 30).upper()
        owner = await db.fetchone(
            "SELECT user_id FROM users WHERE guild_id = ? AND referral_code = ?",
            (str(ctx.guild.id), code),
        )
        if owner is None:
            await ctx.send(embed=error_embed("Código no válido", "No existe ese código de referido."))
            return
        referrer_id = str(owner["user_id"])
        if referrer_id == str(ctx.author.id):
            await ctx.send(
                embed=error_embed("No permitido", "No puedes canjear tu propio código.")
            )
            return

        existing = await db.fetchone(
            "SELECT 1 FROM referrals WHERE guild_id = ? AND referred_id = ?",
            (str(ctx.guild.id), str(ctx.author.id)),
        )
        if existing is not None:
            await ctx.send(
                embed=warn_embed(
                    "Ya canjeado",
                    "Ese código (u otro) ya fue canjeado por esta cuenta.",
                )
            )
            return

        cfg = await db.guild_config(ctx.guild.id)
        bonus = int((cfg.get("points") or {}).get("referral_bonus", 100))
        try:
            await db.execute(
                "INSERT INTO referrals (guild_id, referrer_id, referred_id, bonus) VALUES (?, ?, ?, ?)",
                (str(ctx.guild.id), referrer_id, str(ctx.author.id), bonus),
            )
        except Exception:
            await ctx.send(embed=error_embed("No se pudo registrar", "Inténtalo de nuevo en unos segundos."))
            return

        await db.add_points(ctx.guild.id, referrer_id, bonus, f"referido de {ctx.author}", ctx.author.id)
        await db.add_points(ctx.guild.id, ctx.author.id, bonus // 2, f"código canjeado de {referrer_id}")
        await db.log_event(
            ctx.guild.id,
            "referido",
            user_id=ctx.author.id,
            target_id=referrer_id,
            content=f"{code} (+{bonus} pts)",
        )
        await ctx.send(
            embed=success_embed(
                "🎉 Código canjeado",
                f"¡Gracias! <@{referrer_id}> recibió **{bonus}** puntos y tú **{bonus // 2}**.",
            )
        )

    @referir.command(name="top", description="Top de referidos del servidor.")
    @commands.guild_only()
    async def referir_top(self, ctx: commands.Context) -> None:
        rows = await db.fetchall(
            "SELECT referrer_id, COUNT(*) AS total FROM referrals WHERE guild_id = ? "
            "GROUP BY referrer_id ORDER BY total DESC LIMIT 20",
            (str(ctx.guild.id),),
        )
        if not rows:
            await ctx.send(
                embed=base_embed("🔗 Top de referidos", "Todavía no se ha referido a nadie.")
            )
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = [
            f"{medals[index] if index < 3 else f'`#{index + 1}`'} <@{row['referrer_id']}> — {row['total']} referido(s)"
            for index, row in enumerate(rows)
        ]
        embeds = [
            base_embed("🔗 Top de referidos", "\n".join(lines[start : start + 15]))
            for start in range(0, len(lines), 15)
        ]
        for page, embed in enumerate(embeds, start=1):
            embed.set_footer(text=f"Página {page}/{len(embeds)}")
        await Paginator(embeds, user_id=ctx.author.id).start(ctx)

    async def _invite_link(self, guild: discord.Guild) -> str:
        try:
            invite = await guild.vanity_invite()
            if invite is not None:
                return str(invite)
        except (discord.HTTPException, AttributeError):
            log.info("No se pudo leer la invitación de vanidad del servidor.")
        return "https://discord.gg/" + (guild.name or "servidor").replace(" ", "")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Referidos(bot))
