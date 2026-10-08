#!/bin/bash
# =============================================================================
# Real Avto — deploy from your computer to a server in one command.
#
#   bash scripts/deploy_to_server.sh bozorliii@172.198.138.146 ~/.ssh/bozorliii_student_key [/opt/real_avto_bot]
#
# 1. copies the project to the server (rsync; .git, node_modules, builds are skipped);
# 2. on the FIRST deploy copies your .env (and backend/.env if you have one). Later deploys never overwrite the
#    server's .env files: they hold the generated database password, CRM password and ports;
# 3. runs scripts/server_setup.sh on the server (build, start, health check, cron, systemd).
# Re-run it after every code update.
# =============================================================================

set -euo pipefail

TARGET="${1:?Foydalanish: bash scripts/deploy_to_server.sh user@host ~/.ssh/kalit [/opt/real_avto_bot]}"
KEY="${2:?SSH kalit fayli kerak, masalan ~/.ssh/bozorliii_student_key}"
REMOTE_DIR="${3:-/opt/real_avto_bot}"

cd "$(dirname "$0")/.."
[ -f "$KEY" ] || { echo "SSH kalit topilmadi: $KEY"; exit 1; }
[ -f .env ] || { echo ".env topilmadi — avval .env.example dan .env yarating va to'ldiring"; exit 1; }
command -v rsync > /dev/null || { echo "rsync o'rnatilmagan (Mac: bor; Ubuntu: sudo apt install rsync)"; exit 1; }

SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new)

echo "==> Server papkasi: $REMOTE_DIR"
"${SSH[@]}" "$TARGET" "sudo mkdir -p '$REMOTE_DIR' && sudo chown \$(id -un):\$(id -gn) '$REMOTE_DIR'"

echo "==> Kod yuborilmoqda"
rsync -az --human-readable \
    --exclude .git --exclude node_modules --exclude '**/dist' --exclude .venv --exclude venv \
    --exclude '__pycache__' --exclude '*.pyc' --exclude .pytest_cache --exclude .ruff_cache \
    --exclude .env --exclude backend/.env --exclude frontend/.env --exclude catalog/.env \
    -e "ssh -i $KEY" ./ "$TARGET:$REMOTE_DIR/"

echo "==> .env (faqat birinchi marta)"
if "${SSH[@]}" "$TARGET" "test -f '$REMOTE_DIR/.env'"; then
    echo "serverda .env bor — o'zgartirilmadi"
else
    scp -i "$KEY" .env "$TARGET:$REMOTE_DIR/.env"
    echo ".env yuborildi"
fi
if [ -f backend/.env ] && ! "${SSH[@]}" "$TARGET" "test -f '$REMOTE_DIR/backend/.env'"; then
    scp -i "$KEY" backend/.env "$TARGET:$REMOTE_DIR/backend/.env"
fi

echo "==> Serverda o'rnatish"
"${SSH[@]}" -t "$TARGET" "cd '$REMOTE_DIR' && bash scripts/server_setup.sh"
