#!/usr/bin/env bash
# Run on the server as a user with Docker access.
# Prerequisite: the project is already on the server (git clone or rsync).
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. On Ubuntu:"
  echo "  apt-get update && apt-get install -y ca-certificates curl"
  echo "  the get.docker.com script, or: apt-get install -y docker.io docker-compose-plugin"
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "!.env not found — copy .env.example and fill it in:"
  echo "  cp .env.example .env && nano .env"
  exit 1
fi

if ! grep -Eq '^POSTGRES_PASSWORD=.+' .env; then
  echo "!POSTGRES_PASSWORD is missing in .env. On an existing server use the current password (DEPLOY.md, section 12):"
  echo "  echo 'POSTGRES_PASSWORD=...' >> .env"
  exit 1
fi

if [[ ! -f backend/.env ]]; then
  cp backend/.env.example backend/.env
  echo "[!] backend/.env created from the example. Fill in JWT_SECRET, CRM_ADMIN_PASSWORD and DATABASE_URL:"
  echo "    nano backend/.env"
  exit 1
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
echo "Logs: docker compose logs -f bot"
echo "Full rebuild without cache: BOOTSTRAP_NO_CACHE=1 bash scripts/server_bootstrap.sh"
