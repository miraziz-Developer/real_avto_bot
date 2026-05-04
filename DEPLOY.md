# Real Avto — serverga o‘rnatish (Docker)

Bu qo‘llanma **barcha stack**ni bir serverda ko‘tarish uchun: PostgreSQL, Redis, Node backend (CRM API), Nginx+React frontend, Python Telegram bot.

**Ishlab chiqarishda albatta**: ildiz `.env` da `ADMIN_TELEGRAM_IDS`, `SALES_PHONE`, haqiqiy `BOT_TOKEN` / kanallar; `backend/.env` da kuchli `JWT_SECRET`, `CRM_ADMIN_PASSWORD`, `NODE_ENV=production` va kerak bo‘lsa `CORS_ORIGIN`; Postgres `POSTGRES_PASSWORD` ni `docker-compose.yml` yoki override bilan almashtiring.

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
| Frontend | **3000** → konteyner 80 |
| Backend  | **3001** |
| Postgres / Redis | faqat ichki tarmoq |

Tashqi dunyoga faqat kerak bo‘lganini oching (masalan 3000 CRM uchun). 5432 ni internetga ochmang.

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
