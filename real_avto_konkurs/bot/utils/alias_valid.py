import re

MAX_ALIAS = 32
MIN_ALIAS = 3

_ALLOWED = re.compile(r"^[\w\-\s]{3,32}$", re.UNICODE)


def normalize_and_validate_alias(raw: str) -> str:
    s = " ".join(raw.strip().split())
    if not _ALLOWED.fullmatch(s):
        raise ValueError(f"Faqat harf, raqam, probel, tire, pastki chiziq — {MIN_ALIAS}–{MAX_ALIAS} belgi")
    low = s.lower()
    if "@" in s or "://" in low or "t.me" in low:
        raise ValueError("Havola yoki @ ishlatilmaydi")
    return s
