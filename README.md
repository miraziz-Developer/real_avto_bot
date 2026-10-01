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
