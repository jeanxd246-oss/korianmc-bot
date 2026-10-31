"""Comprobaciones de permisos reutilizables para comandos híbridos.

Se usan por debajo del decorador del comando:

    @commands.hybrid_command()
    @admin_only()
    async def producto(ctx): ...

Trabajan igual con prefijo (`!producto`) y con slash (`/producto`).
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import discord
from discord.ext import commands

from core.database import db
from core.errors import CheckFailed
from utils.permissions import is_admin, is_staff


async def _config(guild: Optional[discord.Guild]) -> dict[str, Any]:
    if guild is None:
        return {}
    return await db.guild_config(guild.id)


async def admin_check(ctx: commands.Context) -> bool:
    """Solo administradores del servidor (rol admin o permiso de administrador)."""
    if ctx.guild is None:
        raise CheckFailed("Este comando solo funciona dentro de un servidor.")
    cfg = await _config(ctx.guild)
    if not is_admin(ctx.author, cfg):
        raise CheckFailed("Necesitas el rol de **administrador** para usar este comando.")
    return True


async def staff_check(ctx: commands.Context) -> bool:
    """Staff (soporte, moderación y administración)."""
    if ctx.guild is None:
        raise CheckFailed("Este comando solo funciona dentro de un servidor.")
    cfg = await _config(ctx.guild)
    if not is_staff(ctx.author, cfg):
        raise CheckFailed("Necesitas el rol de **staff** para usar este comando.")
    return True


def admin_only() -> Callable[[Any], Any]:
    """Decorador: requiere administrador y contexto de servidor."""

    def decorator(func: Any) -> Any:
        func = commands.check(admin_check)(func)
        return commands.guild_only()(func)

    return decorator


def staff_only() -> Callable[[Any], Any]:
    """Decorador: requiere staff y contexto de servidor."""

    def decorator(func: Any) -> Any:
        func = commands.check(staff_check)(func)
        return commands.guild_only()(func)

    return decorator


async def member_is_admin(member: discord.Member, cfg: dict[str, Any]) -> bool:
    return is_admin(member, cfg)


async def member_is_staff(member: discord.Member, cfg: dict[str, Any]) -> bool:
    return is_staff(member, cfg)


async def ensure_staff_interaction(interaction: discord.Interaction, cfg: dict[str, Any]) -> bool:
    """Validación de permisos dentro de callbacks de botones y menús."""
    member = interaction.user
    if not isinstance(member, discord.Member):
        await interaction.response.send_message(
            "Este botón solo funciona dentro de un servidor.", ephemeral=True
        )
        return False
    if not is_staff(member, cfg) and not is_admin(member, cfg):
        await interaction.response.send_message(
            "Necesitas el rol de **staff** para hacer esto.", ephemeral=True
        )
        return False
    return True


async def ensure_admin_interaction(interaction: discord.Interaction, cfg: dict[str, Any]) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member):
        await interaction.response.send_message(
            "Este botón solo funciona dentro de un servidor.", ephemeral=True
        )
        return False
    if not is_admin(member, cfg):
        await interaction.response.send_message(
            "Necesitas el rol de **administrador** para hacer esto.", ephemeral=True
        )
        return False
    return True


__all__ = [
    "admin_check",
    "staff_check",
    "admin_only",
    "staff_only",
    "ensure_staff_interaction",
    "ensure_admin_interaction",
    "member_is_admin",
    "member_is_staff",
]
