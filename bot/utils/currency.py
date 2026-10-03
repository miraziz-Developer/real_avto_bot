from __future__ import annotations

from bot.config import settings


def uzs_to_usd(uzs: int | float) -> float:
    rate = max(1, settings.usd_rate_uzs)
    return float(uzs) / float(rate)


def usd_to_uzs(usd: int | float) -> int:
    rate = max(1, settings.usd_rate_uzs)
    return int(float(usd) * float(rate))


def fmt_usd_from_uzs(uzs: int | float) -> str:
    return f"${uzs_to_usd(uzs):,.0f}"


def fmt_usd(usd: int | float) -> str:
    return f"${float(usd):,.0f}"


def fmt_price(usd: int | float | None, *, empty: str = "") -> str:
    """Kartalar va xabarlar uchun narx: «$9 200» (bo'sh joy bilan); narx yo'q bo'lsa `empty`."""
    if not usd:
        return empty
    return f"${int(usd):,}".replace(",", " ")
