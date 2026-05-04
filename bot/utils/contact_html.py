"""Telefon raqamini HTML matn / tel: havolasi sifatida bir xil ko‘rsatish."""

from __future__ import annotations

import html


def sales_phones_links_html() -> str:
    """Barcha savdo raqamlari (SALES_PHONE) — har bir qator havola."""
    from bot.config import sales_phone_entries

    return "\n".join(phone_link_html(p) for p in sales_phone_entries())


def phone_link_html(phone: str) -> str:
    """Qisqa matn yoki <code> / <a href=tel:…> havola."""
    p = (phone or "").strip()
    if not p:
        return "—"
    digits = "".join(c for c in p if c.isdigit())
    if len(digits) < 9:
        return html.escape(p)
    if len(digits) == 9:
        tel = "+998" + digits
    elif digits.startswith("998"):
        tel = "+" + digits
    else:
        tel = "+" + digits
    return f'<a href="tel:{tel}">{html.escape(p)}</a>'
