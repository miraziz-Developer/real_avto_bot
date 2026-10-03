from datetime import datetime, timedelta, timezone

import pytest

from bot.services.work_hours import TASHKENT, add_work_hours, is_work_time


def tk(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TASHKENT)


@pytest.mark.parametrize(
    ("start", "hours", "expected"),
    [
        (tk(5, 10), 6, tk(5, 16)),  # ish vaqti ichida
        (tk(5, 20), 6, tk(6, 14)),  # kechqurun: 1 soat bugun + 5 soat ertaga
        (tk(5, 23, 30), 6, tk(6, 15)),  # tunda: ertalab 9 dan sanaladi
        (tk(6, 3), 6, tk(6, 15)),  # erta tong
        (tk(5, 21), 6, tk(6, 15)),  # aynan yopilish vaqti
        (tk(5, 15), 30, tk(7, 21)),  # bir necha kun: 6 + 12 + 12 = 30 (worker ertalab chiqaradi)
        (tk(5, 10), 0, tk(5, 10)),
    ],
)
def test_add_work_hours(start, hours, expected):
    got = add_work_hours(start, hours, start_hour=9, end_hour=21)
    assert got == expected
    assert got.tzinfo is not None and got.utcoffset() == timedelta(0)  # natija UTC da


def test_input_in_utc():
    start = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)  # Toshkent 20:00
    assert add_work_hours(start, 6, start_hour=9, end_hour=21) == tk(6, 14)


def test_is_work_time():
    assert is_work_time(tk(5, 9), start_hour=9, end_hour=21)
    assert is_work_time(tk(5, 20, 59), start_hour=9, end_hour=21)
    assert not is_work_time(tk(5, 21), start_hour=9, end_hour=21)
    assert not is_work_time(tk(5, 3), start_hour=9, end_hour=21)


def test_bad_hours():
    with pytest.raises(ValueError):
        add_work_hours(tk(5, 10), 1, start_hour=21, end_hour=9)
