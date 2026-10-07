# Real Avto — serverga o‘rnatish (Docker)

Bu qo‘llanma **barcha stack**ni bir serverda ko‘tarish uchun: PostgreSQL, Redis, Node backend (CRM API), Nginx+React frontend, Python Telegram bot.

**Ishlab chiqarishda albatta**: ildiz `.env` da `ADMIN_TELEGRAM_IDS`, `SALES_PHONE`, haqiqiy `BOT_TOKEN` / kanallar; `backend/.env` da kuchli `JWT_SECRET`, `CRM_ADMIN_PASSWORD`, `NODE_ENV=production` va kerak bo‘lsa `CORS_ORIGIN`; Postgres paroli ildiz `.env` dagi `POSTGRES_PASSWORD` dan olinadi (kodda saqlanmaydi) — u `DATABASE_URL` lardagi parol bilan bir xil bo‘lishi kerak.

## 1. Server talablari

- **OS**: Ubuntu 22.04/24.04 LTS yoki boshqa Linux (Docker qo‘llab-quvvatlanadi).
- **RAM**: kamida **2 GB** (4 GB tavsiya).
- **Disk**: **10 GB+** bo‘sh joy.
- **Tarmoq**: chiqish internet (Telegram **long polling** — ochiq kiruvchi port shart emas, faqat chiqish).
- **Domen** (ixtiyoriy): HTTPS orqali CRM ochish uchun Trafik / Cloudflare / Nginx tashqarisida proksi.

## 2. Docker o‘rnatish (Ubuntu qisqacha)

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
# Rasmiy Docker — https://docs.docker.com/engine/install/ubuntu/
sudo apt-get install -y docker.io docker-compose-plugin
sudo usermod -aG docker "$USER"
# yangi sessiyaga qayta kiring yoki newgrp docker
```

## 3. Loyihani yuklash

```bash
cd /opt   # yoki boshqa papka
sudo git clone <REPO_URL> real_avto_konkurs
sudo chown -R "$USER:$USER" real_avto_konkurs
cd real_avto_konkurs
```

## 4. Muhit fayllari

### 4.1 Ildiz `.env` (bot)

```bash
cp .env.example .env
nano .env
```

**Docker ichida** `DATABASE_URL` hosti **`db`** bo‘lishi kerak:

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/real_avto_konkurs
```

`REDIS_URL` ni odatda qo‘lda yozmasangiz ham bo‘ladi — `docker-compose.yml` botga `redis://redis:6379/0` beradi.

### 4.2 `backend/.env` (CRM API)

```bash
cp backend/.env.example backend/.env
nano backend/.env
```

**Docker** uchun `DATABASE_URL` (Node `pg` — `+asyncpg` yo‘q):

```env
DATABASE_URL=postgresql://postgres:postgres@db:5432/real_avto_konkurs
```

**Majburiy ishlab chiqarish**: `JWT_SECRET` ni uzun random bilan almashtiring (`openssl rand -hex 32`), `CRM_ADMIN_PASSWORD` ni kuchli qiling.

Backend uchun:

```env
NODE_ENV=production
# CRM ochilgan brauzer manzili (bir nechta bo‘lsa vergul bilan). Yo‘q qoldirish ogohlantirish beradi.
CORS_ORIGIN=https://crm.sizning-domen.uz
```

### 4.3 Frontend (Docker ichida odatda shart emas)

Nginx `/api/` ni `backend:3001` ga proksilaydi; brauzerda **nisbiy** `/api` ishlashi uchun `VITE_API_URL` bo‘sh qoldirish mumkin. Alohida API domen bo‘lsa `frontend/.env` da `VITE_API_URL=https://api.sizning-domen.uz/api` qilib qayta `docker compose build frontend`.

## 5. Ishga tushirish

```bash
chmod +x scripts/server_bootstrap.sh
bash scripts/server_bootstrap.sh
```

Yoki qo‘lda:

```bash
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f bot
```

**Toza qayta build** (muammo tuzatishda):

```bash
BOOTSTRAP_NO_CACHE=1 bash scripts/server_bootstrap.sh
```

## 6. Telegram va kanallar checklist

1. **@BotFather** — `BOT_TOKEN` ildiz `.env` da.
2. **Asosiy kanal** (`CHANNEL_ID`) — bot **admin** (post yozish, a’zolar soni / obuna tekshiruvi uchun kerak bo‘lgan huquqlar).
3. **LEADERBOARD_CHANNEL_ID** — TOP postlari shu yerga; odatda asosiy kanal bilan bir xil bo‘lishi mumkin.
4. **@real_avto_otzivlar** (sharhlar) — bot **admin**; kodda default shu kanal ishlatiladi, kerak bo‘lsa `REVIEWS_CHANNEL_ID` bilan almashtiring.
5. **ADMIN_TELEGRAM_IDS** — e’lon moderatsiyasi (vergul bilan bir nechta ID).

## 7. Portlar va firewall

Standart `docker-compose.yml`:

| Xizmat   | Host port |
|----------|-------------|
| Frontend (CRM) | **3000** → konteyner 80 (`/api/` backendga proksi) |
| Katalog | **3002** → konteyner 80 |
| Backend  | **127.0.0.1:3001** — faqat server ichidan (Caddy / nginx orqali) |
| Bot (Instagram webhook) | **127.0.0.1:8081** |
| Postgres / Redis | faqat ichki tarmoq |

Tashqi dunyoga faqat kerak bo‘lganini oching (443 — HTTPS). 5432 va 3001 ni internetga ochmang.

> Docker o‘z portlarini `ufw` qoidalaridan chetlab ochadi — shuning uchun backend `127.0.0.1` ga bog‘langan.

## 8. Yangilash (deploy)

```bash
cd /opt/real_avto_konkurs
git pull
docker compose build
docker compose up -d
```

**Muhim:** `docker compose up -d` **o‘zi yangi kodni tortmaydi** — avvalo image qayta **build** qilinadi (`COPY bot/` Dockerfile ichida). Faqat bot yangilansa ham:

```bash
docker compose build bot --no-cache
docker compose up -d bot
```

`--no-cache` ixtiyoriy, lekin ba’zan eski qatlamdan foydalangan konteynerni oldini oladi.

Migratsiyalar bot **birinchi marta** ishga tushganda PostgreSQLga qo‘llanadi (`bot/main.py` ichidagi `migrate` chaqiruvlari).

## 9. Tekshirish

- **CRM**: brauzerda `http://SERVER_IP:3000` — login `backend/.env` dagi `CRM_ADMIN_USER` / `CRM_ADMIN_PASSWORD`.
- **API**: `curl -s http://127.0.0.1:3001/health` serverda.
- **Bot**: `docker compose logs -f bot` — xatolarsiz polling, kanal tekshiruvi loglari.

## 10. Avtomatik testlar (CI yoki mahalliy)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest tests -q
```

Docker image buildni tekshirish:

```bash
docker compose config
docker compose build
```

## 11. Eslatmalar

- **Mahalliy** ishlab chiqishda bot `DATABASE_URL` da `localhost` ishlatadi; Dockerda **`db`** hostname `docker-compose` tarmog‘ida DNS bo‘ladi.
- **Webhook** ishlatilmaydi — bot process doimiy ishlashi kerak (systemd yoki Docker `restart: unless-stopped` allaqachon bor).

---

## 12. AI CRM yangilanishi (kanal kuzatuvchi, AI agent, katalog, Instagram)

Bir martalik qadamlar — eski versiyadan yangisiga o'tishda **tartib bilan**.

### 12.1 Zaxira nusxa (majburiy)
```bash
cd /opt/real_avto_bot
docker compose exec -T db pg_dump -U postgres real_avto_konkurs | gzip > ~/backup_before_ai_crm_$(date +%F).sql.gz
ls -lh ~/backup_before_ai_crm_*.sql.gz   # hajmi 0 emasligini tekshiring
```

### 12.2 DB parolini almashtirish
Eski parol `docker-compose.yml` ichida edi va git tarixida qolgan — **almashtirish shart**.
`POSTGRES_PASSWORD` faqat **yangi** bazani yaratishda ishlatiladi, mavjud bazada parolni qo'lda o'zgartiring:
```bash
NEW_PW="$(openssl rand -hex 24)"; echo "$NEW_PW"     # saqlab qo'ying
docker compose exec -T db psql -U postgres -c "ALTER USER postgres WITH PASSWORD '$NEW_PW';"
```
Keyin shu parolni yozing:
- ildiz `.env`: `POSTGRES_PASSWORD=...` va `DATABASE_URL=postgresql+asyncpg://postgres:...@db:5432/real_avto_konkurs`
- `backend/.env`: `DATABASE_URL=postgresql://postgres:...@db:5432/real_avto_konkurs`

### 12.3 Yangi `.env` sozlamalari (ildiz)
| O'zgaruvchi | Nima uchun |
|---|---|
| `GEMINI_API_KEY` (yoki `GROQ_API_KEY`) | AI: kanal postlari, ovoz/video tahlili, savdo agenti (bo'sh bo'lsa oddiy rejim) — 12.10 |
| `AI_DAILY_BUDGET_USD`, `AI_USER_DAILY_LIMIT` | AI xarajat chegarasi (standart $1/kun, 40 so'rov/mijoz) |
| `BUSINESS_NAME`, `BUSINESS_ADDRESS`, `BUSINESS_HOURS`, `REAL_AVTO_MAP_URL` | Agent va katalog javoblari |
| `BOT_USERNAME`, `CATALOG_URL` | Katalog (Mini App uchun HTTPS) |
| `LISTING_FREEZE_HOURS`, `WORK_HOUR_START/END`, `BUYOUT_REPLY_HOURS` | E'lon muzlatish va «Sotib olamiz» |
| `INSTAGRAM_ENABLED`, `IG_*` | Instagram (Meta ruxsatidan keyin) |
To'liq ro'yxat va izohlar: `.env.example`.

### 12.4 Yangilash
```bash
git fetch && git checkout main && git pull
docker compose build
docker compose up -d
docker compose logs -f bot backend catalog | head -100
```
Bot ishga tushganda yangi jadvallar (`cars`, `leads`, ...) va ustunlar avtomatik yaratiladi — qayta ishga tushirish xavfsiz.

### 12.5 HTTPS (katalog Mini App va Instagram webhook uchun)
Masalan Caddy (sertifikat avtomatik):
```
katalog.realavto.uz {
    reverse_proxy 127.0.0.1:3002
}
crm.realavto.uz {
    reverse_proxy 127.0.0.1:3000
}
hook.realavto.uz {
    reverse_proxy /webhooks/instagram 127.0.0.1:8081
}
```
CRM'ni tashqi internetga ochmaslik ham mumkin (faqat VPN / IP cheklovi) — u ichki panel.

### 12.6 Telegram sozlamalari
- [ ] Bot asosiy kanalda **admin** (postlarni o'qish va joylash)
- [ ] Bot kanalning **muhokama guruhida admin** (kommentlarga javob)
- [ ] `ADMIN_TELEGRAM_IDS` — lead va mashina kartalarini oladigan jamoa
- [ ] (ixtiyoriy) Telegram Business: egasining akkauntida *Chatbotlar* → bot, **kontaktlarni chiqarib tashlang**

### 12.7 Kanal tarixini import qilish
Yangilangan bot **birinchi ishga tushganda** avvaldan tasdiqlangan (sotuvdagi) bot e'lonlarini mashinalar bazasiga
o'zi ko'chiradi — AI agent ularni darhol taklif qila oladi (logda: «Eski tasdiqlangan e'lonlardan N ta mashina
bazaga qo'shildi»). Kanal tarixini importni **shundan keyin** qiling — bot e'lonlari ikki marta tushmaydi.

Telegram Desktop → kanal → Export chat history (JSON, rasmlarsiz) → serverga `/opt/real_avto_bot/import/result.json`:
```bash
docker compose cp import/result.json bot:/app/result.json
docker compose exec bot python -m scripts.import_channel_export /app/result.json --dry-run
docker compose exec bot python -m scripts.import_channel_export /app/result.json --ai
```

### 12.8 Tekshiruv (smoke test)
- [ ] Kanalga test post tashlang → adminlarga «🆕 Kanalda yangi mashina» kartasi keldi
- [ ] Postni tahrirlab «SOTILDI» yozing → «🔴 sotildi» xabari
- [ ] Botga boshqa akkauntdan «Cobalt bormi?» → bazadan javob; «menejer bilan gaplashmoqchiman» → lead kartasi
- [ ] Lead kartasiga reply → javob mijozga bordi
- [ ] Botda test e'lon → kartada «⏳ … da avtomatik chiqadi» va «💰 Sotib olamiz»
- [ ] `/statistika`, CRM «Mashinalar» va «Mijozlar (AI)» sahifalari ochiladi
- [ ] Katalog: `https://katalog…` ochiladi, rasmlar ko'rinadi, botda «🚗 Katalog» tugmasi bor

### 12.9 Orqaga qaytarish
```bash
git checkout <oldingi-commit> && docker compose build && docker compose up -d
# Ma'lumotni qaytarish kerak bo'lsa:
gunzip -c ~/backup_before_ai_crm_YYYY-MM-DD.sql.gz | docker compose exec -T db psql -U postgres real_avto_konkurs
```
Yangi jadvallar eski kodga xalaqit bermaydi — faqat kodni qaytarish yetarli bo'lishi mumkin.

### 12.10 Gemini'ga o'tish (video + ovoz + o'zbek tili)

1. Kalit oling: <https://aistudio.google.com/apikey> → loyihada **billing** yoqing (bepul tarif limiti prod uchun kichik).
2. Ildiz `.env`:
   ```env
   AI_PROVIDER=auto          # GEMINI_API_KEY bo'lsa Gemini tanlanadi
   GEMINI_API_KEY=...
   AI_DAILY_BUDGET_USD=1     # kunlik chegara, oshsa AI ertangacha o'chadi va adminlarga xabar keladi
   ```
   `GROQ_API_KEY` ni qoldirish mumkin — `AI_PROVIDER=groq` qilib bir zumda qaytsa bo'ladi.
3. `docker compose up -d --build bot` → logda `AI: gemini (model=gemini-3.1-flash-lite, yoqilgan=True ...)` chiqadi.
4. **Sinov:** kanalga gapirilgan dumaloq video va ovozsiz (faqat mashina ko'rsatilgan) video tashlang — admin kartasida
   «🎙 Eshitilgani» ichida nutq va `[Videoda ko'rinadi]: ...` chiqishi kerak. Faqat ovoz/videodan olingan faktlar
   avvalgidek **tekshiruv** holatida qoladi (admin tasdiqlaydi).
5. 20–30 ta haqiqiy namuna bilan aniqlikni tekshiring. Yetmasa: `GEMINI_MODEL=gemini-3.7-flash` (~3x qimmat) yoki
   `GEMINI_MEDIA_RESOLUTION=medium`.

**Cheklov:** Telegram bot 20 MB dan katta faylni yuklab bera olmaydi — bunday video AI'siz qayta ishlanadi
(kanalda saqlanadi, mijozga yuboriladi, faqat tahlil qilinmaydi). Dumaloq video va ovozli xabarlar doim kichik.

**Taxminiy narx (Flash-Lite):** matn ~$0.0008, 30 s ovoz ~$0.001, 40 s dumaloq video ~$0.003 — oyiga bir necha ming
xabar bilan **$5–7**. Bugungi xarajat logda va chegara tugaganda adminga keladigan xabarda ko'rinadi.

### 12.11 Xavfsizlik yangilanishi (shu versiyada)

- **Backend porti** endi faqat `127.0.0.1:3001`. Brauzerda `http://IP:3001/...` ishlamaydi — CRM: `http://IP:3000`
  yoki HTTPS domen. Host'dagi Caddy `localhost:3001` ga proksi qilsa — o'zgarish shart emas.
- **`backend/.env`:** `NODE_ENV=production` bo'lsa `JWT_SECRET` kamida 32 belgi va `CRM_ADMIN_PASSWORD`
  `admin123` bo'lmasligi shart — aks holda backend ishga tushmaydi (`docker compose logs backend` → `[FATAL]`).
- **CRM login:** 15 daqiqada IP+login bo'yicha 8 ta xato urinishdan keyin vaqtincha bloklanadi (429).
- **Redis** FSM ma'lumotini diskka yozadi (`redis_data` volume) va xotira to'lsa o'chirmaydi — restartda
  foydalanuvchilarning yarim to'ldirilgan formalari yo'qolmaydi.
- **Bot healthcheck:** `docker compose ps` da bot `healthy` — Telegram API ga har 30 soniyada ulanish tekshiriladi.
- **Yangi admin buyruqlari:** `/navbat` — moderatsiya kutayotgan foydalanuvchi e'lonlarini tugmalar bilan qayta
  yuboradi; `/umumiy` — foydalanuvchi/e'lon/sotuv statistikasi.
