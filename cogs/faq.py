"""Preguntas frecuentes (FAQ) compartidas con la base de conocimiento de la IA."""

from __future__ import annotations

from typing import Optional

from discord.ext import commands

from core.logging_cfg import get_logger
from services import knowledge
from utils.checks import admin_only
from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.helpers import Paginator
from utils.validators import clean_text, require_text

log = get_logger("faq")


class FAQ(commands.Cog):
    """Comandos de preguntas frecuentes: alimentan también a KorianMC IA."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_group(name="faq", description="Preguntas frecuentes del servidor.", invoke_without_command=True)
    @commands.guild_only()
    async def faq(self, ctx: commands.Context, *, pregunta: Optional[str] = None) -> None:
        if pregunta:
            await self._search(ctx, pregunta)
            return
        await self._list(ctx)

    @faq.command(name="add", description="[Admin] Añade una pregunta frecuente.")
    @admin_only()
    async def faq_add(
        self,
        ctx: commands.Context,
        pregunta: str,
        respuesta: str,
        palabras_clave: Optional[str] = None,
    ) -> None:
        question = require_text(pregunta, "La pregunta", 3, 200)
        answer = require_text(respuesta, "La respuesta", 5, 900)
        keywords = clean_text(palabras_clave, 200)
        entry_id = await knowledge.add_entry(
            ctx.guild.id, question, answer, keywords, source="faq"
        )
        await ctx.send(
            embed=success_embed(
                "FAQ añadida",
                f"Entrada `#{entry_id}` registrada y disponible para la IA.\n"
                f"**P:** {question}",
            )
        )

    @faq.command(name="remove", description="[Admin] Elimina una pregunta frecuente.")
    @admin_only()
    async def faq_remove(self, ctx: commands.Context, id_entrada: int) -> None:
        removed = await knowledge.remove_entry(ctx.guild.id, int(id_entrada))
        if not removed:
            await ctx.send(
                embed=error_embed("Entrada no encontrada", f"No existe la entrada `#{id_entrada}`.")
            )
            return
        await ctx.send(
            embed=success_embed("FAQ eliminada", f"Se borró la entrada `#{id_entrada}`.")
        )

    @faq.command(name="list", description="Lista las preguntas frecuentes.")
    @commands.guild_only()
    async def faq_list(self, ctx: commands.Context) -> None:
        await self._list(ctx)

    @faq.command(name="buscar", description="Busca una respuesta en la FAQ.")
    @commands.guild_only()
    async def faq_search(self, ctx: commands.Context, pregunta: str) -> None:
        await self._search(ctx, pregunta)

    # ------------------------------------------------------------------
    async def _list(self, ctx: commands.Context) -> None:
        rows = await knowledge.entries(ctx.guild.id, source="faq")
        if not rows:
            await ctx.send(
                embed=base_embed(
                    "❓ FAQ",
                    "Todavía no hay preguntas frecuentes. El staff puede añadirlas con `/faq add`.",
                )
            )
            return
        embeds = []
        for start in range(0, len(rows), 4):
            chunk = rows[start : start + 4]
            lines = [
                f"**`#{row['id']}` {clean_text(str(row['question']), 120)}**\n{clean_text(str(row['answer']), 300)}"
                for row in chunk
            ]
            embed = base_embed("❓ Preguntas frecuentes", "\n\n".join(lines))
            embeds.append(embed)
        for page, embed in enumerate(embeds, start=1):
            embed.set_footer(text=f"Página {page}/{len(embeds)}")
        await Paginator(embeds, user_id=ctx.author.id).start(ctx)

    async def _search(self, ctx: commands.Context, question: str) -> None:
        row = await knowledge.search(ctx.guild.id, question, source="faq")
        if row is None:
            await ctx.send(
                embed=warn_embed(
                    "Sin respuesta",
                    "No tengo esa duda registrada. Prueba con otra palabra o abre un ticket con `/ticket create`.",
                )
            )
            return
        embed = base_embed(
            f"❓ {clean_text(str(row['question']), 200)}",
            clean_text(str(row["answer"]), 900),
        )
        embed.set_footer(text=f"Entrada #{row['id']}")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FAQ(bot))
