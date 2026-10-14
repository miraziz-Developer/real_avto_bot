"""Faqat emojidan iborat komment («❤️», «🔥🔥», «😂», «👎») — reaksiyaga qarab qisqa javob, AI'siz.

Instagram API postga bosilgan like/reaksiyalarni bermaydi; odamlar reaksiyani ko'pincha emoji-komment qilib
yozadi. Javoblar bir nechta variantdan tanlanadi (bir xil matn qayta-qayta chiqmasin — Instagram spam deb biladi).
"""

from __future__ import annotations

import random
import unicodedata

from bot.config import sales_phone_entries
from bot.db.models import Car, CarStatus
from bot.services.business_info import with_ai_label

_LOVE = set("❤🧡💛💚💙💜🖤🤍🤎💖💗💓💞💕💘😍🥰😘🤩")
_FIRE = set("🔥💯👍👏👌💪🙌🚀⚡🏆🥇✅😎🤝")
_FUNNY = set("😂🤣😆😁😄😃😀😅😹")
_WOW = set("😮😯😲😱🤯😳🙀👀")
_SAD = set("😢😭😔😞☹🙁😕💔🥺")
_BAD = set("👎😡🤬😠💩🤮🤢🖕")

_IGNORABLE = {"️", "‍", "⃣"}


def is_emoji_only(text: str | None) -> bool:
    """Matnda harf/raqam yo'q va kamida bitta emoji bor."""
    chars = [c for c in (text or "") if not c.isspace() and c not in _IGNORABLE]
    if not chars:
        return False
    if any(unicodedata.category(c)[0] in "LN" for c in chars):
        return False
    return any(unicodedata.category(c) == "So" for c in chars)


def reaction_kind(text: str) -> str:
    chars = set(text)
    for kind, group in (("bad", _BAD), ("sad", _SAD), ("funny", _FUNNY), ("wow", _WOW), ("love", _LOVE), ("fire", _FIRE)):
        if chars & group:
            return kind
    return "fire"


def _on_sale(car: Car | None) -> bool:
    return car is not None and car.status in (CarStatus.ACTIVE, CarStatus.RESERVED)


def emoji_reply(text: str, car: Car | None = None, *, rnd: random.Random | None = None) -> str:
    rnd = rnd or random
    phone = sales_phone_entries()[0]
    kind = reaction_kind(text)
    if kind == "bad":
        options = [
            "Fikringiz uchun rahmat 🙏 Nima yoqmaganini yozsangiz, albatta e'tibor beramiz.",
            "Rahmat, fikringiz biz uchun muhim 🙏 Taklif yoki savol bo'lsa, yozing.",
        ]
    elif kind == "sad":
        options = [
            "🙏 Savol yoki muammo bo'lsa, yozing — yordam beramiz.",
            "Biror narsa noto'g'ri bo'lsa, ayting — albatta hal qilamiz 🙏",
        ]
    elif kind == "funny":
        options = ["😄 Rahmat! Savollar bo'lsa, bemalol yozing.", "😁 Kayfiyatingiz zo'r bo'lsin! Savol bo'lsa — yozing."]
    elif kind == "wow":
        options = (
            [f"Ha, mashina zo'r 😉 Batafsil va narx: {phone}", f"Shunday 😎 Ko'rishga keling yoki qo'ng'iroq qiling: {phone}"]
            if _on_sale(car)
            else ["😉 Savollar bo'lsa, yozing!", f"Batafsil ma'lumot: {phone}"]
        )
    else:  # love / fire
        options = (
            [
                f"Rahmat! ❤️ Mashina hali sotuvda — narx va batafsil: {phone}",
                f"Rahmat 🔥 Ko'rishga keling yoki qo'ng'iroq qiling: {phone}",
                "Rahmat! ❤️ Narx va ma'lumot uchun «narxi?» deb yozing.",
            ]
            if _on_sale(car)
            else ["Rahmat! ❤️", "Rahmat 🔥 Sahifamizni kuzatib boring!", "Rahmat! 🙌 Yangi mashinalar har kuni chiqadi."]
        )
    return with_ai_label(rnd.choice(options))
