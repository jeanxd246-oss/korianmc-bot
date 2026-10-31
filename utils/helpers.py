"""Utilidades compartidas: confirmaciones, paginación y envío seguro de mensajes."""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable, Optional, Sequence

import discord

from core.logging_cfg import get_logger

log = get_logger("helpers")

InteractionOrMessageUser = int

ConfirmCallback = Callable[[discord.Interaction], Awaitable[Any]]


def truncate(text: Optional[str], limit: int = 1000, suffix: str = "…") -> str:
    if not text:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def chunk_text(text: str, size: int = 1900) -> list[str]:
    """Parte un texto largo en trozos que caben en un embed."""
    if len(text) <= size:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > size:
            parts.append(current)
            current = ""
        current += line
    if current:
        parts.append(current)
    return parts


def mention_or_name(user: discord.abc.Snowflake) -> str:
    return getattr(user, "mention", None) or getattr(user, "name", str(user))


def human_join(items: Sequence[str], conjunction: str = "y") -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} {conjunction} {items[-1]}"


async def safe_send(channel: discord.abc.Messageable, **kwargs: Any) -> Optional[discord.Message]:
    """Envía un mensaje evitando que un fallo aislado rompa el comando."""
    try:
        return await channel.send(**kwargs)
    except (discord.Forbidden, discord.HTTPException) as exc:
        log.warning("No se pudo enviar un mensaje en %s: %s", getattr(channel, "id", "?"), exc)
        return None


async def safe_edit(message: Optional[discord.Message], **kwargs: Any) -> None:
    if message is None:
        return
    try:
        await message.edit(**kwargs)
    except (discord.Forbidden, discord.HTTPException) as exc:
        log.warning("No se pudo editar el mensaje %s: %s", getattr(message, "id", "?"), exc)


async def safe_delete(message: Optional[discord.Message]) -> None:
    if message is None:
        return
    try:
        await message.delete()
    except (discord.Forbidden, discord.HTTPException, discord.NotFound):
        log.debug("No se pudo borrar el mensaje %s", getattr(message, "id", "?"))


class ConfirmView(discord.ui.View):
    """Confirmación por botones para acciones destructivas.

    Los `custom_id` se generan por instancia, así que nunca colisionan entre
    dos confirmaciones simultáneas.
    """

    def __init__(
        self,
        *,
        user_id: int,
        on_confirm: ConfirmCallback,
        on_cancel: Optional[ConfirmCallback] = None,
        confirm_label: str = "Confirmar",
        cancel_label: str = "Cancelar",
        timeout: float = 60.0,
    ) -> None:
        super().__init__(timeout=timeout)
        token = uuid.uuid4().hex[:10]
        self.user_id = user_id
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.message: Optional[discord.Message] = None

        self.ok_button = discord.ui.Button(
            label=confirm_label,
            style=discord.ButtonStyle.danger,
            emoji="✅",
            custom_id=f"korian:confirm:{token}",
        )
        self.ok_button.callback = self._confirm
        self.cancel_button = discord.ui.Button(
            label=cancel_label,
            style=discord.ButtonStyle.secondary,
            emoji="✖️",
            custom_id=f"korian:cancel:{token}",
        )
        self.cancel_button.callback = self._cancel
        self.add_item(self.ok_button)
        self.add_item(self.cancel_button)

    async def _authorised(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Solo quien inició esta acción puede confirmarla.", ephemeral=True
            )
            return False
        return True

    async def _confirm(self, interaction: discord.Interaction) -> None:
        if not await self._authorised(interaction):
            return
        self.stop()
        if self.on_confirm is not None:
            await self.on_confirm(interaction)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        if not await self._authorised(interaction):
            return
        self.stop()
        if self.on_cancel is not None:
            await self.on_cancel(interaction)
            return
        embed = discord.Embed(
            description="✖️ Acción cancelada.", color=discord.Color.dark_grey()
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        await safe_edit(self.message, view=self)


class Paginator(discord.ui.View):
    """Paginador con botones «◀ ▶» para listas largas."""

    def __init__(self, embeds: Sequence[discord.Embed], *, user_id: int, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.embeds = list(embeds)
        self.index = 0
        self.user_id = user_id
        self.message: Optional[discord.Message] = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        only_one = len(self.embeds) <= 1
        self.first_page.disabled = self.prev_page.disabled = only_one or self.index == 0
        self.last_page.disabled = self.next_page.disabled = (
            only_one or self.index >= len(self.embeds) - 1
        )

    async def _interact(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Usa tus propios botones para navegar.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(emoji="⏮", style=discord.ButtonStyle.secondary, custom_id="korian:page:first")
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._interact(interaction):
            return
        self.index = 0
        await self._render(interaction)

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.primary, custom_id="korian:page:prev")
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._interact(interaction):
            return
        self.index = max(0, self.index - 1)
        await self._render(interaction)

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.primary, custom_id="korian:page:next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._interact(interaction):
            return
        self.index = min(len(self.embeds) - 1, self.index + 1)
        await self._render(interaction)

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.secondary, custom_id="korian:page:last")
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._interact(interaction):
            return
        self.index = len(self.embeds) - 1
        await self._render(interaction)

    async def _render(self, interaction: discord.Interaction) -> None:
        self._update_buttons()
        if interaction.response.is_done():
            await interaction.followup.edit_message(
                message_id=interaction.message.id, embed=self.embeds[self.index], view=self
            )
        else:
            await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

    async def start(self, destination: discord.abc.Messageable) -> discord.Message:
        self.message = await destination.send(embed=self.embeds[self.index], view=self)
        return self.message

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        await safe_edit(self.message, view=self)
