from bot.db.repositories import _brand_synonyms_lower


def test_brand_synonyms_chevrolet_includes_daewoo():
    s = _brand_synonyms_lower("Chevrolet")
    assert "chevrolet" in s and "daewoo" in s


def test_brand_synonyms_daewoo_includes_chevrolet():
    s = _brand_synonyms_lower("Daewoo")
    assert "chevrolet" in s and "daewoo" in s


def test_brand_synonyms_other_unchanged():
    assert _brand_synonyms_lower("Toyota") == frozenset({"toyota"})
