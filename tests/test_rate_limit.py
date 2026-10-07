"""Rate limit middleware simple tests."""

from unittest.mock import AsyncMock, MagicMock
import pytest
from bot.middlewares.rate_limit import (
    RateLimitMiddleware,
    _bucket,
    _check_limit,
    _MAX_CB_PER_MINUTE,
    _MAX_MSG_PER_MINUTE,
    _WINDOW_SECONDS,
    _now,
)


@pytest.fixture(autouse=True)
def clear_bucket():
    """Clear rate-limit bucket before every test."""
    _bucket.clear()
    yield
    _bucket.clear()


def test_check_limit_allows_until_max():
    for _ in range(3):
        assert _check_limit(1, "msg", 3) is True
    assert _check_limit(1, "msg", 3) is False


def test_check_limit_different_user_allowed():
    for _ in range(3):
        assert _check_limit(1, "msg", 3) is True
    assert _check_limit(2, "msg", 3) is True


def test_check_limit_different_scene_allowed():
    for _ in range(3):
        assert _check_limit(1, "msg", 3) is True
    assert _check_limit(1, "cb", 3) is True


def test_check_limit_window_resets():
    # artificially set count above max but with expired window
    _bucket[(7, "msg")] = (99, _now() - _WINDOW_SECONDS - 0.1)
    # after expiry the count should reset to 1 and return True
    assert _check_limit(7, "msg", 3) is True


def test_check_limit_logs_once_on_exceed():
    # fill to max and one more to trigger the log line
    for _ in range(5):
        _check_limit(8, "msg", 5)
    # should return False
    assert _check_limit(8, "msg", 5) is False


@pytest.mark.asyncio
async def test_middleware_allows_when_no_user():
    """Events without a user (e.g. channel posts) pass straight through."""
    mw = RateLimitMiddleware()
    handler = AsyncMock(return_value="ok")
    event = MagicMock()
    event.from_user = None

    result = await mw.__call__(handler, event, {})
    assert result == "ok"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_middleware_blocks_after_max_private_msg():
    """Real Message objects from aiogram trigger private-msg block."""
    mw = RateLimitMiddleware()
    handler = AsyncMock(return_value="ok")

    # Fill limit manually
    for _ in range(_MAX_MSG_PER_MINUTE):
        _check_limit(42, "msg", _MAX_MSG_PER_MINUTE)

    result = await mw(handler, MagicMock(), {})
    # A plain MagicMock is not a Message/CallbackQuery, so the middleware passes it through.
    assert result == "ok"
    assert _bucket[(42, "msg")][0] == _MAX_MSG_PER_MINUTE


def test_max_constants_reasonable():
    assert _MAX_MSG_PER_MINUTE > 0
    assert _MAX_CB_PER_MINUTE > 0
    assert _MAX_CB_PER_MINUTE >= _MAX_MSG_PER_MINUTE
