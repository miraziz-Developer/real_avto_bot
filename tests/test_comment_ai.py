from types import SimpleNamespace

from bot.db.models import CarStatus
from bot.services.comment_ai import car_facts, parse_decision, public_post_text


def _car(**kw):
    base = dict(
        id=1, title="Chevrolet Cobalt 2020", status=CarStatus.ACTIVE, price_usd=9200, mileage_km=98000,
        color="oq", transmission=None, fuel=None, position=None, paint_status="toza", location=None,
        has_accident=False, purchase_price_usd=7000, expenses_usd=300, notes="ichki izoh",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_car_facts_never_leak_internal_fields():
    f = car_facts(_car())
    assert f["narx_usd"] == 9200 and f["holati"] == "sotuvda" and f["avariya"] == "bo'lmagan"
    flat = str(f)
    assert "7000" not in flat and "300" not in flat and "ichki" not in flat


def test_car_facts_sold_and_review():
    assert car_facts(_car(status=CarStatus.SOLD))["holati"] == "sotilgan"
    review = car_facts(_car(status=CarStatus.REVIEW))
    assert set(review) == {"id", "mashina", "holati"}


def test_parse_decision_defaults_and_guards():
    d = parse_decision({"action": "REPLY", "category": "negative", "reply": "**Uzr**, tushunamiz"})
    assert d.should_reply and d.reply == "Uzr, tushunamiz" and d.notify_admin
    spam = parse_decision({"action": "reply", "category": "spam", "reply": "x"})
    assert not spam.should_reply
    weird = parse_decision({"action": "dance", "category": "???"})
    assert weird.action == "ignore" and weird.category == "other"
    long = parse_decision({"action": "reply", "category": "question", "reply": "a" * 2000})
    assert len(long.reply) <= 700
    assert parse_decision({"action": "reply", "category": "buy_intent", "reply": "ok"}).buy_intent


def test_public_post_text_drops_transcripts():
    raw = "Cobalt 2020\n[Ovoz]: narxi 9000\n[Videoda ko'rinadi]: oq\nprobeg 98 000"
    assert public_post_text(raw) == "Cobalt 2020\nprobeg 98 000"
