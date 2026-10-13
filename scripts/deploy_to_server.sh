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

STOP_OTHER=""   # --stop-bozorliii: the other project on this server is stopped first (only "stop", nothing is deleted)
ARGS=()
for a in "$@"; do
    case "$a" in
        --stop-bozorliii) STOP_OTHER="/opt/bozorliii" ;;
        *) ARGS+=("$a") ;;
    esac
done
set -- "${ARGS[@]}"
TARGET="${1:?Foydalanish: bash scripts/deploy_to_server.sh user@host ~/.ssh/kalit [/opt/real_avto_bot] [--stop-bozorliii]}"
KEY="${2:?SSH kalit fayli kerak, masalan ~/.ssh/bozorliii_student_key}"
REMOTE_DIR="${3:-/opt/real_avto_bot}"

cd "$(dirname "$0")/.."
# KEY="password": log in with the password from $SSHPASS (sshpass) instead of a key file
USE_PASSWORD=""
if [ "$KEY" = "password" ]; then
    [ -n "${SSHPASS:-}" ] || { echo "SSHPASS (server paroli) berilmagan"; exit 1; }
    command -v sshpass > /dev/null || { echo "sshpass o'rnatilmagan"; exit 1; }
    USE_PASSWORD=1
elif [ ! -f "$KEY" ]; then
    echo "SSH kalit topilmadi: $KEY"; exit 1
fi
[ -f .env ] || { echo ".env topilmadi — avval .env.example dan .env yarating va to'ldiring"; exit 1; }
command -v rsync > /dev/null || { echo "rsync o'rnatilmagan (Mac: bor; Ubuntu: sudo apt install rsync)"; exit 1; }

if [ -n "$USE_PASSWORD" ]; then
    SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no)
    SSH=(sshpass -e ssh "${SSH_OPTS[@]}")
    SCP=(sshpass -e scp "${SSH_OPTS[@]}")
    RSYNC_SSH="sshpass -e ssh ${SSH_OPTS[*]}"
else
    SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new)
    SCP=(scp -i "$KEY")
    RSYNC_SSH="ssh -i $KEY"
fi

echo "==> Server holati"
"${SSH[@]}" "$TARGET" 'free -h; df -h / | tail -n 1; docker ps --format "table {{.Names}}\t{{.Status}}"'

if [ -n "$STOP_OTHER" ]; then
    echo "==> $STOP_OTHER vaqtincha to'xtatilmoqda (.env nusxalanadi, hech narsa o'chirilmaydi)"
    "${SSH[@]}" "$TARGET" "set -e; cd '$STOP_OTHER'; b=\$HOME/bozorliii_env_backup_\$(date +%Y%m%d_%H%M%S); cp -a .env \$b && chmod 600 \$b && echo \".env nusxasi: \$b\"; docker compose -f docker-compose.prod.yml stop; free -h"
fi

echo "==> Server papkasi: $REMOTE_DIR"
"${SSH[@]}" "$TARGET" "sudo mkdir -p '$REMOTE_DIR' && sudo chown \$(id -un):\$(id -gn) '$REMOTE_DIR'"

echo "==> Kod yuborilmoqda"
rsync -az --human-readable \
    --exclude .git --exclude node_modules --exclude '**/dist' --exclude .venv --exclude venv \
    --exclude '__pycache__' --exclude '*.pyc' --exclude .pytest_cache --exclude .ruff_cache \
    --exclude .env --exclude backend/.env --exclude frontend/.env --exclude catalog/.env \
    -e "$RSYNC_SSH" ./ "$TARGET:$REMOTE_DIR/"

echo "==> .env (faqat birinchi marta)"
if "${SSH[@]}" "$TARGET" "test -f '$REMOTE_DIR/.env'"; then
    echo "serverda .env bor — o'zgartirilmadi"
else
    "${SCP[@]}" .env "$TARGET:$REMOTE_DIR/.env"
    echo ".env yuborildi"
fi
if [ -f backend/.env ] && ! "${SSH[@]}" "$TARGET" "test -f '$REMOTE_DIR/backend/.env'"; then
    "${SCP[@]}" backend/.env "$TARGET:$REMOTE_DIR/backend/.env"
fi

echo "==> Serverda o'rnatish"
TTY=(-t)
[ -t 1 ] || TTY=()  # CI: no terminal
"${SSH[@]}" "${TTY[@]}" "$TARGET" "cd '$REMOTE_DIR' && SETUP_QUIET_SECRETS='${SETUP_QUIET_SECRETS:-0}' bash scripts/server_setup.sh"
