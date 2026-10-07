"""Forma chegaralari (baza ustunlariga mos — oxirgi qadamda «Saqlanmadi» chiqmasligi uchun)."""

CAR_YEAR_MIN = 1990
CAR_YEAR_MAX = 2030

MILEAGE_MAX_KM = 2_000_000
PRICE_USD_MIN = 100
# 1 mln dan katta qiymatlar eski so'm→USD migratsiyasi bilan adashtirilmasin; real mashinalar uchun yetarli
PRICE_USD_MAX = 1_000_000
BUDGET_USD_MIN = 500
BUDGET_USD_MAX = PRICE_USD_MAX
MODEL_NAME_MAX = 100
PHONE_DIGITS_MIN = 9
PHONE_DIGITS_MAX = 15  # E.164
