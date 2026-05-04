"""sale_followup_first_prompt_after — birinchi DM kutish logikasi."""

from datetime import timedelta
from unittest.mock import patch

import bot.config as cfg


def test_first_prompt_short_interval_unchanged():
    with patch.object(cfg, "_optional_positive_int", return_value=None):
        i = timedelta(minutes=30)
        assert cfg.sale_followup_first_prompt_after(i, cfg.settings) == i


def test_first_prompt_long_interval_defaults_one_hour():
    with patch.object(cfg, "_optional_positive_int", return_value=None):
        got = cfg.sale_followup_first_prompt_after(timedelta(hours=24), cfg.settings)
        assert got == timedelta(hours=1)


def test_first_prompt_explicit_minutes():
    with patch.object(cfg, "_optional_positive_int", return_value=45):
        got = cfg.sale_followup_first_prompt_after(timedelta(hours=24), cfg.settings)
        assert got == timedelta(minutes=45)
