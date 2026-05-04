#!/usr/bin/env bash
# Serverda (Docker huquqi bor foydalanuvchi) ishga tushiring.
# Oldindan: loyiha papkasi serverda (git clone yoki rsync).
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker yo'q. Ubuntu misolida:"
  echo "  apt-get update && apt-get install -y ca-certificates curl"
  echo "  install.docker.com skripti yoki: apt-get install -y docker.io docker-compose-plugin"
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "!.env topilmadi — .env.example dan nusxa oling va to'ldiring:"
  echo "  cp .env.example .env && nano .env"
  exit 1
fi

if [[ ! -f backend/.env ]]; then
  cp backend/.env.example backend/.env
  echo "[!] backend/.env yaratildi (namunadan). JWT_SECRET va CRM_ADMIN_PASSWORD ni o'zgartiring:"
  echo "    nano backend/.env"
fi

docker compose pull 2>/dev/null || true
if [[ "${BOOTSTRAP_NO_CACHE:-}" == "1" ]]; then
  docker compose build --no-cache
else
  docker compose build
fi
docker compose up -d
docker compose ps
echo ""
echo "Loglar: docker compose logs -f bot"
echo "To'liq qayta build (keshsiz): BOOTSTRAP_NO_CACHE=1 bash scripts/server_bootstrap.sh"
