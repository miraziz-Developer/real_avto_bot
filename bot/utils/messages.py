ROOT_WELCOME = (
    "🚘 <b>Real Avto botiga xush kelibsiz!</b>\n\n"
    "📢 <b>Elon berish</b> — mashinangizni e‘lon qilish (moderatsiya, keyin kanal).\n"
    "🔍 <b>Qidiruv saqlash</b> — parametrlar bo‘yicha mos e‘lon chiqqanda avtomatik xabar."
)

WELCOME = (
    "👋 <b>Assalomu alaykum!</b>\n\n"
    "Bu yerda <b>do‘stlaringizni taklif qilish</b> orqali real mukofotga "
    "erishasiz. Har bir ishtirokchining <b>shaxsiy havolasi</b> bo‘ladi.\n\n"
    "⬇️ Avval majburiy kanalimizga obuna bo‘ling — keyin Instagram bosqichiga o‘tamiz."
)

AFTER_CHANNEL = (
    "✅ <b>Telegram kanali tasdiqlandi.</b>\n\n"
    "Endi musobaqada qatnashish uchun <b>Instagram sahifamizni</b> oching va "
    "tanishlaringizga ham ulashing — bu qoidalarning ruhiy qismi.\n\n"
    "Pastdagi tugmalar bilan davom eting."
)

MAIN_READY = (
    "🎯 <b>Siz tayyorsiz!</b>\n\n"
    "Endi shaxsiy havolangizni tarqating — kim sizning havolangizdan kirsa, "
    "kanal va Instagram qadamlarini tugatsa, <b>sizning hisobingizga +1</b> qo‘shiladi.\n\n"
    "Eng ko‘p taklif qilganlar — <b>TOP-15</b> va asosiy mukofot uchun kurashadi.\n\n"
    "<i>TOPda aniqroq chiqish uchun «TOP uchun taxallus»ni ham to‘ldiring.</i>"
)

ABOUT = (
    "🏆 <b>Musobaqa qoidalari (qisqa)</b>\n\n"
    "1️⃣ Shaxsiy havolangizni do‘stlaringizga yuboring.\n"
    "2️⃣ Ular botga kirib, kanal va Instagram bosqichlarini tugatsa — "
    "sizga <b>1 ta referal</b> yoziladi.\n"
    "3️⃣ Eng ko‘p referal to‘plaganlar orasida <b>liderlar</b> aniqlanadi.\n"
    "4️⃣ Har {days} kunda <b>TOP-15</b> ro‘yxati kanalimizda e’lon qilinadi.\n"
    "5️⃣ TOP postlarida har kishining <b>Telegram ID</b> va (bo‘lsa) "
    "<b>@username</b> havolasi chiqadi — shaxsiy chat emas, "
    "kim ekanini aniq solishtirish uchun.\n\n"
    "💰 Asosiy yo‘nalish: eng yuqori natija uchun <b>${prize}</b> gacha mukofot "
    "(tashkilotchining e’loniga muvofiq).\n\n"
    "<i>Firibgarlik va soxta akkauntlar rad etiladi.</i>"
)

MUST_JOIN = (
    "⛔️ Avval kanalga obuna bo‘ling, keyin <b>«Obunani tekshirish»</b> tugmasini bosing."
)

NOT_SUBSCRIBED_YET = (
    "❌ Kanal a’zoligi topilmadi.\n"
    "Iltimos, kanalga qo‘shiling va yana tekshiring."
)

INVITE_HEADER = (
    "👥 <b>Do‘stlarni taklif qiling</b>\n\n"
    "Quyidagi havolani nusxalab, do‘stlaringizga yuboring:\n\n"
    "<code>{link}</code>\n\n"
    "<i>Havola shaxsiy — faqat sizga tegishli.</i>"
)

STATS = (
    "📊 <b>Sizning statistikangiz</b>\n\n"
    "🔗 Referal kodingiz: <code>{code}</code>\n"
    "👥 Jami qo‘shilgan do‘stlar: <b>{count}</b> ta\n\n"
    "{alias_block}\n"
    "🆔 Telegram ID: <code>{tg_id}</code>\n"
    "<i>(kanal TOP ro‘yxatida shu ID chiqadi — ism o‘xshash bo‘lsa ham farq qiladi)</i>\n\n"
    "Reytingda yuqoriga chiqish uchun havolangizni faol tarqating."
)

ALIAS_PROMPT = (
    "✏️ <b>TOP uchun taxallus</b>\n\n"
    "Kanalda e’lon qilinadigan <b>TOP-15</b> ro‘yxatida chiqadigan "
    "taxallusingizni yozib yuboring.\n\n"
    "• 3–32 belgi\n"
    "• Harf, raqam, probel, tire, pastki chiziq\n"
    "• @username yoki havola <b>bo‘lmasin</b> (agar @ bo‘lsa, alohida chiqadi)\n\n"
    "<i>Bu boshqa foydalanuvchilarning sizni aniq tanishi uchun.</i>"
)

ALIAS_SAVED = "✅ TOP taxallusi saqlandi: <b>{alias}</b>"

ALIAS_CANCELLED = "Bekor qilindi. Bosh menyudan davom eting."

REVERIFY_CHANNEL = (
    "⚠️ Kanal a’zoligi aniqlanmadi. Iltimos, qayta obuna bo‘ling va "
    "«Obunani tekshirish»dan foydalaning."
)

CONTEST_HUB_HEADER = (
    "🏆 <b>Konkurs markazi</b>\n"
    "<i>Rasmiy konkurs (omadan g‘olib) + referal reyting — bir joyda.</i>"
)

CONTEST_HUB_NO_ACTIVE = (
    "\n\nHozircha <b>faol rasmiy konkurs</b> (ommadan g‘olib) e’lon qilinmagan.\n"
    "Referallar bo‘yicha <b>TOP</b> va mukofotlar esa har doim ishlaydi — "
    "pastdagi tugmalar orqali havola va statistikangizni oching."
)

CONTEST_HUB_ACTIVE = (
    "\n\n<b>{title}</b>\n"
    "🎁 Mukofot: <b>{prize}</b>\n"
    "📅 Tugash: <code>{ends}</code>\n\n"
    "{status_block}\n\n"
    "<b>Referal (reyting uchun)</b>\n"
    "Havola: <code>{link}</code>\n"
    "Jami tasdiqlangan referallar: <b>{refs}</b> ta\n\n"
    "<i>Rasmiy konkursda ishtirok alohida — u yerda ro‘yxatdan o‘tganlar orasidan "
    "g‘olib tanlanadi (CRM). TOP esa eng ko‘p referal to‘plaganlar uchun.</i>"
)

CONTEST_JOIN_OK = "✅ Siz rasmiy konkurs ishtirokchilari ro‘yxatiga qo‘shildingiz."
CONTEST_JOIN_ALREADY = "Siz allaqachon ushbu konkurs uchun ro‘yxatga olgansiz."
CONTEST_JOIN_NONE = "Hozircha aktiv konkurs yo‘q — keyinroq urinib ko‘ring."
CONTEST_JOIN_ENDED = "Bu konkurs muddati tugagan. Yangi bosqich e’lon qilinadi."
CONTEST_JOIN_NEED_SETUP = "Avval kanal va Instagram bosqichlarini tugating."

WISHLIST_LISTING_FOOTER = (
    "⚡️ <b>Tezkor savdo va maslahat</b>\n\n"
    "{phone_links}\n\n"
    "<i>Mashina haqida savolingiz bo‘lsa — tugma orqali yozing; "
    "sotib olish yoki ko‘rish uchun raqamlarga chiqishingiz mumkin.</i>"
)
