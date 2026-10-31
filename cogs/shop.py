"""Tienda con puntos: productos, compras, entregas y pagos externos.

Los pagos con Stripe/PayPal están preparados pero desactivados hasta que se
configuren las claves en el archivo `.env`.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

import aiohttp
import discord
from discord.ext import commands

from core.config import settings
from core.database import db
from core.logging_cfg import get_logger
from utils.checks import admin_only, staff_only
from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.helpers import ConfirmView, Paginator
from utils.validators import parse_int, require_text

log = get_logger("tienda")

PAYMENT_METHODS = ("puntos", "stripe", "paypal")


# ---------------------------------------------------------------------------
# Pagos externos (arquitectura lista, desactivados sin claves)
# ---------------------------------------------------------------------------
def payment_status() -> str:
    if settings.payments_provider == "stripe" and settings.stripe_secret_key:
        return "Stripe: ✅ configurado"
    if settings.payments_provider == "paypal" and settings.paypal_client_id:
        return "PayPal: ✅ configurado"
    return "Pagos externos: ❌ desactivados (sin PAYMENTS_PROVIDER / claves en .env)"


async def _stripe_checkout(
    product: dict[str, Any], guild: discord.Guild, user: discord.User
) -> Optional[str]:
    if not (settings.payments_provider == "stripe" and settings.stripe_secret_key):
        return None
    price_cents = int(product["price"]) * 100
    data = {
        "mode": "payment",
        "success_url": f"https://discord.com/channels/{guild.id}",
        "cancel_url": f"https://discord.com/channels/{guild.id}",
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": (await db.guild_config(guild.id)).get("payments", {}).get("currency", "eur"),
        "line_items[0][price_data][unit_amount]": str(price_cents),
        "line_items[0][price_data][product_data][name]": str(product["name"])[:120],
        "client_reference_id": f"{guild.id}:{user.id}:{product['id']}",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://api.stripe.com/v1/checkout/sessions",
                data=data,
                auth=aiohttp.BasicAuth(settings.stripe_secret_key, ""),
            ) as response:
                payload = await response.json()
    except (aiohttp.ClientError, ValueError) as exc:
        log.warning("Fallo de Stripe: %s", exc)
        return None
    if response.status != 200:
        log.warning("Stripe devolvió %s: %s", response.status, payload.get("error", {}).get("message"))
        return None
    return payload.get("url")


async def _paypal_order(product: dict[str, Any], guild: discord.Guild, user: discord.User) -> Optional[str]:
    if not (settings.payments_provider == "paypal" and settings.paypal_client_id):
        return None
    auth_url = "https://api-m.sandbox.paypal.com/v1/oauth2/token"
    order_url = "https://api-m.sandbox.paypal.com/v2/checkout/orders"
    try:
        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                auth_url,
                data={"grant_type": "client_credentials"},
                auth=aiohttp.BasicAuth(settings.paypal_client_id, settings.paypal_secret),
            ) as response:
                token_data = await response.json()
            token = token_data.get("access_token")
            if not token:
                log.warning("PayPal no devolvió token: %s", token_data.get("error"))
                return None
            async with session.post(
                order_url,
                json={
                    "intent": "CAPTURE",
                    "purchase_units": [
                        {
                            "reference_id": f"{guild.id}-{user.id}-{product['id']}",
                            "description": str(product["name"])[:120],
                            "amount": {
                                "currency_code": "EUR",
                                "value": f"{float(product['price']):.2f}",
                            },
                        }
                    ],
                },
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                order = await response.json()
    except (aiohttp.ClientError, ValueError) as exc:
        log.warning("Fallo de PayPal: %s", exc)
        return None
    if response.status != 201:
        log.warning("PayPal devolvió %s", response.status)
        return None
    for link in order.get("links", []):
        if link.get("rel") == "approve":
            return link.get("href")
    return None


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class Shop(commands.Cog):
    """Tienda de puntos, productos y compras."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _find_product(self, guild_id: Any, name: str) -> Optional[dict[str, Any]]:
        row = await db.fetchone(
            "SELECT * FROM products WHERE guild_id = ? AND LOWER(name) = LOWER(?)",
            (str(guild_id), name.strip()),
        )
        if row:
            return row
        return await db.fetchone(
            "SELECT * FROM products WHERE guild_id = ? AND LOWER(name) LIKE LOWER(?) AND enabled = 1",
            (str(guild_id), f"%{name.strip()}%"),
        )

    def _product_embed(self, product: dict[str, Any], index: int) -> discord.Embed:
        embed = base_embed(
            f"🛒 {product['name']}",
            (product.get("description") or "Sin descripción.")[:500],
        )
        embed.add_field(name="Precio", value=f"🪙 {product['price']} puntos", inline=True)
        stock = int(product.get("stock", -1))
        embed.add_field(name="Stock", value="Ilimitado" if stock < 0 else str(stock), inline=True)
        embed.add_field(name="ID", value=f"`{product['id']}`", inline=True)
        if product.get("role_id"):
            embed.add_field(name="Entrega", value=f"Rol <@&{product['role_id']}> automática", inline=False)
        elif product.get("delivery"):
            embed.add_field(name="Entrega", value=str(product["delivery"])[:400], inline=False)
        return embed

    # ------------------------------------------------------------------
    # Comandos públicos
    # ------------------------------------------------------------------
    @commands.hybrid_command(name="tienda", description="Lista los productos disponibles y tus compras.")
    @commands.guild_only()
    async def tienda(self, ctx: commands.Context) -> None:
        products = await db.fetchall(
            "SELECT * FROM products WHERE guild_id = ? AND enabled = 1 ORDER BY price ASC",
            (str(ctx.guild.id),),
        )
        if not products:
            await ctx.send(
                embed=base_embed(
                    "🛒 Tienda",
                    "Todavía no hay productos. El staff puede añadirlos con `/product create`.",
                )
            )
            return

        embeds = [self._product_embed(product, index) for index, product in enumerate(products, start=1)]
        for page, embed in enumerate(embeds, start=1):
            embed.set_footer(text=f"Página {page}/{len(embeds)} · Compra con /buy <producto>")

        history = await db.fetchall(
            "SELECT id, product_name, price, status, created_at FROM purchases "
            "WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT 5",
            (str(ctx.guild.id), str(ctx.author.id)),
        )
        if history:
            lines = [
                f"**#{row['id']}** {row['product_name']} — {row['price']} pts · {row['status']}"
                for row in history
            ]
            embeds[0].add_field(name="Tus últimas compras", value="\n".join(lines)[:1000], inline=False)

        await Paginator(embeds, user_id=ctx.author.id).start(ctx)

    @commands.hybrid_command(name="buy", description="Compra un producto con puntos o con pago externo.")
    @commands.guild_only()
    async def buy(
        self,
        ctx: commands.Context,
        producto: str,
        metodo: Optional[str] = None,
    ) -> None:
        product = await self._find_product(ctx.guild.id, producto)
        if product is None or not int(product.get("enabled", 1)):
            await ctx.send(
                embed=error_embed("Producto no encontrado", f"No existe **{producto}** en la tienda.")
            )
            return
        if int(product.get("stock", -1)) == 0:
            await ctx.send(embed=error_embed("Sin stock", f"**{product['name']}** está agotado."))
            return

        method = (metodo or "puntos").strip().lower()
        if method not in PAYMENT_METHODS:
            await ctx.send(
                embed=warn_embed(
                    "Método no válido",
                    f"Métodos disponibles: {', '.join(PAYMENT_METHODS)}.",
                )
            )
            return

        if method == "stripe":
            url = await _stripe_checkout(product, ctx.guild, ctx.author)
            if url is None:
                await ctx.send(embed=warn_embed("Pagos desactivados", payment_status()))
                return
            await ctx.send(embed=success_embed("Pago con Stripe", f"Completa el pago aquí:\n{url}"))
            return
        if method == "paypal":
            url = await _paypal_order(product, ctx.guild, ctx.author)
            if url is None:
                await ctx.send(embed=warn_embed("Pagos desactivados", payment_status()))
                return
            await ctx.send(embed=success_embed("Pago con PayPal", f"Completa el pago aquí:\n{url}"))
            return

        balance_row = await db.ensure_user(ctx.guild.id, ctx.author.id)
        balance = int(balance_row.get("points", 0))
        price = int(product["price"])
        if balance < price:
            await ctx.send(
                embed=error_embed(
                    "Puntos insuficientes",
                    f"**{product['name']}** cuesta **{price}** puntos y tienes **{balance}**.",
                )
            )
            return

        embed = warn_embed(
            "Confirmar compra",
            f"**{product['name']}** por **{price}** puntos.\n"
            f"Tu saldo: {balance} → {balance - price}\n"
            "Esta acción no se puede deshacer.",
        )

        async def _confirm(interaction: discord.Interaction) -> None:
            await self._complete_purchase(interaction, product, price)

        view = ConfirmView(
            user_id=ctx.author.id,
            on_confirm=_confirm,
            confirm_label=f"Comprar por {price}",
            cancel_label="Cancelar",
        )
        message = await ctx.send(embed=embed, view=view)
        view.message = message

    async def _complete_purchase(
        self, interaction: discord.Interaction, product: dict[str, Any], price: int
    ) -> None:
        balance_row = await db.ensure_user(interaction.guild_id, interaction.user.id)
        if int(balance_row.get("points", 0)) < price:
            await interaction.response.edit_message(
                embed=error_embed("Puntos insuficientes", "Tu saldo cambió antes de completar la compra."),
                view=None,
            )
            return

        await db.add_points(
            interaction.guild_id, interaction.user.id, -price, f"compra: {product['name']}", interaction.user.id
        )
        code = uuid.uuid4().hex[:8].upper()
        cursor = await db.execute(
            "INSERT INTO purchases (guild_id, user_id, product_id, product_name, price, status, code) "
            "VALUES (?, ?, ?, ?, ?, 'pendiente', ?)",
            (
                str(interaction.guild_id),
                str(interaction.user.id),
                int(product["id"]),
                str(product["name"]),
                price,
                code,
            ),
        )
        purchase_id = cursor.lastrowid
        if int(product.get("stock", -1)) >= 0:
            await db.execute(
                "UPDATE products SET stock = stock - 1 WHERE id = ?", (int(product["id"]),)
            )

        assigned = False
        if product.get("role_id") and interaction.guild is not None:
            role = interaction.guild.get_role(int(product["role_id"]))
            if role is not None and interaction.guild.me and interaction.guild.me.top_role > role:
                try:
                    await interaction.user.add_roles(role, reason=f"Compra #{purchase_id}")
                    assigned = True
                except discord.HTTPException:
                    log.warning("No se pudo asignar el rol de %s", product["name"])

        await db.log_event(
            interaction.guild_id,
            "compra",
            user_id=interaction.user.id,
            content=f"#{purchase_id} {product['name']} por {price} pts ({code})",
        )

        lines = [
            f"**Compra registrada** — ID `#{purchase_id}` · código `{code}`",
            f"**Producto:** {product['name']}",
            f"**Precio:** {price} puntos",
        ]
        if product.get("delivery"):
            lines.append(f"**Entrega:** {product['delivery']}")
        if assigned:
            lines.append("**Rol asignado automáticamente.**")
        else:
            lines.append("El staff la entregará manualmente con `/product entregar`.")
        await interaction.response.edit_message(
            embed=success_embed("Compra realizada", "\n".join(lines)), view=None
        )

    # ------------------------------------------------------------------
    # Gestión de productos (admin)
    # ------------------------------------------------------------------
    @commands.hybrid_group(name="product", description="Gestión de productos de la tienda.", invoke_without_command=True)
    @admin_only()
    async def product(self, ctx: commands.Context) -> None:
        await self.product_list(ctx)

    @product.command(name="create", description="Crea un producto en la tienda.")
    @admin_only()
    async def product_create(
        self,
        ctx: commands.Context,
        nombre: str,
        precio: int,
        descripcion: Optional[str] = None,
        stock: Optional[int] = None,
        rol: Optional[discord.Role] = None,
        entrega: Optional[str] = None,
    ) -> None:
        name = require_text(nombre, "El nombre del producto", 2, 80)
        price = parse_int(precio, "El precio", minimum=0, maximum=10_000_000)
        if await self._find_product(ctx.guild.id, name):
            await ctx.send(embed=error_embed("Producto duplicado", f"Ya existe **{name}**."))
            return
        cursor = await db.execute(
            "INSERT INTO products (guild_id, name, description, price, stock, role_id, delivery) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(ctx.guild.id),
                name,
                (descripcion or "").strip()[:900] or None,
                price,
                int(stock) if stock is not None else -1,
                str(rol.id) if rol else None,
                (entrega or "").strip()[:400] or None,
            ),
        )
        await ctx.send(
            embed=success_embed(
                "Producto creado",
                f"**{name}** — {price} puntos (ID `{cursor.lastrowid}`).",
            )
        )

    @product.command(name="list", description="Lista todos los productos.")
    @admin_only()
    async def product_list(self, ctx: commands.Context) -> None:
        products = await db.fetchall(
            "SELECT * FROM products WHERE guild_id = ? ORDER BY id ASC", (str(ctx.guild.id),)
        )
        if not products:
            await ctx.send(embed=base_embed("📦 Productos", "No hay productos todavía."))
            return
        lines = [
            f"`#{row['id']}` **{row['name']}** — {row['price']} pts · stock "
            f"{'∞' if int(row['stock']) < 0 else row['stock']} · {'activo' if int(row['enabled']) else 'inactivo'}"
            for row in products
        ]
        embeds = [
            base_embed("📦 Productos", "\n".join(lines[start : start + 15]))
            for start in range(0, len(lines), 15)
        ]
        for page, embed in enumerate(embeds, start=1):
            embed.set_footer(text=f"Página {page}/{len(embeds)}")
        await Paginator(embeds, user_id=ctx.author.id).start(ctx)

    @product.command(name="set-price", description="Cambia el precio de un producto.")
    @admin_only()
    async def product_set_price(self, ctx: commands.Context, producto: str, precio: int) -> None:
        product = await self._find_product(ctx.guild.id, producto)
        if product is None:
            await ctx.send(embed=error_embed("Producto no encontrado", f"No existe **{producto}**."))
            return
        price = parse_int(precio, "El precio", minimum=0, maximum=10_000_000)
        await db.execute("UPDATE products SET price = ? WHERE id = ?", (price, int(product["id"])))
        await ctx.send(
            embed=success_embed("Precio actualizado", f"**{product['name']}** ahora cuesta **{price}** puntos.")
        )

    @product.command(name="edit", description="Edita la descripción, stock o entrega de un producto.")
    @admin_only()
    async def product_edit(
        self,
        ctx: commands.Context,
        producto: str,
        descripcion: Optional[str] = None,
        stock: Optional[int] = None,
        entrega: Optional[str] = None,
        activo: Optional[bool] = None,
    ) -> None:
        product = await self._find_product(ctx.guild.id, producto)
        if product is None:
            await ctx.send(embed=error_embed("Producto no encontrado", f"No existe **{producto}**."))
            return
        updates: list[str] = []
        params: list[Any] = []
        if descripcion is not None:
            updates.append("description = ?")
            params.append(descripcion.strip()[:900])
        if stock is not None:
            updates.append("stock = ?")
            params.append(int(stock))
        if entrega is not None:
            updates.append("delivery = ?")
            params.append(entrega.strip()[:400])
        if activo is not None:
            updates.append("enabled = ?")
            params.append(1 if activo else 0)
        if not updates:
            await ctx.send(embed=warn_embed("Nada que cambiar", "Indica al menos un campo a modificar."))
            return
        params.append(int(product["id"]))
        await db.execute(f"UPDATE products SET {', '.join(updates)} WHERE id = ?", params)
        await ctx.send(
            embed=success_embed("Producto actualizado", f"**{product['name']}** se guardó correctamente.")
        )

    @product.command(name="delete", description="Elimina un producto de la tienda.")
    @admin_only()
    async def product_delete(self, ctx: commands.Context, producto: str) -> None:
        product = await self._find_product(ctx.guild.id, producto)
        if product is None:
            await ctx.send(embed=error_embed("Producto no encontrado", f"No existe **{producto}**."))
            return

        async def _confirm(interaction: discord.Interaction) -> None:
            await db.execute("DELETE FROM products WHERE id = ?", (int(product["id"]),))
            await interaction.response.edit_message(
                embed=success_embed("Producto eliminado", f"**{product['name']}** ya no está en la tienda."),
                view=None,
            )

        view = ConfirmView(user_id=ctx.author.id, on_confirm=_confirm, confirm_label="Eliminar")
        message = await ctx.send(
            embed=warn_embed(
                "¿Eliminar producto?",
                f"Vas a eliminar **{product['name']}**. Esta acción no se puede deshacer.",
            ),
            view=view,
        )
        view.message = message

    @product.command(name="entregar", description="[Staff] Marca una compra como entregada.")
    @staff_only()
    async def product_deliver(self, ctx: commands.Context, id_compra: int) -> None:
        purchase = await db.fetchone(
            "SELECT * FROM purchases WHERE guild_id = ? AND id = ?",
            (str(ctx.guild.id), int(id_compra)),
        )
        if purchase is None:
            await ctx.send(embed=error_embed("Compra no encontrada", f"No existe la compra `#{id_compra}`."))
            return
        if str(purchase["status"]) == "entregado":
            await ctx.send(embed=warn_embed("Ya entregada", "Esa compra ya fue entregada."))
            return
        await db.execute(
            "UPDATE purchases SET status = 'entregado', delivered_by = ? WHERE id = ?",
            (str(ctx.author.id), int(id_compra)),
        )
        member = ctx.guild.get_member(int(purchase["user_id"]))
        if member is not None:
            try:
                await member.send(
                    f"✅ Tu compra **#{id_compra}** ({purchase['product_name']}) fue entregada por el staff."
                )
            except (discord.Forbidden, discord.HTTPException):
                log.info("No se pudo avisar por DM de la compra #%s", id_compra)
        await ctx.send(
            embed=success_embed(
                "Compra entregada",
                f"`#{id_compra}` **{purchase['product_name']}** → <@{purchase['user_id']}>",
            )
        )

    @commands.hybrid_command(name="pagos", description="[Admin] Estado de los pagos externos.")
    @admin_only()
    async def pagos(self, ctx: commands.Context) -> None:
        cfg = await db.guild_config(ctx.guild.id)
        provider = (cfg.get("payments") or {}).get("provider", "none")
        embed = base_embed(
            "💳 Pagos",
            f"**Estado:** {payment_status()}\n"
            f"**Proveedor configurado en el servidor:** `{provider}`\n"
            f"**Moneda:** `{(cfg.get('payments') or {}).get('currency', 'EUR')}`\n\n"
            "Para activarlos, define `PAYMENTS_PROVIDER`, `STRIPE_SECRET_KEY` o "
            "`PAYPAL_CLIENT_ID`/`PAYPAL_SECRET` en el archivo `.env` y reinicia el bot.",
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Shop(bot))
