"""Foydalanuvchi kiritgan sonlarni xavfsiz o'qish."""

from __future__ import annotations

import re

_ASCII_DIGITS = re.compile(r"[0-9]+")
_SEPARATORS = re.compile(r"[\s,.'’_]")


def parse_int(raw: str | None, *, allow_separators: bool = True) -> int | None:
    """Faqat ASCII raqamlar ('35 000', '35,000', '35.000' ham). Aks holda None.

    `str.isdigit()` '²' yoki arabcha raqamlarni ham qabul qiladi, `int()` esa ularda yiqiladi —
    shuning uchun aniq regex.
    """
    s = (raw or "").strip()
    if allow_separators:
        s = _SEPARATORS.sub("", s)
    if not s or len(s) > 12 or not _ASCII_DIGITS.fullmatch(s):
        return None
    return int(s)


def parse_int_in_range(raw: str | None, lo: int, hi: int, *, allow_separators: bool = True) -> int | None:
    v = parse_int(raw, allow_separators=allow_separators)
    if v is None or v < lo or v > hi:
        return None
    return v
