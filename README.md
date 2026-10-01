# Real Avto konkurs

Telegram bot (e’lon, wishlist, konkurs, anonim savol), CRM (Node + React), PostgreSQL, Redis.

- **Serverga o‘rnatish (Docker, qadam-baqadam)**: [DEPLOY.md](DEPLOY.md)
- **Bot `.env` namunasi**: [.env.example](.env.example)
- **Backend `.env`**: [backend/.env.example](backend/.env.example)
- **Ishga tushirish (skript)**: `bash scripts/server_bootstrap.sh`

Mahalliy testlar: `pip install -r requirements-dev.txt && pytest tests -q`

---

## Mashinalar bazasi va kanal kuzatuvchi (AI CRM, 1-bosqich)

Kanalga tashlangan har bir e'lon avtomatik **mashinalar bazasiga** (`cars`) yoziladi — savdo agenti va statistika shu bazadan ishlaydi.

**Qanday ishlaydi**
- Bot asosiy kanalda (`CHANNEL_ID`) **admin** bo'lishi kerak. Yangi post (matn, albom, dumaloq video) → marka, model, yil, probeg, narx ajratiladi → adminlarga karta boradi.
- Ma'lumot to'liq bo'lsa — 🟢 *Sotuvda*; yetishmasa — 🟡 *Tekshiruv* (kartadagi «✏️ Tuzatish» orqali to'ldiriladi).
- **Sotildi** deb belgilash (istalgani): kanal postini tahrirlab «SOTILDI» yozish · postga «sotildi» deb reply · kartadagi «💰 Sotildi» · `/sotildi ID [narx]` · CRM.
- Bot orqali berilgan va tasdiqlangan e'lonlar ham bazaga tushadi; egasi «sotildi» desa — bazada ham sotildi.
- `CAR_STALE_DAYS` (14) kundan beri sotuvda turgan mashina uchun adminlarga «hali sotuvdami?» so'rovi (ish vaqtida).

**AI (ixtiyoriy)** — `.env` ga `GROQ_API_KEY` qo'shilsa: aniqroq tahlil, qisqa xulosa, dumaloq video/ovozni matnga aylantirish. Kalitsiz ham oddiy (regex) tahlil ishlaydi.

**Admin buyruqlari (botda)**: `/statistika [kun]` · `/sotuvda` · `/tekshiruv` · `/mashina ID` · `/sotildi ID [narx]`
Kartada: ✏️ Tuzatish — `narx 9800`, `yil 2021`, `probeg 76000`, o'zimiz olgan bo'lsak `xarid 8000`, `xarajat 300`.

**CRM** → «Mashinalar» bo'limi: sotuvdagilar, jami qiymat, o'rtacha sotilish muddati, turib qolganlar, foyda; har bir mashina tarixi (narx/holat o'zgarishlari).

**Eski postlarni import qilish** (bot qo'shilishidan oldingi kanal tarixi):
```bash
# Telegram Desktop → kanal → ⋮ → Export chat history → JSON
python -m scripts.import_channel_export path/to/result.json --dry-run   # avval ko'rib chiqish
python -m scripts.import_channel_export path/to/result.json             # bazaga yozish (--ai — Groq bilan)
```
`--active-days` (30) dan eski, sotilganligi noma'lum postlar arxivga tushadi (agent eski mashinani taklif qilmasligi uchun).

**E'lon to'lovi**: hozircha bepul (`LISTING_PAYMENT_ENABLED=false`). Pullik rejim: `true`.

**DB testlari** (haqiqiy Postgres bilan):
```bash
docker run -d --rm --name realavto-testdb -e POSTGRES_PASSWORD=test -e POSTGRES_DB=realavto_test -p 55432:5432 postgres:16-alpine
TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:55432/realavto_test pytest tests -q
```

---

## AI savdo agenti (2-bosqich)

Botga yozilgan har qanday savolga (matn yoki ovoz) agent **faqat mashinalar bazasidan** javob beradi, mijozni «pishiradi» va tayyor bo'lganda menejerga topshiradi.

**Mijoz tomoni**
- «Cobalt bormi?», «10 000$ gacha avtomat», ovozli xabar — agent sotuvdagi mashinalarni topadi (sotilganini hech qachon taklif qilmaydi), rasmlarini yuboradi, savollarga javob beradi.
- Mos mashina yo'q bo'lsa — o'xshashlarini taklif qiladi yoki «chiqsa xabar beraman» (qidiruv saqlaydi). Kanalga mos mashina tushishi bilan mijozga xabar boradi.
- Mijoz tayyor bo'lsa (ko'rishga kelmoqchi, «olaman», narx/kredit so'rayapti, odam bilan gaplashmoqchi) — menejerga topshiriladi.
- `GROQ_API_KEY` bo'lmasa ham ishlaydi: matndan model/byudjetni ajratib, bazadan ro'yxat + «Menejer bilan bog'lanish» tugmasi.
- Chegirma, kredit, hujjat bo'yicha va'da bermaydi; ma'lumot yo'q bo'lsa o'ylab topmaydi.

**Menejer tomoni (botda)**
- 🔥 **Lead kartasi**: ism, telefon, qiziqqan mashina, byudjet, to'lov usuli, kelish vaqti, AI xulosasi, oxirgi xabarlar.
- **Kartaga reply qilsangiz — javob mijozga boradi** (AI shu zahoti jim turadi). Mijoz javoblari ham sizga keladi.
- Tugmalar: ✅ Oldim · 🤖 AI davom etsin · 🏁 Sotuv bo'ldi · ❌ Yopish · 💬 To'liq suhbat.
- `LEAD_REMINDER_MINUTES` (5) ichida hech kim olmasa — barcha adminlarga qayta eslatma.
- Buyruqlar: `/leadlar` (ochiq mijozlar) · `/lead ID`.

**CRM** → «Mijozlar (AI)»: ochiq mijozlar, menejer kutayotganlar, konversiya, topshirishgacha vaqt, har bir suhbat to'liq.

**Sozlamalar** (`.env`): `AGENT_ENABLED`, `GROQ_API_KEY`, `GROQ_AGENT_MODEL`, `BUSINESS_NAME`, `BUSINESS_ADDRESS`, `BUSINESS_HOURS` (bo'sh bo'lsa agent ish vaqtini aytmaydi), `REAL_AVTO_MAP_URL`, `LEAD_REMINDER_MINUTES`.

**Kanaldan botga yo'naltirish**: istalgan mashina uchun havola `https://t.me/<bot>?start=car_<ID>` — mijoz shu mashina rasmlari va ma'lumoti bilan suhbatni boshlaydi.

### Telegram Business (Ikrom akaning shaxsiy akkaunti) va kanal kommentlari

**Business** — mijoz shaxsiy akkauntga yozsa ham agent javob beradi (akkaunt nomidan):
1. Akkaunt egasi (Telegram **Premium** kerak) → *Sozlamalar → Telegram Business → Chatbotlar* → bot username'ini kiritadi.
2. «Xabarlarga javob berish» ruxsatini yoqadi.
3. ⚠️ **«Chatlar» bo'limida kontaktlarni chiqarib tashlang** (yoki faqat «yangi chatlar»ni tanlang) — aks holda AI oila va do'stlarga ham javob beradi.
4. Ulanganda adminlarga «✅ Telegram Business ulandi» xabari keladi.

Egasi mijozga **o'zi yozsa** — AI shu mijoz bilan `BUSINESS_OWNER_PAUSE_HOURS` (6) soat jim turadi. Business chatda tugmalar yuborilmaydi (Telegram cheklovi); menejer javobi ham akkaunt nomidan ketadi.

**Kommentlar** — kanal postiga «narxi qancha?», «bormi?», «kredit bormi?» kabi savol yozilsa, bot bazadan qisqa javob beradi (sotuvda/bron/sotilgan, narx, probeg) va «🤖 Botda batafsil» tugmasini qo'yadi. Ochiq joyda AI ishlatilmaydi — faqat bazadagi faktlar. Xarid niyati bo'lsa («olaman», «kredit», «raqam») — adminlarga signal.
- Talab: bot kanalga ulangan **muhokama guruhida admin** bo'lishi kerak.
- Bir mijozga bitta post bo'yicha 10 daqiqada bir marta javob (guruh to'lib ketmasligi uchun). O'chirish: `COMMENTS_ENABLED=false`.
