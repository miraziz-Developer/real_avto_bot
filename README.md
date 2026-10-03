# Real Avto konkurs

Telegram bot (e’lon, wishlist, konkurs, anonim savol), CRM (Node + React), PostgreSQL, Redis.

- **Serverga o‘rnatish (Docker, qadam-baqadam)**: [DEPLOY.md](DEPLOY.md)
- **Bot `.env` namunasi**: [.env.example](.env.example)
- **Backend `.env`**: [backend/.env.example](backend/.env.example)
- **Ishga tushirish (skript)**: `bash scripts/server_bootstrap.sh`

Mahalliy testlar:

```bash
pip install -r requirements-dev.txt && pytest tests -q          # bot
cd backend && npm ci && npm test                                # CRM API
```

Postgres ustidagi parallel (qulf) testlari uchun `TEST_DATABASE_URL` bering — [DEPLOY.md](DEPLOY.md#10-avtomatik-testlar-ci-yoki-mahalliy). CI: `.github/workflows/ci.yml`.
