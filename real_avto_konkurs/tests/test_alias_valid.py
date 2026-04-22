import pytest

from bot.utils.alias_valid import normalize_and_validate_alias


def test_alias_ok():
    assert normalize_and_validate_alias("  Real_Avto  ") == "Real_Avto"


def test_alias_too_short():
    with pytest.raises(ValueError):
        normalize_and_validate_alias("ab")


def test_alias_rejects_link():
    with pytest.raises(ValueError):
        normalize_and_validate_alias("see https://x.com")
