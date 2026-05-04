from bot.utils.anonym_guard import validate_anonymous_content


def test_anonym_allows_normal_question():
    ok, err = validate_anonymous_content("Kraska qayerdan bo‘yalgan?")
    assert ok and err is None


def test_anonym_blocks_at():
    ok, err = validate_anonymous_content("Menga @username yozing")
    assert not ok and err


def test_anonym_blocks_phone_digits():
    ok, err = validate_anonymous_content("Qo‘ng‘iroq qiling 90 123 45 67")
    assert not ok and err


def test_anonym_blocks_url():
    ok, err = validate_anonymous_content("Mana https://t.me/x")
    assert not ok and err


def test_anonym_allows_year_range():
    ok, err = validate_anonymous_content("2019 yilda sotib olganman")
    assert ok and err is None
