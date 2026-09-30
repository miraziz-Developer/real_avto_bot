"""O‘zbekiston telefon raqamlarini bir xil ko‘rinishga keltirish (+998XXXXXXXXX)."""

from __future__ import annotations

import re

_DIGITS = re.compile(r"\D+")
# Matn ichidan raqamga o‘xshash bo‘lakni topish: +998 90 123-45-67, 90 1234567, 901234567 ...
_PHONE_IN_TEXT = re.compile(r"(?:\+?998[\s\-()]*)?\(?\d{2}\)?[\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")


def normalize_uz_phone(raw: str | None) -> str | None:
    """+998XXXXXXXXX yoki None (raqam noto‘g‘ri bo‘lsa)."""
    digits = _DIGITS.sub("", raw or "")
    if len(digits) == 9:
        digits = "998" + digits
    elif len(digits) == 10 and digits.startswith("8"):
        # Eski format: 8 90 123 45 67
        digits = "998" + digits[1:]
    if len(digits) != 12 or not digits.startswith("998"):
        return None
    return "+" + digits


def find_uz_phone(text: str | None) -> str | None:
    """Erkin matndan birinchi to‘g‘ri O‘zbekiston raqamini topish."""
    for m in _PHONE_IN_TEXT.finditer(text or ""):
        phone = normalize_uz_phone(m.group(0))
        if phone:
            return phone
    return None
