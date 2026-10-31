"""Embeds con estilo premium oscuro y color de marca #5865F2."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

import discord

from core.config import BOT_SECTION, color_int, settings


def brand_color(explicit: Any = None) -> discord.Color:
    """Color principal: valor dado > color del servidor > #5865F2."""
    if explicit:
        return discord.Color(color_int(explicit, settings.default_color))
    return discord.Color(settings.default_color)


def guild_color(cfg: Optional[dict[str, Any]] = None) -> discord.Color:
    if cfg is None:
        return brand_color()
    appearance = cfg.get("appearance") or {}
    return brand_color(appearance.get("color"))


def base_embed(
    title: str,
    description: str = "",
    *,
    color: Any = None,
    cfg: Optional[dict[str, Any]] = None,
) -> discord.Embed:
    """Embed base con el estilo del bot: título, color de marca y timestamp."""
    embed = discord.Embed(title=title, description=description or None, color=brand_color(color))
    if cfg is not None:
        embed.color = guild_color(cfg)
    embed.timestamp = datetime.utcnow()
    embed.set_footer(
        text=f"{settings.bot_name} · {BOT_SECTION.get('language', 'es').upper()}"
    )
    return embed


def success_embed(title: str, description: str = "", **kwargs: Any) -> discord.Embed:
    embed = base_embed(f"✅ {title}", description, **kwargs)
    return embed


def error_embed(title: str, description: str = "", **kwargs: Any) -> discord.Embed:
    return base_embed(f"❌ {title}", description, **kwargs)


def info_embed(title: str, description: str = "", **kwargs: Any) -> discord.Embed:
    return base_embed(f"ℹ️ {title}", description, **kwargs)


def warn_embed(title: str, description: str = "", **kwargs: Any) -> discord.Embed:
    return base_embed(f"⚠️ {title}", description, **kwargs)


def add_fields(embed: discord.Embed, fields: Sequence[tuple[str, Any, bool]]) -> discord.Embed:
    """Añade campos '(nombre, valor, inline)' ignorando valores vacíos."""
    for name, value, inline in fields:
        if value in (None, "", []):
            continue
        embed.add_field(name=name, value=str(value), inline=inline)
    return embed


def progress_bar(current: float, total: float, size: int = 12) -> str:
    """Barra de progreso con caracteres de bloque."""
    if total <= 0:
        total = 1
    ratio = max(0.0, min(1.0, current / total))
    filled = round(ratio * size)
    return f"{'█' * filled}{'░' * (size - filled)}"


def pages_from_items(
    items: Sequence[Any],
    per_page: int,
    title: str,
    formatter,
    *,
    color: Any = None,
    empty: str = "No hay elementos todavía.",
    footer_prefix: str = "",
) -> list[discord.Embed]:
    """Divide elementos en embeds paginados usando `formatter(item, index)`."""
    if not items:
        return [base_embed(title, empty, color=color)]
    pages: list[discord.Embed] = []
    for start in range(0, len(items), per_page):
        chunk = items[start : start + per_page]
        lines = [
            formatter(item, start + offset + 1) for offset, item in enumerate(chunk)
        ]
        embed = base_embed(title, "\n".join(lines), color=color)
        page_number = len(pages) + 1
        total_pages = (len(items) + per_page - 1) // per_page
        embed.set_footer(text=f"{footer_prefix}Página {page_number}/{total_pages}".strip())
        pages.append(embed)
    return pages
