"""Faqat emojidan iborat komment («❤️», «🔥🔥», «😂», «👎») — reaksiyani aniqlash va AI ishlamasa zaxira javob.

Asosiy javobni komment AI yozadi (mashina va uning bazadagi holatini ko'rib); bu yerdagi shablonlar mashina
sotuvda/sotilgani haqida hech narsa demaydi — taxmin xato bo'lmasin.

Instagram API postga bosilgan like/reaksiyalarni bermaydi; odamlar reaksiyani ko'pincha emoji-komment qilib
yozadi. Javoblar bir nechta variantdan tanlanadi (bir xil matn qayta-qayta chiqmasin — Instagram spam deb biladi).
"""

from __future__ import annotations

import random
import unicodedata

from bot.config import sales_phone_entries
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


def emoji_reply(text: str, *, rnd: random.Random | None = None) -> str:
    """AI ishlamaganda: mashina holati haqida HECH NARSA demaydigan (xato bo'lmasin) qisqa javob."""
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
    else:  # love / fire / wow
        options = [
            "Rahmat! ❤️",
            f"Rahmat 🔥 Savollar bo'lsa, yozing yoki qo'ng'iroq qiling: {phone}",
            "Rahmat! 🙌 Sahifamizni kuzatib boring.",
        ]
    return with_ai_label(rnd.choice(options))
