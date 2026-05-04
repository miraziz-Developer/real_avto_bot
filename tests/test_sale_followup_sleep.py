"""sale_followup_loop uyqu oralig‘i (intervalga bog‘liq)."""

from datetime import timedelta

from bot.workers.sale_followup import _loop_sleep_seconds


def test_loop_sleep_short_interval_clamped():
    s = _loop_sleep_seconds(timedelta(minutes=3))
    assert 10 <= s <= 60


def test_loop_sleep_hour_interval_max_five_minutes():
    assert _loop_sleep_seconds(timedelta(hours=1)) == 300
