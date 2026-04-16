"""
Serviço de formatação de mensagens profissional.

Gera mensagens ricas em Markdown para o Telegram, com suporte a
cupons, preços, descontos e links de afiliados.

CORREÇÕES v2.3:
- Estilo visual profissional (inspirado em Santana Tech)
- Bloco de cupom destacado com instrução de cópia
- Formatação de preços e economia real
- Emojis por categoria e loja
"""

from __future__ import annotations

import re
from typing import Optional

from promo_bot.database.models import Product, Store

# Emojis por loja
STORE_EMOJIS = {
    Store.AMAZON: "??",
    Store.KABUM: "??",
    Store.ALIEXPRESS: "??",
    Store.SHOPEE: "??",
    Store.PELANDO: "??",
    Store.PROMOBIT: "??",
    Store.TERABYTE: "??",
    Store.MERCADOLIVRE: "??",
}

# Nomes amigáveis por loja
STORE_NAMES = {
    Store.AMAZON: "Amazon",
    Store.KABUM: "KaBuM!",
    Store.ALIEXPRESS: "AliExpress",
    Store.SHOPEE: "Shopee",
    Store.PELANDO: "Pelando",
    Store.PROMOBIT: "Promobit",
    Store.TERABYTE: "Terabyte Shop",
    Store.MERCADOLIVRE: "Mercado Livre",
}

class PromoFormatter:
    """Formatador de mensagens profissional."""

    @staticmethod
    def format_promo(product: Product) -> str:
        """Gera a mensagem formatada para uma promoção."""
        store_emoji = STORE_EMOJIS.get(product.store, "??")
        store_name = STORE_NAMES.get(product.store, product.store.value.title())
        
        # 1. Cabeçalho e Título
        lines = [
            f"?? **PROMOÇÃO ATIVA** ??",
            "",
            f"?? **{product.title}**",
            "",
            f"{store_emoji} **Loja:** {store_name}",
        ]

        # 2. Preços e Desconto
        if product.price:
            price_str = f"R$ {product.price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            if product.original_price and product.original_price > product.price:
                old_price_str = f"R$ {product.original_price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                savings = product.original_price - product.price
                savings_str = f"R$ {savings:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                lines.append(f"?? **De:** ~~{old_price_str}~~")
                lines.append(f"?? **Por:** `{price_str}` à vista")
                lines.append(f"?? **Economize:** `{savings_str}`")
            else:
                lines.append(f"?? **Preço:** `{price_str}`")
        
        if product.free_shipping:
            lines.append("?? **Frete Grátis!**")

        # 3. Bloco de Cupom (Estilo Santana Tech)
        if product.coupon_code:
            lines.append("")
            lines.append("?? **CUPOM DE DESCONTO!**")
            lines.append("")
            lines.append(f"?? `{product.coupon_code}`")
            lines.append("?? _(Toque no código acima para copiar)_")

        # 4. Rodapé e Link
        lines.append("")
        lines.append("?? **Confira no link abaixo:**")
        lines.append(f"?? [Abrir Oferta]({product.link})")
        
        # 5. Tag de ajuda (opcional)
        lines.append("")
        lines.append("?? _Sempre ative os cupons pelo link para ajudar o grupo!_ ??")

        return "\n".join(lines)

    @staticmethod
    def format_coupon_only(product: Product) -> str:
        """Formata apenas o cupom se for uma oferta de cupom puro."""
        store_name = STORE_NAMES.get(product.store, product.store.value.title())
        lines = [
            f"?? **CUPOM DE DESCONTO {store_name.upper()}!**",
            "",
            f"?? **{product.title}**",
            "",
            f"?? `{product.coupon_code}`",
            "",
            "?? **Ative por aqui para ajudar o grupo:**",
            f"?? [Ir para a Loja]({product.link})",
            "",
            "?? _Sempre ative os cupons pelo link para ajudar o grupo!_ ??"
        ]
        return "\n".join(lines)
