"""
Formatador profissional de mensagens do Telegram.

Cria mensagens visualmente atraentes e informativas para cada
promoção, com suporte a HTML, emojis, preços formatados,
detecção automática de cupons e exibição da loja de origem real
quando a oferta vem de agregadores (Pelando, Promobit).
"""

from __future__ import annotations

import re
from html import escape
from typing import Optional

from promo_bot.database.models import Product, Store
from promo_bot.utils.logger import get_logger

logger = get_logger("formatter")


def _format_brl(value: float) -> str:
    """Formata valor em Reais no padrão brasileiro (R$ 1.499,90)."""
    formatted = f"{value:,.2f}"
    formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


# Emojis por loja
STORE_EMOJIS = {
    Store.SHOPEE: "🟠",
    Store.ALIEXPRESS: "🔴",
    Store.AMAZON: "📦",
    Store.KABUM: "🟢",
    Store.PELANDO: "🔥",
    Store.PROMOBIT: "💎",
    Store.TERABYTE: "💻",
    Store.MERCADOLIVRE: "🛒",
    Store.UNKNOWN: "🏷️",
}

STORE_NAMES = {
    Store.SHOPEE: "Shopee",
    Store.ALIEXPRESS: "AliExpress",
    Store.AMAZON: "Amazon",
    Store.KABUM: "KaBuM!",
    Store.PELANDO: "Pelando",
    Store.PROMOBIT: "Promobit",
    Store.TERABYTE: "Terabyte Shop",
    Store.MERCADOLIVRE: "Mercado Livre",
    Store.UNKNOWN: "Loja",
}


class MessageFormatter:
    """
    Formatador de mensagens para o Telegram.

    Cria mensagens em HTML com formatação profissional,
    incluindo título, preço, desconto, cupom, link e
    loja de origem real quando disponível.
    """

    def format_product(self, product: Product) -> str:
        """
        Formata um produto em mensagem HTML para o Telegram.

        Args:
            product: Produto a ser formatado.

        Returns:
            String HTML formatada para envio via Telegram.
        """
        # Detecta se tem cupom
        coupon = product.coupon_code or self._detect_coupon(product.title)

        if coupon:
            return self._format_coupon_deal(product, coupon)
        elif product.has_discount:
            return self._format_discount_deal(product)
        elif product.price is not None:
            return self._format_price_deal(product)
        else:
            return self._format_simple_deal(product)

    def _get_store_display(self, product: Product) -> tuple[str, str]:
        """
        Retorna emoji e nome da loja para exibição.

        Para ofertas de agregadores (Pelando, Promobit), mostra a
        loja de origem real quando disponível.

        Returns:
            Tupla (emoji, nome_da_loja).
        """
        store_emoji = STORE_EMOJIS.get(product.store, "🏷️")
        store_name = STORE_NAMES.get(product.store, "Loja")

        # Para Pelando e Promobit, mostra a loja de origem real
        origin_store = product.extra.get("origin_store", "")
        if origin_store and product.store in (Store.PELANDO, Store.PROMOBIT):
            store_name = origin_store
            # Tenta mapear emoji da loja de origem
            origin_lower = origin_store.lower()
            if "amazon" in origin_lower:
                store_emoji = "📦"
            elif "shopee" in origin_lower:
                store_emoji = "🟠"
            elif "aliexpress" in origin_lower or "ali express" in origin_lower:
                store_emoji = "🔴"
            elif "kabum" in origin_lower:
                store_emoji = "🟢"
            elif "mercado livre" in origin_lower or "mercadolivre" in origin_lower:
                store_emoji = "🛒"
            elif "magalu" in origin_lower or "magazine luiza" in origin_lower:
                store_emoji = "🔵"
            elif "casas bahia" in origin_lower:
                store_emoji = "🏠"
            elif "carrefour" in origin_lower:
                store_emoji = "🛒"
            elif "samsung" in origin_lower:
                store_emoji = "📱"
            elif "nike" in origin_lower:
                store_emoji = "👟"
            else:
                store_emoji = "🏪"

        return store_emoji, store_name

    def _format_discount_deal(self, product: Product) -> str:
        """Formata uma oferta com desconto visível."""
        store_emoji, store_name = self._get_store_display(product)
        title = escape(self._truncate(product.title, 200))

        lines = [
            f"🚨 <b>OFERTA IMPERDÍVEL</b> 🚨",
            "",
            f"📦 <b>{title}</b>",
            "",
        ]

        # Preço original riscado
        if product.original_price:
            lines.append(f"❌ De: <s>{_format_brl(product.original_price)}</s>")

        # Preço atual em destaque
        if product.price:
            lines.append(f"✅ Por: <b>{_format_brl(product.price)}</b>")

        # Desconto
        discount = product.calculated_discount
        if discount:
            lines.append(f"📉 Desconto: <b>{discount:.0f}% OFF</b>")

        # Economia
        savings = product.savings
        if savings:
            lines.append(f"💰 Economia: <b>{_format_brl(savings)}</b>")

        lines.append("")

        # Frete grátis
        if product.free_shipping:
            lines.append("🚚 <b>Frete Grátis</b>")

        # Cupom (se houver)
        if product.coupon_code:
            lines.append(f"✂️ Cupom: <code>{escape(product.coupon_code)}</code>")

        # Loja
        lines.append(f"{store_emoji} Loja: <b>{escape(store_name)}</b>")
        lines.append("")
        lines.append("🔗 <b>Acesse a oferta abaixo:</b>")

        return "\n".join(lines)

    def _format_price_deal(self, product: Product) -> str:
        """Formata uma oferta com preço (sem desconto calculável)."""
        store_emoji, store_name = self._get_store_display(product)
        title = escape(self._truncate(product.title, 200))

        lines = [
            f"🔥 <b>OFERTA ENCONTRADA</b> 🔥",
            "",
            f"📦 <b>{title}</b>",
            "",
            f"💰 Valor: <b>{_format_brl(product.price)}</b>",
        ]

        if product.discount_pct:
            lines.append(f"📉 Desconto: <b>{product.discount_pct:.0f}% OFF</b>")

        if product.free_shipping:
            lines.append("🚚 <b>Frete Grátis</b>")

        # Cupom (se houver)
        if product.coupon_code:
            lines.append(f"✂️ Cupom: <code>{escape(product.coupon_code)}</code>")

        lines.append("")
        lines.append(f"{store_emoji} Loja: <b>{escape(store_name)}</b>")
        lines.append("")
        lines.append("🔗 <b>Confira no link abaixo:</b>")

        return "\n".join(lines)

    def _format_coupon_deal(self, product: Product, coupon: str) -> str:
        """Formata uma oferta com cupom de desconto."""
        store_emoji, store_name = self._get_store_display(product)
        title = escape(self._truncate(product.title, 200))

        lines = [
            f"🎟️ <b>CUPOM DE DESCONTO</b> 🎟️",
            "",
            f"📦 <b>{title}</b>",
            "",
        ]

        if product.price:
            lines.append(f"💰 Valor: <b>{_format_brl(product.price)}</b>")

        if product.original_price and product.price:
            lines.append(f"❌ De: <s>{_format_brl(product.original_price)}</s>")

        lines.extend([
            "",
            f"✂️ Cupom: <code>{escape(coupon)}</code>",
            "<i>(Toque no código para copiar)</i>",
            "",
        ])

        if product.free_shipping:
            lines.append("🚚 <b>Frete Grátis</b>")

        lines.append(f"{store_emoji} Loja: <b>{escape(store_name)}</b>")
        lines.append("")
        lines.append("🔗 <b>Resgate o cupom abaixo:</b>")

        return "\n".join(lines)

    def _format_simple_deal(self, product: Product) -> str:
        """Formata uma oferta simples (sem preço definido)."""
        store_emoji, store_name = self._get_store_display(product)
        title = escape(self._truncate(product.title, 200))

        lines = [
            f"⚡ <b>PROMOÇÃO ATIVA</b> ⚡",
            "",
            f"🎯 <b>{title}</b>",
            "",
        ]

        if product.discount_pct:
            lines.append(f"📉 Desconto: <b>{product.discount_pct:.0f}% OFF</b>")

        if product.free_shipping:
            lines.append("🚚 <b>Frete Grátis</b>")

        # Cupom (se houver)
        if product.coupon_code:
            lines.append(f"✂️ Cupom: <code>{escape(product.coupon_code)}</code>")

        lines.append(f"{store_emoji} Loja: <b>{escape(store_name)}</b>")
        lines.append("")
        lines.append("🔗 <b>Confira no link abaixo:</b>")

        return "\n".join(lines)

    def _detect_coupon(self, title: str) -> Optional[str]:
        """Detecta cupom de desconto no título."""
        upper = title.upper()
        if "CUPOM" not in upper and "CÓDIGO" not in upper and "CODIGO" not in upper:
            return None

        # Padrão: CUPOM: XPTO123
        match = re.search(
            r'(?:CUPOM|CÓDIGO|CODIGO|USE O|CÓD|COD)[\s:]*([A-Z0-9]{4,25})',
            upper,
        )
        if match:
            return match.group(1)

        # Padrão secundário: código alfanumérico isolado
        match = re.search(r'\b([A-Z]+[0-9]+[A-Z0-9]*)\b', upper)
        if match and len(match.group(1)) >= 4:
            return match.group(1)

        return "APLICADO NO CARRINHO"

    def _truncate(self, text: str, max_length: int) -> str:
        """Trunca texto mantendo palavras inteiras."""
        if len(text) <= max_length:
            return text
        truncated = text[:max_length].rsplit(" ", 1)[0]
        return truncated + "..."

    def format_stats_message(self, stats: dict) -> str:
        """Formata mensagem de estatísticas para o admin."""
        lines = [
            "📊 <b>Painel do PromoBot</b>",
            "",
            f"🔄 Ciclos executados: <b>{stats.get('cycles', 0)}</b>",
            f"📥 Total coletado: <b>{stats.get('total_collected', 0)}</b>",
            f"📤 Total enviado: <b>{stats.get('total_sent', 0)}</b>",
            f"🤖 Scrapers ativos: <b>{stats.get('scrapers_active', 0)}</b>",
            "",
        ]

        # Cache stats
        cache = stats.get("cache_stats", {})
        if cache:
            lines.extend([
                "<b>Cache:</b>",
                f"  Tamanho: {cache.get('size', 0)}/{cache.get('max_size', 0)}",
                f"  Hit rate: {cache.get('hit_rate', '0%')}",
                "",
            ])

        # Filter stats
        filt = stats.get("filter_stats", {})
        if filt:
            lines.extend([
                "<b>Filtros:</b>",
                f"  Aprovados: {filt.get('passed', 0)}",
                f"  Rejeitados: {filt.get('filtered', 0)}",
                f"  Taxa: {filt.get('pass_rate', '0%')}",
                "",
            ])

        # Dedup stats
        dedup = stats.get("dedup_stats", {})
        if dedup:
            lines.extend([
                "<b>Deduplicação:</b>",
                f"  Únicos: {dedup.get('unique', 0)}",
                f"  Duplicatas: {dedup.get('duplicates', 0)}",
                "",
            ])

        # HTTP stats
        http = stats.get("http_stats", {})
        if http:
            lines.extend([
                "<b>HTTP:</b>",
                f"  Requisições: {http.get('total_requests', 0)}",
                f"  Erros: {http.get('total_errors', 0)}",
                f"  Taxa de erro: {http.get('error_rate', '0%')}",
            ])

        # Telegram stats
        telegram = stats.get("telegram", {})
        if telegram:
            lines.extend([
                "",
                "<b>Telegram:</b>",
                f"  Mensagens: {telegram.get('messages_sent', 0)}",
                f"  Fotos: {telegram.get('photos_sent', 0)}",
                f"  Erros: {telegram.get('errors', 0)}",
            ])

        return "\n".join(lines)
