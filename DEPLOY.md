# Deployment guide (Docker)

This guide brings up the whole stack on a single server:

- PostgreSQL and Redis;
- the Node.js CRM API;
- the CRM web app and the catalog (both served by nginx);
- the Python Telegram bot.

**Production checklist:**

- Root `.env`:
  - a real `BOT_TOKEN`, the channels, `ADMIN_TELEGRAM_IDS` and `SALES_PHONE`;
  - `POSTGRES_PASSWORD`, which must match the password inside every `DATABASE_URL`. It is never stored in the repository.
- `backend/.env`:
  - a strong `JWT_SECRET` and `CRM_ADMIN_PASSWORD`;
  - `NODE_ENV=production`;
  - `CORS_ORIGIN` if the CRM is served from its own domain.

## 1. Server requirements

| | Minimum |
|---|---|
| OS | Ubuntu 22.04 / 24.04 LTS or another Linux distribution supported by Docker |
| RAM | 2 GB (4 GB recommended) |
| Disk | 10 GB free |
| Network | Outbound internet. The bot uses long polling, so no inbound port is needed for Telegram |
| Domain (optional) | Needed for HTTPS: the catalog Mini App and the Instagram webhook require it |

## 2. Install Docker (Ubuntu)

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
# Official instructions: https://docs.docker.com/engine/install/ubuntu/
sudo apt-get install -y docker.io docker-compose-plugin
sudo usermod -aG docker "$USER"   # then log in again (or run: newgrp docker)
```

## 3. Get the code

```bash
cd /opt
sudo git clone <REPO_URL> real_avto_bot
sudo chown -R "$USER:$USER" real_avto_bot
cd real_avto_bot
```

## 4. Configuration

### 4.1 Root `.env` (bot)

```bash
cp .env.example .env
nano .env
```

Inside Docker the database host is **`db`**:

```env
DATABASE_URL=postgresql+asyncpg://postgres:<POSTGRES_PASSWORD>@db:5432/real_avto_konkurs
```

`REDIS_URL` can be left empty, because `docker-compose.yml` passes `redis://redis:6379/0` to the bot.

### 4.2 `backend/.env` (CRM API)

```bash
cp backend/.env.example backend/.env
nano backend/.env
```

The Node `pg` driver uses a plain URL (no `+asyncpg`):

```env
DATABASE_URL=postgresql://postgres:<POSTGRES_PASSWORD>@db:5432/real_avto_konkurs
NODE_ENV=production
JWT_SECRET=<output of: openssl rand -hex 32>
CRM_ADMIN_PASSWORD=<strong password>
# Browser origin(s) of the CRM, comma-separated
CORS_ORIGIN=https://crm.example.com
```

With `NODE_ENV=production`, the backend **refuses to start** in either of these cases:

- `JWT_SECRET` is shorter than 32 characters;
- `CRM_ADMIN_PASSWORD` is still the default.

### 4.3 Frontend

Usually nothing to configure. nginx proxies `/api/` to `backend:3001`, so the CRM calls the API through the relative `/api` path.

For a separate API domain:

1. Set `VITE_API_URL=https://api.example.com/api` in `frontend/.env`.
2. Rebuild: `docker compose build frontend`.

## 5. Start

```bash
bash scripts/server_bootstrap.sh
```

Or manually:

```bash
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f bot
```

Clean rebuild (when troubleshooting): `BOOTSTRAP_NO_CACHE=1 bash scripts/server_bootstrap.sh`.

## 6. Telegram checklist

- [ ] **@BotFather**: the token is set as `BOT_TOKEN`. Under *Bot Settings → Menu Button*, remove any old Mini App URL. The bot manages the menu button itself from `CATALOG_URL` and `CRM_URL`.
- [ ] **Main channel** (`CHANNEL_ID`): the bot is an **admin**, so it can read and publish posts.
- [ ] **Discussion group** of the channel: the bot is an **admin**, so it can answer comments.
- [ ] **Leaderboard channel** (`LEADERBOARD_CHANNEL_ID`): defaults to the main channel.
- [ ] **Reviews channel** (`REVIEWS_CHANNEL_ID`): the bot is an admin.
- [ ] **`ADMIN_TELEGRAM_IDS`**: the team that receives moderation, car and lead cards. Each admin must have pressed /start in the bot.

## 7. Ports and firewall

| Service | Host port |
|---|---|
| CRM web app | **3000** → container 80 (proxies `/api/` to the backend) |
| Catalog | **3002** → container 80 |
| CRM API | **127.0.0.1:3001** (local only; expose through a reverse proxy) |
| Bot (Instagram webhook) | **127.0.0.1:8081** |
| PostgreSQL / Redis | internal Docker network only |

Expose only what is needed (443 for HTTPS). Never expose 5432 or 3001 to the internet.

> Docker publishes ports bypassing `ufw` rules. This is why internal services are bound to `127.0.0.1`.

## 8. Updating

```bash
cd /opt/real_avto_bot
git pull
docker compose build
docker compose up -d
```

`docker compose up -d` alone does **not** pick up new code: images must be rebuilt first. To update only the bot:

```bash
docker compose build bot
docker compose up -d bot
```

Database migrations run automatically when the bot starts and are idempotent, so restarting is safe.

## 9. Verification

- **CRM:** `http://SERVER_IP:3000`. Log in with `CRM_ADMIN_USER` / `CRM_ADMIN_PASSWORD` from `backend/.env`.
- **API:** `curl -s http://127.0.0.1:3001/health` (run on the server).
- **Bot:** `docker compose logs -f bot` shows `Run polling` without errors. `docker compose ps` reports the bot as `healthy`, because a heartbeat is checked every 30 seconds.

## 10. Automated tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest tests -q
docker compose config && docker compose build
```

See [README.md](README.md#tests) for running the database tests against PostgreSQL.

## 11. Notes

- **Database host:** in local development, outside Docker, `DATABASE_URL` uses `localhost`. Inside Docker it uses `db`.
- **Polling:** the bot uses long polling, not webhooks. The process must run continuously, which `restart: unless-stopped` already ensures.
- **One bot per token:** a second process polling with the same token causes conflicts.

---

## 12. Upgrading to the AI CRM release

Follow these steps in order when upgrading from a version without the inventory database, the AI agent and the catalog.

### 12.1 Back up (mandatory)

```bash
cd /opt/real_avto_bot
docker compose exec -T db pg_dump -U postgres real_avto_konkurs | gzip > ~/backup_before_ai_crm_$(date +%F).sql.gz
ls -lh ~/backup_before_ai_crm_*.sql.gz   # make sure the file is not empty
```

### 12.2 Rotate the database password

Older versions kept the password in `docker-compose.yml`, so it is still in git history and **must be rotated**.

`POSTGRES_PASSWORD` is only used when a new database is created. For an existing database, change the password manually:

```bash
NEW_PW="$(openssl rand -hex 24)"; echo "$NEW_PW"     # store it safely
docker compose exec -T db psql -U postgres -c "ALTER USER postgres WITH PASSWORD '$NEW_PW';"
```

Then put the new password into:

- the root `.env`: `POSTGRES_PASSWORD=...` and `DATABASE_URL=postgresql+asyncpg://postgres:...@db:5432/real_avto_konkurs`;
- `backend/.env`: `DATABASE_URL=postgresql://postgres:...@db:5432/real_avto_konkurs`.

### 12.3 New settings (root `.env`)

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` (or `GROQ_API_KEY`) | AI for channel posts, voice and video, the sales agent and comments. See 12.10 |
| `AI_DAILY_BUDGET_USD`, `AI_USER_DAILY_LIMIT` | AI spending cap (default $1/day, 40 requests per customer) |
| `BUSINESS_NAME`, `BUSINESS_ADDRESS`, `BUSINESS_HOURS`, `REAL_AVTO_MAP_URL` | Used in agent and catalog answers |
| `BOT_USERNAME`, `CATALOG_URL`, `CRM_URL` | Catalog and CRM Mini Apps (HTTPS only) |
| `LISTING_FREEZE_HOURS`, `WORK_HOUR_START` / `WORK_HOUR_END`, `BUYOUT_REPLY_HOURS` | Listing freeze and buy-out offers |
| `COMMENTS_ENABLED`, `COMMENTS_AI_ENABLED`, `COMMENTS_ANSWER_ADMINS`, `DISCUSSION_GROUP_ID` | Channel comments |
| `CAR_STALE_DAYS` | "Still for sale?" prompt (default 7 days) |
| `INSTAGRAM_ENABLED`, `IG_*` | Instagram, after Meta approval |

The complete list with comments is in [`.env.example`](.env.example).

### 12.4 Update

```bash
git fetch && git checkout main && git pull
docker compose build
docker compose up -d
docker compose logs -f bot backend catalog | head -100
```

On startup the bot creates the new tables (`cars`, `leads`, …) and columns.

### 12.5 HTTPS

HTTPS is required for the catalog Mini App and the Instagram webhook. Example Caddy configuration (certificates are issued automatically):

```
catalog.example.com {
    reverse_proxy 127.0.0.1:3002
}
crm.example.com {
    reverse_proxy 127.0.0.1:3000
}
hook.example.com {
    reverse_proxy /webhooks/instagram 127.0.0.1:8081
}
```

The CRM is an internal panel. You may keep it off the public internet (VPN or IP allow-list).

### 12.6 Telegram

- [ ] The bot is an **admin** in the main channel and in its **discussion group**.
- [ ] `ADMIN_TELEGRAM_IDS` lists the team that receives lead and car cards.
- [ ] Optional, Telegram Business: in the owner's account open *Chatbots*, add the bot, and **exclude contacts**.

### 12.7 Import channel history

On its **first start**, the upgraded bot copies previously approved bot listings that are still on sale into the inventory. The log shows the number of cars added.

Import the channel history **after** that, so bot listings are not added twice:

1. Export from Telegram Desktop: channel → *Export chat history* → JSON, without media.
2. Copy the export to the server as `/opt/real_avto_bot/import/result.json`.
3. Run:

```bash
docker compose cp import/result.json bot:/app/result.json
docker compose exec bot python -m scripts.import_channel_export /app/result.json --dry-run
docker compose exec bot python -m scripts.import_channel_export /app/result.json --ai
```

### 12.8 Smoke test

- [ ] **New post:** post a car to the channel → admins receive "🆕 Kanalda yangi mashina".
- [ ] **Edit:** change the post to say "SOTILDI" → the car is marked sold.
- [ ] **Reply:** reply "sotildi" to an old post that is not in the database → the car is restored and marked sold.
- [ ] **Speech:** post a spoken round video → the card shows "🎙 Eshitilgani (Gemini): …".
- [ ] **Agent:** from another account, ask the bot "Cobalt bormi?" → it answers from the database. "I want to talk to a manager" → a lead card is sent.
- [ ] **Lead reply:** reply to the lead card → the customer receives the message.
- [ ] **Comments:** comment "narxi qancha?" under a post → a public answer appears.
- [ ] **Listing:** submit a test listing → the card shows the scheduled time and "💰 Sotib olamiz".
- [ ] **Statistics:** `/statistika` works, and the CRM *Cars* and *Customers (AI)* pages open.
- [ ] **Catalog:** the catalog opens over HTTPS, photos load, and the bot shows the "🚗 Katalog" button.

### 12.9 Rollback

```bash
git checkout <previous-commit> && docker compose build && docker compose up -d
# Only if data must be restored as well:
gunzip -c ~/backup_before_ai_crm_YYYY-MM-DD.sql.gz | docker compose exec -T db psql -U postgres real_avto_konkurs
```

The new tables do not affect the old code, so rolling back the code alone is usually enough.

### 12.10 Switching to Gemini (video + voice + Uzbek)

1. **Get a key** at <https://aistudio.google.com/apikey> and enable **billing** on the project. Free-tier limits are too low for production.
2. **Configure** the root `.env`:
   ```env
   AI_PROVIDER=auto          # Gemini is selected when GEMINI_API_KEY is set
   GEMINI_API_KEY=...
   AI_DAILY_BUDGET_USD=1     # when exceeded, AI pauses until tomorrow and admins are notified
   ```
   Keep `GROQ_API_KEY` if you want a quick way back (`AI_PROVIDER=groq`).
3. **Restart:** `docker compose up -d --build bot`. The log must show `AI: gemini (model=gemini-3.1-flash-lite, yoqilgan=True ...)`. If the bot falls back to Groq, admins receive a warning on startup.
4. **Test:** post a spoken round video and a silent video that only shows a car.
   - The card should show the speech and a `[Videoda ko'rinadi]: …` line.
   - Facts taken only from speech or video stay in **Review** until an admin confirms them.
5. **Tune:** check accuracy on 20–30 real samples. If it is not good enough, set `GEMINI_MODEL` to a larger Flash model (about 3× the cost) or `GEMINI_MEDIA_RESOLUTION=medium`.

**Limitation:** bots cannot download files larger than 20 MB. Such videos are stored and can be sent to customers, but they are not analysed. Round videos and voice messages are always small enough.

**Estimated cost (Flash-Lite):**

| Item | Cost |
|---|---|
| Text message | ≈ $0.0008 |
| 30 s voice message | ≈ $0.001 |
| 40 s round video | ≈ $0.003 |

A few thousand messages a month comes to roughly **$5–7**. Today's spend appears in the log and in the admin alert when the cap is reached.

### 12.11 Security changes in this release

- **CRM API port:** the API is bound to `127.0.0.1:3001` only. Open the CRM through `http://IP:3000` or its HTTPS domain. A reverse proxy on the host that targets `localhost:3001` keeps working.
- **Production checks:** with `NODE_ENV=production`, the backend refuses to start with a short `JWT_SECRET` or the default `CRM_ADMIN_PASSWORD`. The reason is logged as `[FATAL]` (`docker compose logs backend`).
- **Login throttling:** 8 failed attempts per IP and username within 15 minutes cause a temporary block (HTTP 429). Passwords are hashed with scrypt.
- **Redis persistence:** Redis persists bot conversation state (`redis_data` volume, AOF, `noeviction`), so half-filled forms survive restarts.
- **Non-root containers:** the bot and CRM API containers run as non-root users.
- **Admin commands:**
  - `/navbat` re-sends listings waiting for moderation;
  - `/umumiy` shows user, listing, sales and AI-spend statistics.
