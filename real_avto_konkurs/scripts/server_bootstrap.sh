#!/usr/bin/env bash
# Serverda (root yoki docker huquqi bor foydalanuvchi) ishga tushiring.
# Oldindan: git clone ... yoki scp/rsync bilan loyiha papkasi serverda bo‘lishi kerak.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker yo‘q. Ubuntu misolida:"
  echo "  apt-get update && apt-get install -y ca-certificates curl"
  echo "  install.docker.com skripti yoki: apt-get install -y docker.io docker-compose-plugin"
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "!.env topilmadi — .env.example dan nusxa oling va to‘ldiring:"
  echo "  cp .env.example .env && nano .env"
  exit 1
fi

docker compose pull 2>/dev/null || true
docker compose build --no-cache
docker compose up -d
docker compose ps
echo "Log: docker compose logs -f bot"
