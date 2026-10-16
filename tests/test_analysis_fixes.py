"""2 kunlik ish tahlilida topilgan xatolar: nasiya to'lovi narx emas, kunlik obzor e'lon emas, AI ro'yxat qaytarsa."""

from __future__ import annotations

import pytest

from bot.ai.errors import AIError, json_object
from bot.services.car_extract import extract_car, is_roundup
from bot.services.car_parser import down_payment_amounts, parse_car_text, parse_price_usd

RATE = 12800


@pytest.mark.parametrize(
    ("text", "price"),
    [
        ("Bu nasiya savdoga qo'yapmiz, 600 dollar pulingiz bo'lsa, 18 oy 200 dan yana egasi", None),
        ("Matizimiz, nasiya savdoga 1000 dollar pulingiz bo'lsa, chiqib ketadi", None),
        ("boshlang'ich to'lov 3000$, narxi 9300$", 9300),
        ("Narxi: 9300$ nasiya bor, boshlang'ich 3000$", 9300),
        ("narxini 5150 dollar qo'ydik. Nasiya ham bor", 5150),
        ("Real Avtoda 1950 dollarga qo'yyapmiz", 1950),
        ("oyiga 200$ dan to'laysiz", None),
    ],
)
def test_down_payment_is_not_price(text, price):
    assert parse_price_usd(text, RATE) == price


def test_down_payment_amounts():
    assert down_payment_amounts("600 dollar pulingiz bo'lsa, narxi 5000$") == {600}


def test_daily_roundup_is_not_a_listing():
    roundup = (
        "Tak, Malibumiz 2018, 18 mingga qo'yganmiz, nasiya savdolari bor. I Oniksimiz 2023, narxini 9100 ga. "
        "Sparkimiz, buni narxini 7300 qo'yganmiz, 2021. 2100 ga 14 Matizimiz."
    )
    assert is_roundup(roundup)
    one = (
        "Real Avto avtosalonidan 2024-yilda ishlab chiqarilgan Damasimiz kelib qoldi. 3 yillik Gentra Damasga ham "
        "ko'ndirib qo'ydik. Malibu bar Damasga moslashgan. Narxini 5150 dollar qo'ydik."
    )
    assert not is_roundup(one)


def test_json_object_accepts_list():
    assert json_object({"a": 1}) == {"a": 1}
    assert json_object([{"a": 1}, {"a": 2}]) == {"a": 1, "_items_count": 2}
    with pytest.raises(AIError):
        json_object(["x"])


class _AI:
    enabled = True

    def __init__(self, answer):
        self.answer = answer

    async def chat_json(self, system, user, **kw):
        if isinstance(self.answer, Exception):
            raise self.answer
        return json_object(self.answer)


async def test_extract_car_multi_car_answers_are_not_listings():
    text = "[Ovoz]: Spark 2018 bor, Cobalt 2022 ham bor, 1000 dollar pulingiz bo'lsa"
    many = [{"is_car_listing": True, "model": "Spark", "year": 2018}, {"is_car_listing": True, "model": "Cobalt"}]
    assert not (await extract_car(text, ai=_AI(many), usd_rate_uzs=RATE)).looks_like_car()
    counted = {"is_car_listing": True, "model": "Spark", "year": 2018, "cars_mentioned": 3}
    assert not (await extract_car(text, ai=_AI(counted), usd_rate_uzs=RATE)).looks_like_car()


async def test_extract_car_drops_regex_price_that_ai_calls_down_payment():
    text = "Damas 2014, probeg 735 ming. Nasiyaga beramiz, 600$ boshida, qolganini 18 oy"
    assert parse_car_text(text, usd_rate_uzs=RATE).price_usd == 600  # qoidalar o'zi buni bilmaydi
    ai = _AI({"is_car_listing": True, "model": "Damas", "year": 2014, "price_amount": None,
              "notes": "Nasiyaga: boshlang'ich to'lov 600 dollar, 18 oy.", "confidence": 0.9})
    parsed = await extract_car(text, ai=ai, usd_rate_uzs=RATE)
    assert parsed.price_usd is None and parsed.mileage_km == 735000


def test_jiguli_aliases():
    from bot.db.cars_repo import same_car_model

    assert parse_car_text("🚘AVTOMOBIL: JUGULI 2106\n🗓️yili: 1990", usd_rate_uzs=RATE).model == "2106"
    assert same_car_model("Jiguli", "2106") and not same_car_model("2106", "2107")


def test_banner_has_no_car_facts():
    from bot.handlers.channel_watch import _has_car_facts

    assert not _has_car_facts("BUGUN\n📆16 10.2026\nNASIYA SOVDASI BOR\n☎️Aloqa: +998974337608")
    assert not _has_car_facts("")
    assert _has_car_facts("🚘AVTOMOBIL: SPARK\n🗓️yili: 2018\n📍probeg: 90.000km")

