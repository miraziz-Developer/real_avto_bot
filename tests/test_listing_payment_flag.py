import pytest

from bot import config


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, False),
        ("", False),
        ("false", False),
        ("0", False),
        ("true", True),
        ("1", True),
        ("YES", True),
        (" on ", True),
    ],
)
def test_bool_env(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("LISTING_PAYMENT_ENABLED", raising=False)
    else:
        monkeypatch.setenv("LISTING_PAYMENT_ENABLED", raw)
    assert config._bool("LISTING_PAYMENT_ENABLED", False) is expected
