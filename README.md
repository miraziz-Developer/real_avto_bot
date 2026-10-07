# Real Avto

Sales automation for a used-car dealership that sells through Telegram: a Telegram bot with an AI sales agent, a channel tracker that keeps an inventory database in sync with the channel, a CRM, and a public car catalog (website + Telegram Mini App).

| Component | Stack | Path |
|---|---|---|
| Telegram bot, AI agent, background workers | Python 3.13, aiogram 3, SQLAlchemy (async) | [`bot/`](bot) |
| CRM API | Node.js 20, Express | [`backend/`](backend) |
| CRM web app | React 18, Vite | [`frontend/`](frontend) |
| Public catalog / Mini App | React 18, Vite | [`catalog/`](catalog) |
| Storage | PostgreSQL 16, Redis 7 | [`docker-compose.yml`](docker-compose.yml) |

- **Server deployment (Docker, step by step):** [DEPLOY.md](DEPLOY.md)
- **Bot configuration:** [`.env.example`](.env.example) · **CRM API configuration:** [`backend/.env.example`](backend/.env.example)
- **One-command bootstrap:** `bash scripts/server_bootstrap.sh`

## Quick start (local)

```bash
cp .env.example .env              # set BOT_TOKEN, CHANNEL_ID, ADMIN_TELEGRAM_IDS, GEMINI_API_KEY
cp backend/.env.example backend/.env
docker compose up -d --build
```

- CRM: <http://localhost:3000> (credentials from `backend/.env`)
- Catalog: <http://localhost:3002>
- Bot logs: `docker compose logs -f bot`

Run only one bot process per token — two pollers on the same token conflict.

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check bot scripts tests                      # lint (configured in ruff.toml)
pytest tests -q                                   # unit tests (DB tests are skipped)

# Full suite against a real PostgreSQL
docker run -d --rm --name realavto-testdb -e POSTGRES_PASSWORD=test -e POSTGRES_DB=realavto_test \
  -p 55432:5432 postgres:16-alpine
TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:55432/realavto_test pytest tests -q
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on every push and pull request:

- lint and bot tests against PostgreSQL;
- CRM backend tests;
- frontend and catalog builds;
- `docker compose` config check and image builds;
- a backend smoke test against PostgreSQL.

---

## Features

### 1. Inventory database and channel tracker

Every listing posted to the main channel is written to the `cars` table. The sales agent, the catalog and the statistics all read from this table.

- **Requirement:** the bot must be an **admin** of the main channel (`CHANNEL_ID`).
- **New posts** are parsed: text, albums, round videos and voice. The bot extracts make, model, year, mileage and price, and admins receive a card for each car.
- **Status:** complete data → 🟢 *Active*. Missing data, or facts taken only from speech → 🟡 *Review*, where an admin confirms or fixes the car from the card.
- **Ways to mark a car sold** (any of these):
  - edit the post to say "SOTILDI";
  - reply "sotildi" to the post;
  - press **💰 Sold** on the card;
  - send `/sotildi ID [price]`;
  - change it in the CRM.
- **Reserved:** adding or removing "BRON" in a post moves the car between *Reserved* and *Active*.
- **Editing a post** updates the database. Each channel message's text is stored separately, so the edit is compared with that message's previous text only; other posts of the same car and video transcripts are kept. Admins receive a card listing what changed (e.g. `narx: $9 500 → $9 200`).
  - Price, mileage, year, model and other fields are corrected.
  - "SOTILDI" added → *Sold*. Removed again → back to *Active*.
  - Phone number removed → *Sold*. Phone restored → back to *Active*.
  - An edited reply (e.g. "narxi 8000$" → "narxi 7500$") changes only that part.
- **Reposts:** the same car posted again (for example, with a lower price) is merged into the existing record instead of being duplicated.
- **Old posts** (published before the bot joined the channel):
  - a reply such as "sotildi", "bron" or "narxi 8000$", or an edit of the post, restores the car from the original post and applies the change;
  - Telegram includes the original post in the reply.
- **Post-sale videos:** round videos or posts such as "muborak", "sotildi" or "olib ketishdi" are understood from the speech.
  - As a reply, they mark the replied post's car sold.
  - Standalone, they mark the single matching car sold. Matching uses the model (with aliases, e.g. Gentra ↔ Lacetti), plus the year and colour when given.
  - If several cars match, admins are asked to choose. If none match, admins are notified and no junk record is created.
- **Deleted posts:** checked every 3 hours. Deleted posts take the car off sale; a mass-deletion guard prevents accidents.
- **Stale cars:** a car on sale for more than `CAR_STALE_DAYS` (7) days triggers an "is it still for sale?" prompt to admins, sent during working hours.
- **Approved bot listings** are added to the database as well.

**Admin commands:** `/statistika [days]` · `/sotuvda` · `/tekshiruv` · `/mashina ID` · `/sotildi ID [price]` · `/qayta ID` · `/import_kanal LINK` · `/umumiy` · `/navbat`

`/qayta ID` re-transcribes a car's videos with the current AI and replaces the facts taken from speech (fields an admin fixed by hand are kept). Use it when a car was created while AI misheard the audio, e.g. before Gemini was configured.

On a car card, **✏️ Edit** accepts: `narx 9800`, `yil 2021`, `probeg 76000`, and for cars the dealership bought itself `xarid 8000`, `xarajat 300`.

**Importing channel history.** After the bot is connected to a channel it only sees new posts. Older cars are imported with an admin command in the bot:

```
/import_kanal https://t.me/your_channel/12345 [count=300] [archive_after_days=30]
```

Pass the link of the **latest** post (⋮ → Copy link).
- **How it reads posts:** the bot reads each of the last `count` posts by forwarding it silently to the admin's chat and deleting it right away. It gets everything: text, photos, videos, and the speech in round videos (via Gemini).
- **Processing:** each post goes through the normal channel tracker. Videos and descriptions merge into one car, reposts are not duplicated, and "SOTILDI" posts become sold.
- **During import:** no admin cards or customer notifications are sent; a single report arrives at the end.
- **Archiving:** cars older than `archive_after_days` with an unknown status are archived, so the agent never offers stale cars.
- **Sold detection:** deleted posts cannot be read, so they are skipped automatically. Posts saying "SOTILDI" become sold. When the channel's posts usually carry a phone number, a post whose phone was removed is treated as sold, because the team removes it after a sale.
- **Re-running** is safe: known posts are skipped.
- **Limitation:** a forwarded copy loses its reply link, so old "sotildi" replies are matched by model only.
- **Cost:** each round video costs about $0.003 of Gemini time, so 300 posts with many videos may exceed the default $1 daily cap. Raise `AI_DAILY_BUDGET_USD` for the import day.

**Choosing cars by hand:** an admin can forward any channel post (an album, a video and its description) to the bot. The bot adds that car to the database and sends its card; forwarding the same post again only says it is already there. This is useful on day one to add only the cars that are still for sale. New posts are then tracked automatically.

**Text-only alternative** (Telegram Desktop export, no media):

```bash
# Telegram Desktop → channel → ⋮ → Export chat history → JSON
python -m scripts.import_channel_export path/to/result.json --dry-run   # preview
python -m scripts.import_channel_export path/to/result.json --ai        # write to the database
```

### 2. AI

Set `GEMINI_API_KEY` (recommended) or `GROQ_API_KEY`. With `AI_PROVIDER=auto`, Gemini is used whenever its key is present.

| | Gemini (recommended) | Groq |
|---|---|---|
| Text, tool calling | ✅ | ✅ |
| Voice messages | ✅ accurate Uzbek | ⚠️ Whisper often misdetects Uzbek |
| Round videos / videos | ✅ hears the speech **and** sees the car (make, colour, body, odometer) | speech only |

- **Spending cap:** `AI_DAILY_BUDGET_USD` (default $1) and `AI_USER_DAILY_LIMIT` (40 requests per customer per day). When the cap is reached, AI pauses until the next day and admins are notified.
- **Fallback:** without a key, rule-based parsing and template replies keep working.
- **Feedback for admins:**
  - each card shows which engine transcribed the speech;
  - on startup the bot warns admins if voice and video fall back to Groq.

Details and cost estimates: [DEPLOY.md § 12.10](DEPLOY.md#1210-switching-to-gemini-video--voice--uzbek).

### 3. AI sales agent

Customers can write or send voice messages to the bot. The agent answers **only from the inventory database** and hands the customer to a manager when they are ready to buy.

- **Customer side:**
  - finds cars on sale (never sold ones), sends photos and videos, and answers questions;
  - offers similar cars, or saves a search and notifies the customer when a matching car is posted;
  - never promises discounts, credit terms or paperwork, and never invents facts;
  - answers in the customer's language: Uzbek Latin, Uzbek Cyrillic or Russian.
- **Hand-off:**
  - the customer wants to visit, buy, discuss price or credit, or talk to a person → a 🔥 **lead card** goes to admins;
  - the card shows name, phone, car of interest, budget, payment method, visit time, AI summary and recent messages.
- **Managers:**
  - replying to the lead card sends the message to the customer and pauses the AI for that customer;
  - buttons: ✅ Take · 🤖 Let AI continue · 🏁 Sold · ❌ Close · 💬 Full chat;
  - unclaimed leads are re-announced after `LEAD_REMINDER_MINUTES` (5);
  - commands: `/leadlar`, `/lead ID`.
- **Deep links:** `https://t.me/<bot>?start=car_<ID>` opens a conversation about a specific car.

**Configuration:** `AGENT_ENABLED`, `GEMINI_AGENT_MODEL` / `GROQ_AGENT_MODEL`, `BUSINESS_NAME`, `BUSINESS_ADDRESS`, `BUSINESS_HOURS`, `REAL_AVTO_MAP_URL`, `LEAD_REMINDER_MINUTES`.

### 4. Channel comments and discussion group

The AI reads every comment, including text, voice, round videos and captioned media, and acts according to its type:

| Message | Action |
|---|---|
| Question (price, availability, mileage, condition, address, credit) | Short public answer from database facts + "🤖 Details in the bot" button |
| General question ("what cars do you have?") | Lists 2–4 cars currently on sale |
| Purchase intent | Answer + alert to admins |
| Negative feedback | Polite, non-defensive answer + alert to admins with a link |
| Insult, provocation, spam | No reply; insults are reported to admins |
| Praise, off-topic chat | Ignored |

Rules for public replies:

- Only database facts are used, and replies are in the commenter's language.
- Seller phone numbers, purchase prices, profit and internal notes are never disclosed.
- Unverified (*Review*) cars are discussed without numbers.
- If a post is not in the database, its public text is used.
- A manager's reply to a customer is never interrupted. A manager's own test question is answered without alerts (`COMMENTS_ANSWER_ADMINS`).
- At most 3 replies per person per 10 minutes, and the bot stays silent in groups that are not its own.

**Requirement:** the bot must be an **admin of the channel's discussion group**. Settings: `COMMENTS_ENABLED`, `COMMENTS_AI_ENABLED`, `DISCUSSION_GROUP_ID` (optional).

### 5. Telegram Business

The agent can also answer customers who write to the owner's personal account, replying on behalf of that account.

1. The account owner (Telegram **Premium** required) opens *Settings → Telegram Business → Chatbots* and adds the bot.
2. Enable "Reply to messages".
3. ⚠️ **Exclude contacts** (or select "New chats" only), otherwise the AI will reply to family and friends.
4. Admins receive "✅ Telegram Business connected".

When the owner writes to a customer personally, the AI stays silent with that customer for `BUSINESS_OWNER_PAUSE_HOURS` (6).

### 6. Listing freeze and buy-out offers

Listings submitted through the bot are held for `LISTING_FREEZE_HOURS` (6) **working hours** before they go to the channel. During this time the dealership can make the seller an offer.

- **Working hours:** `WORK_HOUR_START`–`WORK_HOUR_END` (9–21, Tashkent time). Nothing is published at night.
- **Admin card:** ✅ Approve · ❌ Reject · **💰 Buy out**, with the scheduled publication time.
- **💰 Buy out:**
  1. The admin enters a price (`8500` or `110 mln`).
  2. The seller chooses ✅ Agree · 💬 Discuss · 📢 No, publish it.
  3. If there is no answer within `BUYOUT_REPLY_HOURS` (24), the listing is published.
- **🏁 Bought:**
  - the car is stored as dealership-owned, with its purchase price;
  - repair costs and sale price are entered on the card;
  - **📢 Post to channel** publishes it in the house template;
  - profit appears in CRM statistics.
- **Disable:** `LISTING_FREEZE_HOURS=0`.

### 7. Catalog website and Telegram Mini App

`catalog/` is a mobile-first, sign-up-free catalog of cars **on sale**, served live from the database.

- **Browsing:**
  - search and filters: make, budget, year, transmission, sorting;
  - photo galleries;
  - similar cars.
- **"Fair price" badge:** each car is compared with the median price of similar cars (same model, ±1 year, last 12 months). The badge needs at least 3 comparables and is never shown for above-market prices.
- **Actions:**
  - "🤖 Ask a question" opens the agent with that car;
  - "💰 Sell my car" opens the listing flow in the bot;
  - "🔔 Notify me" saves a search, verified with the Telegram signature.
- **Never exposed:** seller phone, purchase price, profit or internal notes.
- **Public API:** `/api/public/*` (rate-limited per IP). CRM endpoints are not reachable through it.

Run with `docker compose up -d catalog` (port 3002), put it behind HTTPS and set:

```env
CATALOG_URL=https://catalog.example.com
BOT_USERNAME=your_bot
```

After a restart the bot shows a **🚗 Katalog** menu button. Mini Apps require HTTPS.

### 8. Instagram (Direct and comments)

The same agent answers Instagram Direct messages and comments, and qualified leads are delivered to admins in Telegram.

- **Direct:**
  - questions are answered from the database;
  - a manager's reply to the lead card is sent back to Direct;
  - when the owner replies personally, the AI pauses for 6 hours.
- **Comments:** a short public reply with a detailed private reply in Direct. Praise is ignored, and purchase intent alerts admins.
- **Setup**, after Meta App Review:
  1. In Meta Developers, open *Instagram API with Instagram Login* and request `instagram_business_basic`, `instagram_business_manage_messages` and `instagram_business_manage_comments`.
  2. Set the webhook URL to `https://DOMAIN/webhooks/instagram` (proxy to `127.0.0.1:8081`). Use `IG_VERIFY_TOKEN` as the verify token and subscribe to `messages` and `comments`.
  3. In `.env`, set `INSTAGRAM_ENABLED=true` and `IG_ACCESS_TOKEN`, `IG_APP_SECRET`, `IG_VERIFY_TOKEN`, `IG_ACCOUNT_ID`.
  4. Every webhook request is verified with `X-Hub-Signature-256`.

In development mode, before App Review, only accounts added as app testers work.

### 9. CRM

- **Cars:**
  - inventory, total value and time to sell;
  - stale cars and profit;
  - full history per car: price and status changes, reposts, deletions.
- **Customers (AI):**
  - open leads and leads waiting for a manager;
  - conversion and time to hand-off;
  - full conversation transcripts.
- **Listings:** freeze timers and buy-out offer status.
- **Clients, wishlists, contests.**

**Listing payment:** free by default (`LISTING_PAYMENT_ENABLED=false`).
