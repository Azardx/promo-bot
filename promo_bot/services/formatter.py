"""
Serviço de formatação de mensagens profissional — PromoBot v2.3+

CORREÇÕES:
- Emojis corrigidos (encoding UTF-8 adequado)
- Suporte a loja de origem real (Pelando/Promobit → Amazon, ML, etc.)
- MessageFormatter alias para compatibilidade com test_changes.py
- Formatação Markdown compatível com Telegram parse_mode='Markdown'
"""

from __future__ import annotations

import re
from typing import Optional

from promo_bot.database.models import Product, Store

# ── Emojis por loja ──────────────────────────────────────────────────────────
STORE_EMOJIS: dict[Store, str] = {
    Store.AMAZON:       "\U0001f4e6",   # 📦
    Store.KABUM:        "\U0001f5a5",   # 🖥️
    Store.ALIEXPRESS:   "\U0001f30f",   # 🌏
    Store.SHOPEE:       "\U0001f6cd",   # 🛍️
    Store.PELANDO:      "\U0001f525",   # 🔥
    Store.PROMOBIT:     "\u26a1",       # ⚡
    Store.TERABYTE:     "\U0001f4bb",   # 💻
    Store.MERCADOLIVRE: "\U0001f6d2",   # 🛒
    Store.UNKNOWN:      "\U0001f3ea",   # 🏪
}

# ── Mapeamento de nomes de lojas externas para emojis ───────────────────────
_ORIGIN_EMOJI_MAP: dict[str, str] = {
    "amazon":          "\U0001f4e6",   # 📦
    "kabum":           "\U0001f5a5",   # 🖥️
    "aliexpress":      "\U0001f30f",   # 🌏
    "shopee":          "\U0001f6cd",   # 🛍️
    "mercado livre":   "\U0001f6d2",   # 🛒
    "mercadolivre":    "\U0001f6d2",   # 🛒
    "terabyte":        "\U0001f4bb",   # 💻
    "magazine luiza":  "\U0001f4d8",   # 📘
    "magalu":          "\U0001f4d8",   # 📘
    "americanas":      "\U0001f534",   # 🔴
    "casas bahia":     "\U0001f3e0",   # 🏠
    "ponto frio":      "\u2744",       # ❄️
    "extra":           "\u2b50",       # ⭐
    "carrefour":       "\U0001f6d2",   # 🛒
    "submarino":       "\U0001f6a2",   # 🚢
    "c&a":             "\U0001f455",   # 👔
    "renner":          "\U0001f457",   # 👗
    "hering":          "\U0001f455",   # 👔
    "pague menos":     "\U0001f48a",   # 💊
    "netshoes":        "\U0001f45f",   # 👟
    "centauro":        "\U0001f3c3",   # 🏃
}

# ── Nomes amigáveis ──────────────────────────────────────────────────────────
STORE_NAMES: dict[Store, str] = {
    Store.AMAZON:       "Amazon",
    Store.KABUM:        "KaBuM!",
    Store.ALIEXPRESS:   "AliExpress",
    Store.SHOPEE:       "Shopee",
    Store.PELANDO:      "Pelando",
    Store.PROMOBIT:     "Promobit",
    Store.TERABYTE:     "Terabyte Shop",
    Store.MERCADOLIVRE: "Mercado Livre",
    Store.UNKNOWN:      "Loja",
}

# Emojis de UI usados no template
_FIRE   = "\U0001f525"   # 🔥
_TAG    = "\U0001f3f7"   # 🏷️
_SHOP   = "\U0001f6cd"   # 🛍️
_OLD    = "\u274c"       # ❌
_NEW    = "\u2705"       # ✅
_SAVE   = "\U0001f4b0"   # 💰
_TRUCK  = "\U0001f69a"   # 🚚
_TICKET = "\U0001f39f"   # 🎟️
_FINGER = "\U0001f449"   # 👉
_LINK   = "\U0001f517"   # 🔗
_BULB   = "\U0001f4a1"   # 💡
_PRAY   = "\U0001f64f"   # 🙏


def _get_origin_emoji(origin_store: str) -> str:
    """Retorna emoji para o nome de uma loja externa."""
    key = origin_store.lower().strip()
    for pattern, emoji in _ORIGIN_EMOJI_MAP.items():
        if pattern in key:
            return emoji
    return "\U0001f3ea"  # 🏪


def _fmt_price(value: float) -> str:
    """Formata float no padrão monetário brasileiro (R$ 1.299,90)."""
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _escape_markdown(text: str) -> str:
    """
    Escapa caracteres especiais do Markdown V1 do Telegram.
    Não escapa caracteres que já fazem parte da formatação intencional.
    """
    # Apenas escapa _ e ` fora de contexto de formatação
    return text.replace("_", "\\_")


class PromoFormatter:
    """Formatador de mensagens para o canal de promoções."""

    # Limite da API do Telegram para captions de foto (1024 chars)
    MAX_CAPTION = 1024
    # Limite para mensagens de texto puro (4096 chars)
    MAX_TEXT    = 4096

    @staticmethod
    def format_promo(product: Product) -> str:
        """
        Gera a mensagem formatada para uma promoção.

        Se o produto veio de um agregador (Pelando/Promobit), exibe a
        loja de origem real (Amazon, ML…) em vez do agregador.
        """
        # ── Determina loja exibida ────────────────────────────────────────
        origin_store: str = (product.extra or {}).get("origin_store", "")
        is_aggregator = product.store in (Store.PELANDO, Store.PROMOBIT)

        if origin_store and is_aggregator:
            display_store = origin_store
            display_emoji = _get_origin_emoji(origin_store)
        else:
            display_store = STORE_NAMES.get(product.store, product.store.value.title())
            display_emoji = STORE_EMOJIS.get(product.store, "\U0001f3ea")

        safe_title = _escape_markdown(product.title)

        # ── Cabeçalho ─────────────────────────────────────────────────────
        lines: list[str] = [
            f"{_FIRE} *PROMO\u00c7\u00c3O ATIVA* {_FIRE}",
            "",
            f"{_SHOP} *{safe_title}*",
            "",
            f"{display_emoji} *Loja:* {display_store}",
        ]

        # ── Bloco de preços ───────────────────────────────────────────────
        if product.price:
            price_str = _fmt_price(product.price)

            if product.original_price and product.original_price > product.price:
                old_str    = _fmt_price(product.original_price)
                savings    = product.original_price - product.price
                savings_str = _fmt_price(savings)
                disc       = product.calculated_discount
                disc_tag   = f"  *(-{disc:.0f}%)*" if disc else ""

                lines += [
                    f"{_OLD} *De:* ~{old_str}~",
                    f"{_NEW} *Por:* `{price_str}` \u00e0 vista{disc_tag}",
                    f"{_SAVE} *Economize:* `{savings_str}`",
                ]
            else:
                lines.append(f"{_SAVE} *Pre\u00e7o:* `{price_str}`")

        if product.free_shipping:
            lines.append(f"{_TRUCK} *Frete Gr\u00e1tis!*")

        # ── Bloco de cupom ────────────────────────────────────────────────
        if product.coupon_code:
            lines += [
                "",
                f"{_TICKET} *CUPOM DE DESCONTO!*",
                "",
                f"{_FINGER} `{product.coupon_code}`",
                "_(Toque no c\u00f3digo para copiar)_",
            ]

        # ── Rodapé ────────────────────────────────────────────────────────
        lines += [
            "",
            f"{_LINK} *Confira no link abaixo:*",
            f"{_FINGER} [Abrir Oferta]({product.link})",
            "",
            f"{_BULB} _Sempre ative os cupons pelo link para ajudar o grupo!_ {_PRAY}",
        ]

        return "\n".join(lines)

    @staticmethod
    def format_coupon_only(product: Product) -> str:
        """Formata mensagem exclusiva de cupom."""
        origin_store: str = (product.extra or {}).get("origin_store", "")
        store_name = (
            origin_store
            or STORE_NAMES.get(product.store, product.store.value.title())
        )
        lines = [
            f"{_TICKET} *CUPOM {store_name.upper()}!*",
            "",
            f"{_SHOP} *{_escape_markdown(product.title)}*",
            "",
            f"{_FINGER} `{product.coupon_code}`",
            "",
            f"{_LINK} *Ative pelo link para ajudar o grupo:*",
            f"{_FINGER} [Ir para a Loja]({product.link})",
            "",
            f"{_BULB} _Sempre ative os cupons pelo link!_ {_PRAY}",
        ]
        return "\n".join(lines)

    def truncate_for_caption(self, text: str) -> str:
        """Trunca a mensagem para o limite de caption do Telegram."""
        if len(text) <= self.MAX_CAPTION:
            return text
        return text[: self.MAX_CAPTION - 4] + "\n..."

    def _is_valid_image_url(self, url: str) -> bool:
        """Valida se a URL de imagem é utilizável."""
        if not url or not url.startswith("http"):
            return False
        if "placeholder" in url.lower() or url.startswith("data:"):
            return False
        return bool(re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", url, re.I)
                    or re.search(r"/(image|photo|img|media)/", url, re.I))

    def _truncate_for_caption(self, text: str) -> str:
        """Alias público para compatibilidade."""
        return self.truncate_for_caption(text)

    @property
    def stats(self) -> dict:
        return {"formatter": "PromoFormatter v2.3+"}


# ── Alias de compatibilidade (test_changes.py usa MessageFormatter) ──────────
class MessageFormatter(PromoFormatter):
    """Alias de compatibilidade — use PromoFormatter em código novo."""

    def format_product(self, product: Product) -> str:
        return self.format_promo(product)
