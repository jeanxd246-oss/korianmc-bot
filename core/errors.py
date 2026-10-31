"""Excepciones propias y manejador único de errores de comandos.

Cualquier fallo termina en un mensaje amable en español; el detalle técnico
solo va al log de archivo y a la tabla `error_logs`.
"""

from __future__ import annotations

import traceback
from typing import Optional, Union

import discord
from discord import app_commands
from discord.ext import commands

from core.logging_cfg import get_logger

log = get_logger("errores")

Dest = Union["commands.Context", discord.Interaction]


# ---------------------------------------------------------------------------
# Excepciones propias
# ---------------------------------------------------------------------------
class KorianError(Exception):
    """Error con mensaje pensado para mostrarlo al usuario."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationError(KorianError):
    """Entrada inválida proporcionada por el usuario."""


class PermissionDenied(KorianError):
    """El usuario no tiene permisos para la acción solicitada."""


class ConfigMissing(KorianError):
    """Falta configurar algo con /setup antes de usar el comando."""


class AIUnavailable(KorianError):
    """Gemini no está disponible (sin clave o con fallo de la API)."""


class CheckFailed(commands.CheckFailure, app_commands.CheckFailure):
    """Fallo de permisos reconocible tanto por prefijo como por slash."""

    def __init__(self, message: str = "No tienes permisos para usar este comando.") -> None:
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
async def respond(
    dest: Dest,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
    ephemeral: bool = True,
) -> None:
    """Responde a un Context (prefijo) o a una Interacción (slash/componentes)."""
    try:
        if isinstance(dest, discord.Interaction):
            if dest.response.is_done():
                await dest.followup.send(
                    content=content, embed=embed, view=view, ephemeral=ephemeral
                )
            else:
                await dest.response.send_message(
                    content=content, embed=embed, view=view, ephemeral=ephemeral
                )
        else:
            await dest.send(content=content, embed=embed, view=view)
    except discord.HTTPException:
        log.warning("No se pudo responder al usuario en %s", type(dest).__name__)


def _description(error: BaseException) -> str:
    message = getattr(error, "message", None)
    if message:
        return str(message)
    return str(error) or "Error sin descripción."


def _unwrap(error: BaseException) -> BaseException:
    """Descapa HybridCommandError / CommandInvokeError hasta el error real."""
    seen: set[int] = set()
    current = error
    while id(current) not in seen:
        seen.add(id(current))
        original = getattr(current, "original", None)
        if isinstance(original, BaseException):
            current = original
            continue
        inner = getattr(current, "error", None)
        if isinstance(inner, BaseException) and inner is not current:
            current = inner
            continue
        break
    return current


async def _log_technical(dest: Dest, error: BaseException, context_label: str) -> None:
    text = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    log.error("Error no controlado en %s:\n%s", context_label, text)
    try:
        from core.database import db

        await db.log_error(context_label, f"{type(error).__name__}: {error}", text)
    except Exception:
        log.warning("No se pudo registrar el error en la base de datos.")


async def handle_error(dest: Dest, error: BaseException, context_label: str = "comando") -> None:
    """Convierte cualquier excepción en un mensaje de usuario en español."""
    from utils.embeds import error_embed, warn_embed

    error = _unwrap(error)

    if isinstance(error, (commands.CommandNotFound, app_commands.CommandNotFound)):
        return

    if isinstance(error, (KorianError, CheckFailed, commands.CheckFailure, app_commands.CheckFailure)):
        embed = warn_embed("No se pudo ejecutar", _description(error))
        await respond(dest, embed=embed, ephemeral=True)
        return

    if isinstance(error, commands.MissingRequiredArgument):
        name = error.param.name
        embed = warn_embed(
            "Falta información",
            f"Falta el argumento obligatorio `{name}`.\n"
            f"Usa `/help` para ver la sintaxis completa del comando.",
        )
        await respond(dest, embed=embed, ephemeral=True)
        return

    if isinstance(error, (commands.BadArgument, app_commands.AppCommandError)):
        if isinstance(error, commands.CheckFailure) or isinstance(error, app_commands.CheckFailure):
            embed = warn_embed("No se pudo ejecutar", _description(error))
        elif isinstance(error, commands.BadArgument):
            embed = warn_embed("Valor no válido", _description(error))
        else:
            await _log_technical(dest, error, context_label)
            embed = error_embed(
                "Parámetros inválidos",
                "Discord no entendió alguno de los valores enviados. Revisa los datos e inténtalo de nuevo.",
            )
        await respond(dest, embed=embed, ephemeral=True)
        return

    if isinstance(error, (commands.NotOwner, commands.MissingPermissions, app_commands.MissingPermissions)):
        embed = warn_embed(
            "Permisos insuficientes",
            "Tu rol no tiene permisos suficientes para este comando.",
        )
        await respond(dest, embed=embed, ephemeral=True)
        return

    if isinstance(error, commands.CommandOnCooldown):
        embed = warn_embed(
            "Espera un momento",
            f"Cooldown activo: vuelve a intentarlo en {error.retry_after:.0f} segundos.",
        )
        await respond(dest, embed=embed, ephemeral=True)
        return

    if isinstance(error, commands.DisabledCommand):
        embed = warn_embed("Comando desactivado", "Este comando está desactivado ahora mismo.")
        await respond(dest, embed=embed, ephemeral=True)
        return

    await _log_technical(dest, error, context_label)
    embed = error_embed(
        "Algo salió mal",
        "Ocurrió un error inesperado mientras ejecutaba el comando. "
        "El incidente quedó registrado en los logs del bot.",
    )
    await respond(dest, embed=embed, ephemeral=True)


async def handle_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    """Manejador para comandos con prefijo (`!help`, `/help` escrito a mano)."""
    label = f"prefijo {ctx.command.qualified_name if ctx.command else ctx.invoked_with}"
    await handle_error(ctx, error, label)


async def handle_tree_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    """Manejador global para slash commands y menús contextuales."""
    command = interaction.command
    name = getattr(command, "qualified_name", None) or "slash"
    await handle_error(interaction, error, f"slash /{name}")


__all__ = [
    "KorianError",
    "ValidationError",
    "PermissionDenied",
    "ConfigMissing",
    "AIUnavailable",
    "CheckFailed",
    "respond",
    "handle_error",
    "handle_command_error",
    "handle_tree_error",
]
