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

**AI (ixtiyoriy)** — `.env` ga `GEMINI_API_KEY` (tavsiya) yoki `GROQ_API_KEY` qo'shilsa: aniqroq tahlil, qisqa xulosa, ovoz va dumaloq videoni tushunish. Gemini videoni **ko'radi** ham (marka, rang, kuzov, spidometr) — Groq faqat ovozni matnga aylantiradi. Kunlik xarajat chegarasi: `AI_DAILY_BUDGET_USD` (standart $1). Kalitsiz ham oddiy (regex) tahlil ishlaydi. Batafsil: [DEPLOY.md 12.10](DEPLOY.md#1210-geminiga-otish-video--ovoz--ozbek-tili).

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
- AI kaliti (`GEMINI_API_KEY` / `GROQ_API_KEY`) bo'lmasa yoki kunlik chegara tugasa ham ishlaydi: matndan model/byudjetni ajratib, bazadan ro'yxat + «Menejer bilan bog'lanish» tugmasi.
- Chegirma, kredit, hujjat bo'yicha va'da bermaydi; ma'lumot yo'q bo'lsa o'ylab topmaydi.

**Menejer tomoni (botda)**
- 🔥 **Lead kartasi**: ism, telefon, qiziqqan mashina, byudjet, to'lov usuli, kelish vaqti, AI xulosasi, oxirgi xabarlar.
- **Kartaga reply qilsangiz — javob mijozga boradi** (AI shu zahoti jim turadi). Mijoz javoblari ham sizga keladi.
- Tugmalar: ✅ Oldim · 🤖 AI davom etsin · 🏁 Sotuv bo'ldi · ❌ Yopish · 💬 To'liq suhbat.
- `LEAD_REMINDER_MINUTES` (5) ichida hech kim olmasa — barcha adminlarga qayta eslatma.
- Buyruqlar: `/leadlar` (ochiq mijozlar) · `/lead ID`.

**CRM** → «Mijozlar (AI)»: ochiq mijozlar, menejer kutayotganlar, konversiya, topshirishgacha vaqt, har bir suhbat to'liq.

**Sozlamalar** (`.env`): `AGENT_ENABLED`, `GEMINI_API_KEY` / `GROQ_API_KEY`, `GEMINI_AGENT_MODEL` / `GROQ_AGENT_MODEL`, `AI_USER_DAILY_LIMIT`, `BUSINESS_NAME`, `BUSINESS_ADDRESS`, `BUSINESS_HOURS` (bo'sh bo'lsa agent ish vaqtini aytmaydi), `REAL_AVTO_MAP_URL`, `LEAD_REMINDER_MINUTES`.

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

---

## E'lon muzlatish va «💰 Sotib olamiz» (3-bosqich)

Bot orqali kelgan e'lon darhol kanalga chiqmaydi — `LISTING_FREEZE_HOURS` (6) **ish soati** jamoada turadi. Shu vaqtda yaxshi mashinani o'zimiz sotib olishimiz mumkin.

- Ish vaqti `WORK_HOUR_START`–`WORK_HOUR_END` (9–21, Toshkent). Kechqurun 20:00 da kelgan e'lon ertasi 14:00 da chiqadi — tunda vaqt «yonib» ketmaydi, kanalga ham tunda e'lon chiqmaydi.
- Admin kartasida: ✅ Tasdiqlash · ❌ Rad etish · **💰 Sotib olamiz** + «⏳ 12.10 14:00 da avtomatik chiqadi».
- **💰 Sotib olamiz** → narx yoziladi (`8500` yoki `110 mln`) → sotuvchiga taklif: ✅ Roziman · 💬 Muhokama · 📢 Yo'q, e'lon qilinsin.
  - Rozi / muhokama → adminlarga sotuvchi telefoni va «🏁 Sotib oldik» / «📢 Bekor — e'lon qilish».
  - Rad → e'lon darhol kanalga.
  - `BUYOUT_REPLY_HOURS` (24) ichida javob yo'q → e'lon avtomatik kanalga.
- **🏁 Sotib oldik** → mashina bazaga «bizniki» bo'lib (xarid narxi bilan) tushadi. Ta'mir xarajati va sotuv narxini kartadagi «✏️ Tuzatish» orqali kiriting, tayyor bo'lgach **📢 Kanalga joylash** — bot o'zi Real Avto shablonida joylaydi, agent darhol taklif qila boshlaydi, foyda CRM statistikasida.
- Hech kim hech narsa qilmasa — muddat tugagach e'lon o'zi kanalga chiqadi, adminlarga xabar boradi.
- CRM «E'lonlar» bo'limida muzlatish vaqti va taklif holati ko'rinadi. O'chirish: `LISTING_FREEZE_HOURS=0`.

---

## Mashinalar katalogi — sayt va Telegram Mini App (4-bosqich)

`catalog/` — ro'yxatdan o'tishsiz, telefonga mo'ljallangan katalog: faqat **sotuvdagi** mashinalar (bazadan avtomatik).

- Qidiruv va filtrlar (marka, byudjet, yil, avtomat/mexanika, saralash), rasmlar galereyasi.
- **«Real narx»**: bazadagi o'xshash mashinalar (model, yil ±1, oxirgi 12 oy) medianasi bilan solishtirib «Bozordan ~10% arzon» / «Bozor narxida» belgisi (kamida 3 ta o'xshash bo'lsa; qimmat bo'lsa ko'rsatilmaydi).
- Har mashinada: «🤖 Savol berish» (botda shu mashina bilan agent suhbati), qo'ng'iroq, xarita, o'xshash mashinalar.
- «💰 Mashina sotaman» → botda e'lon berish; mos mashina bo'lmasa «🔔 Chiqsa xabar ber» (Mini App ichida bir bosishda, Telegram imzosi tekshiriladi).
- Hech qachon ko'rsatilmaydi: sotuvchi telefoni, xarid narxi, foyda, ichki izohlar. Ochiq API: `/api/public/*` (IP bo'yicha cheklov), CRM endpointlari katalog orqali ochilmaydi.

**Ishga tushirish**: `docker compose up -d catalog` → `http://SERVER:3002`. Domen + HTTPS (masalan nginx/Caddy orqali `https://katalog.realavto.uz`) ulab, ildiz `.env` ga:
```
CATALOG_URL=https://katalog.realavto.uz
BOT_USERNAME=real_avto_bot
```
Bot qayta ishga tushgach chat pastida **«🚗 Katalog»** tugmasi va bosh menyuda «Sotuvdagi mashinalar» paydo bo'ladi (Mini App faqat HTTPS bilan ishlaydi).

## CI
`.github/workflows/ci.yml`: har PR'da bot testlari (PostgreSQL bilan), backend testlari, CRM va katalog build.

---

## Instagram — Direct va kommentlar (5-bosqich)

Instagram'ga yozilgan savollarga ham **o'sha agent** javob beradi (bazadan), tayyor mijoz Telegram'dagi adminlarga lead kartasi bo'lib keladi.

- **Direct**: savolga javob; menejer lead kartasiga reply qilsa — javob Instagram Direct'ga ketadi. Akkaunt egasi Instagram ilovasidan o'zi yozsa — AI shu mijoz bilan 6 soat jim.
- **Kommentlar**: savol bo'lsa ochiq qisqa javob («Javobni Direct'ga yubordik 📩») + batafsil javob Direct'ga (private reply). «Zo'r 🔥» kabi kommentlarga javob yo'q; xarid niyati — adminlarga signal.
- Rasmlar Direct'ga katalogning ochiq rasm manzili orqali yuboriladi (`CATALOG_URL` HTTPS bo'lishi kerak).

**Ulash** (Meta App Review tasdiqlagach):
1. Meta Developers → ilova → *Instagram API with Instagram Login*: `instagram_business_basic`, `instagram_business_manage_messages`, `instagram_business_manage_comments`.
2. Webhook: Callback URL `https://DOMEN/webhooks/instagram` (reverse-proxy → bot konteyneri `127.0.0.1:8081`), Verify token = `IG_VERIFY_TOKEN`, obunalar: `messages`, `comments`.
3. Ildiz `.env`: `INSTAGRAM_ENABLED=true`, `IG_ACCESS_TOKEN`, `IG_APP_SECRET`, `IG_VERIFY_TOKEN`, `IG_ACCOUNT_ID`.
4. Har webhook so'rovi `X-Hub-Signature-256` bilan tekshiriladi — imzosiz so'rovlar rad etiladi.

Development rejimida (App Review'dan oldin) faqat ilovaga tester qilib qo'shilgan akkauntlar bilan ishlaydi.
