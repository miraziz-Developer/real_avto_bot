import pytest

from bot.utils.phone import find_uz_phone, normalize_uz_phone


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+998 90 123 45 67", "+998901234567"),
        ("998901234567", "+998901234567"),
        ("90 123-45-67", "+998901234567"),
        ("(90) 1234567", "+998901234567"),
        ("8 90 123 45 67", "+998901234567"),
        ("12345", None),
        ("+7 999 123 45 67", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_uz_phone(raw, expected):
    assert normalize_uz_phone(raw) == expected


def test_find_uz_phone_in_text():
    assert find_uz_phone("Ertaga boraman, raqamim +998 93 555 11 22, Sardor") == "+998935551122"
    assert find_uz_phone("raqam 97 111 22 33") == "+998971112233"
    assert find_uz_phone("Malibu 2 2019 narxi qancha?") is None
