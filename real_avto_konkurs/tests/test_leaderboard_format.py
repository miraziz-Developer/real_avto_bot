from types import SimpleNamespace

from bot.services.leaderboard import LeaderboardService


def test_format_row_with_alias_username():
    u = SimpleNamespace(
        leaderboard_alias="Boss1",
        username="real_shop",
        first_name="Ali",
        last_name=None,
        tg_id=123456789,
    )
    line = LeaderboardService.format_user_row_public(u)
    assert "Boss1" in line
    assert "real_shop" in line
    assert "id:<code>123456789</code>" in line


def test_format_top_message_empty():
    text = LeaderboardService.format_top_message([], 50)
    assert "TOP 15" in text
    assert "yo‘q" in text or "statistika" in text.lower()
