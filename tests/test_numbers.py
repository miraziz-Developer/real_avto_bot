from bot.handlers.ad_listing import parse_phone_from_text
from bot.utils.numbers import parse_int, parse_int_in_range


def test_parse_int_accepts_common_formats():
    assert parse_int("35000") == 35000
    assert parse_int("35 000") == 35000
    assert parse_int("35,000") == 35000
    assert parse_int("35.000") == 35000


def test_parse_int_rejects_garbage_and_unicode_digits():
    for bad in ["", "abc", "-5", "12a", "²", "١٢٣", "1e5", None, "9" * 13]:
        assert parse_int(bad) is None, bad


def test_parse_int_in_range():
    assert parse_int_in_range("2020", 1990, 2030, allow_separators=False) == 2020
    assert parse_int_in_range("2 020", 1990, 2030, allow_separators=False) is None
    assert parse_int_in_range("1989", 1990, 2030) is None
    assert parse_int_in_range("99999999999", 0, 2_000_000) is None


def test_phone_length_bounded():
    assert parse_phone_from_text("+998 90 123 45 67") == "+998901234567"
    assert parse_phone_from_text("12345") is None
    assert parse_phone_from_text("+" + "9" * 20) is None
