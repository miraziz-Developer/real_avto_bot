"""Ish vaqti bo'yicha hisob (Toshkent, UTC+5): kechqurun kelgan e'lonning muzlatish muddati tunda «yonib» ketmasin."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

TASHKENT = timezone(timedelta(hours=5))


def is_work_time(moment: datetime, *, start_hour: int, end_hour: int) -> bool:
    local = moment.astimezone(TASHKENT)
    return start_hour <= local.hour < end_hour


def add_work_hours(moment: datetime, hours: float, *, start_hour: int, end_hour: int) -> datetime:
    """`moment` dan boshlab faqat ish soatlarini sanab, `hours` qo'shadi. Natija UTC da.

    Masalan (9–21): 20:00 + 6 soat → ertasi 14:00; 23:30 + 6 → ertasi 15:00.
    """
    if end_hour <= start_hour:
        raise ValueError("Ish vaqti noto'g'ri: end_hour > start_hour bo'lishi kerak")
    remaining = timedelta(hours=max(0.0, hours))
    local = moment.astimezone(TASHKENT)
    while True:
        day_start = local.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        day_end = local.replace(hour=end_hour, minute=0, second=0, microsecond=0)
        if local < day_start:
            local = day_start
        if local >= day_end:
            local = day_start + timedelta(days=1)
            continue
        available = day_end - local
        if remaining <= available:
            return (local + remaining).astimezone(timezone.utc)
        remaining -= available
        local = day_start + timedelta(days=1)
