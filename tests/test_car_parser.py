import pytest

from bot.services.car_extract import ai_dict_to_parsed
from bot.services.car_parser import is_sold_text, parse_admin_edit, parse_car_text

RATE = 12800


def p(text: str):
    return parse_car_text(text, usd_rate_uzs=RATE)


def test_real_avto_template():
    r = p("Damas 📆 yili: 2022\n📍 probeg: 76.000km\n💰 Narxi: 7 800$\nKraskasi toza, 2-pozitsiya\n📍 Yangiyo'l")
    assert (r.brand, r.model, r.year, r.mileage_km, r.price_usd) == ("Chevrolet", "Damas", 2022, 76000, 7800)
    assert r.paint_status == "toza"
    assert r.position == "2-pozitsiya"
    # «📍 probeg» qatori lokatsiya deb olinmasligi kerak
    assert r.location == "Yangiyo'l"
    assert r.is_complete()


@pytest.mark.parametrize(
    ("text", "brand", "model"),
    [
        ("jentra 2019", "Chevrolet", "Gentra"),
        ("Кобальт 2020 йил", "Chevrolet", "Cobalt"),
        ("Nexia 3 2018", "Chevrolet", "Nexia 3"),
        ("nexia 2018", "Chevrolet", "Nexia"),
        ("Ravon Gentra 2017", "Ravon", "Gentra"),
        ("JAC J7 2024", "JAC", "J7"),
        ("BYD Song Plus 2023", "BYD", "Song Plus"),
        ("Lacetti 2012", "Chevrolet", "Lacetti"),
    ],
)
def test_brand_model_detection(text, brand, model):
    r = p(text)
    assert (r.brand, r.model) == (brand, model)


@pytest.mark.parametrize(
    ("text", "year"),
    [
        ("yili: 2023/2024", 2023),
        ("2019 yil", 2019),
        ("Spark 2021, 31000 km", 2021),
        ("год 2015", 2015),
        ("narx $2015", None),  # narx yil emas
    ],
)
def test_year(text, year):
    assert p(text).year == year


@pytest.mark.parametrize(
    ("text", "km"),
    [
        ("probeg: 76.000km", 76000),
        ("probeg 120 ming", 120000),
        ("31000 km", 31000),
        ("пробег 98 000 км", 98000),
        ("probeg 64k", 64000),
    ],
)
def test_mileage(text, km):
    assert p(text).mileage_km == km


@pytest.mark.parametrize(
    ("text", "usd"),
    [
        ("Narxi: 7 800$", 7800),
        ("narx $14 500", 14500),
        ("9.8k$", 9800),
        ("narxi 21500", 21500),  # belgisiz — dollar
        ("цена 118 млн", round(118_000_000 / RATE)),
        ("112 000 000 so'm", round(112_000_000 / RATE)),
        ("95 mln so'm", round(95_000_000 / RATE)),
    ],
)
def test_price(text, usd):
    assert p(text).price_usd == usd


def test_non_car_post():
    r = p("Hammaga hayrli kun! Bizda yangi mashinalar bor")
    assert not r.looks_like_car()


@pytest.mark.parametrize(
    ("text", "sold"),
    [
        ("SOTILDI ✅", True),
        ("sotilgan", True),
        ("Продано", True),
        ("СОТИЛДИ", True),
        ("Sotiladi", False),  # «sotuvda» ma'nosida — sotildi emas!
        ("Mashina sotuvda", False),
        ("", False),
    ],
)
def test_sold_detection(text, sold):
    assert is_sold_text(text) is sold


def test_admin_edit():
    values, bad = parse_admin_edit(
        "narx 9800\nyil: 2021\nprobeg 76,000\nmodel Gentra\nxarid 8000, xarajat 300\ndtp yo'q\npoz 3\nsalom",
        usd_rate_uzs=RATE,
    )
    assert values == {
        "price_usd": 9800,
        "year": 2021,
        "mileage_km": 76000,
        "model": "Gentra",
        "purchase_price_usd": 8000,
        "is_own": True,
        "expenses_usd": 300,
        "has_accident": False,
        "position": "3-pozitsiya",
    }
    assert bad == ["salom"]


def test_ai_dict_validation_drops_garbage():
    r = ai_dict_to_parsed(
        {
            "is_car_listing": True,
            "brand": "Chevrolet",
            "model": "Cobalt",
            "year": 1850,  # noto'g'ri
            "mileage_km": "98000",
            "price_amount": 118000000,
            "price_currency": "UZS",
            "transmission": "robot",  # ruxsat etilmagan
            "confidence": 3,
        },
        usd_rate_uzs=RATE,
    )
    assert r.year is None
    assert r.mileage_km == 98000
    assert r.price_usd == round(118_000_000 / RATE)
    assert r.transmission is None
    assert r.confidence == 1.0
    assert r.is_car_listing is True


def test_merge_missing_keeps_regex_values():
    regex = p("Damas yili: 2022 narx 7800$")
    ai = ai_dict_to_parsed({"model": "Labo", "year": 2020, "mileage_km": 50000}, usd_rate_uzs=RATE)
    regex.merge_missing(ai)
    assert (regex.model, regex.year, regex.mileage_km) == ("Damas", 2022, 50000)


@pytest.mark.parametrize(
    ("text", "strict", "extended"),
    [
        ("Baraka bo'ldi ✅", False, True),
        ("Barakasini bersin", False, True),
        ("olib ketildi", False, True),
        ("SOTILDI", True, True),
        ("Mashina sotuvda, kelinglar", False, False),
    ],
)
def test_sold_extended_only_for_edits_and_replies(text, strict, extended):
    assert is_sold_text(text) is strict
    assert is_sold_text(text, extended=True) is extended


def test_reserved_and_phone():
    from bot.services.car_parser import has_phone, is_reserved_text

    assert is_reserved_text("Bron") and is_reserved_text("бронь") and not is_reserved_text("Bronza rang")
    assert has_phone("📞 +998 97 782 92 99") and has_phone("97 433 76 08") and has_phone("+998977829299")
    assert not has_phone("Cobalt 2020, probeg 98 000 km, narxi 9200$")
