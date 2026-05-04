"""Anonim chat: telefon, username, tashqi havolalar — filtr."""

from __future__ import annotations

import re


def validate_anonymous_content(text: str | None) -> tuple[bool, str | None]:
    """
    Matn anonim qoidaga mos bo‘lsa (True, None).
    Taqiqlangan bo‘lsa (False, foydalanuvchiga qisqa sabab).
    """
    if text is None or not str(text).strip():
        return True, None
    t = str(text).strip()
    if "@" in t:
        return False, "Bu chatda @username yozmaylik — savol matni bilan yozing."
    if re.search(r"\b(?:telegram|whatsapp|viber)\b", t, re.I):
        return False, "Tashqi messenjer nomlari yozmaylik."
    low = t.lower().replace(" ", "")
    for bad in ("t.me/", "telegram.me/", "http://", "https://", "wa.me"):
        if bad in low:
            return False, "Havolasiz qisqa savol yozing."
    # Bo'shliq bilan yozilgan telefon ham: barcha raqamlar ketma-ket 9+ bo‘lsa
    digits_only = re.sub(r"\D", "", t)
    if len(digits_only) >= 9:
        return False, "Telefonni bu chatda yozmaylik — Real Avto raqamidan foydalaning."
    return True, None
