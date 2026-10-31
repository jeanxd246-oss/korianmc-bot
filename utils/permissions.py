"""Permisos del bot: siempre por ID de rol, nunca por nombre."""

from __future__ import annotations

from typing import Any, Iterable, Sequence, Union

import discord


def _ids(values: Any) -> list[int]:
    if not values:
        return []
    if isinstance(values, (str, int)):
        values = [values]
    out: list[int] = []
    for value in values:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def role_ids(cfg: dict[str, Any], key: str) -> list[int]:
    """Lee `roles.<key>` de la configuración del servidor como lista de IDs."""
    roles = cfg.get("roles", {}) if isinstance(cfg, dict) else {}
    return _ids(roles.get(key, []))


def channel_id(cfg: dict[str, Any], key: str) -> int | None:
    channels = cfg.get("channels", {}) if isinstance(cfg, dict) else {}
    raw = channels.get(key)
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def has_any_role(member: Union[discord.Member, discord.User], ids: Sequence[int]) -> bool:
    if not ids:
        return False
    if isinstance(member, discord.Member):
        member_ids = {role.id for role in member.roles}
        return any(role_id in member_ids for role_id in ids)
    return False


def is_admin(member: discord.Member, cfg: dict[str, Any]) -> bool:
    """Equipo administrador: permiso de administrador o rol admin configurado."""
    if member.guild_permissions.administrator:
        return True
    return has_any_role(member, role_ids(cfg, "admin"))


def is_staff(member: discord.Member, cfg: dict[str, Any]) -> bool:
    """Staff: admin, rol staff configurado o permisos de gestión del servidor."""
    if is_admin(member, cfg):
        return True
    if member.guild_permissions.manage_guild or member.guild_permissions.manage_roles:
        return True
    return has_any_role(member, role_ids(cfg, "staff"))


def can_moderate(member: discord.Member, cfg: dict[str, Any]) -> bool:
    """Quien puede sancionar: staff con permisos de moderación."""
    if is_staff(member, cfg):
        return True
    return member.guild_permissions.ban_members or member.guild_permissions.kick_members


def member_can_manage_roles(member: discord.Member) -> bool:
    return member.guild_permissions.manage_roles


def bot_can_manage_roles(guild: discord.Guild, role: discord.Role) -> bool:
    bot_member = guild.me
    if bot_member is None:
        return False
    return bot_member.guild_permissions.manage_roles and bot_member.top_role > role


def role_position_ok(guild: discord.Guild, role: discord.Role) -> bool:
    return bot_can_manage_roles(guild, role)


def ids_from_text(text: Union[str, Sequence[str], None]) -> list[int]:
    """'<@&123>, 456' -> [123, 456]."""
    if not text:
        return []
    if isinstance(text, str):
        parts = [text]
    else:
        parts = list(text)
    out: list[int] = []
    for part in parts:
        for token in str(part).replace("<@&", "").replace(">", "").replace(",", " ").split():
            if token.isdigit():
                out.append(int(token))
    return list(dict.fromkeys(out))


def unique_ints(values: Iterable[int]) -> list[int]:
    return list(dict.fromkeys(values))
