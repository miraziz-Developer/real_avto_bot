#!/bin/bash
# =============================================================================
# Real Avto — one-command server setup (safe on a server shared with other Docker projects).
#
#   cd /opt/real_avto_bot && bash scripts/server_setup.sh
#
# What it does (re-running is safe):
#   1. checks Docker and the root .env (bot token, channel, admins, AI key);
#   2. fills in production secrets that are missing (Postgres password, JWT secret, CRM password)
#      and points both DATABASE_URLs at the "db" container;
#   3. picks free host ports so it does not collide with other projects, and stores them in .env;
#   4. builds and starts the stack (project name "real_avto": own containers, network and volumes);
#   5. waits until everything is healthy;
#   6. installs cron jobs (backup every 6 h, watchdog every 5 min) and a systemd unit (start on boot);
#   7. prints the CRM address and login.
# =============================================================================

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

say()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[x] %s\033[0m\n' "$*" >&2; exit 1; }

get_env() { # file key
    [ -f "$1" ] || return 0
    { grep -E "^$2=" "$1" || true; } | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

set_env() { # file key value — replaces the line or appends it
    local file="$1" key="$2" value="$3" tmp
    tmp="$(mktemp)"
    if grep -qE "^$key=" "$file"; then
        awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' "$file" > "$tmp"
        cat "$tmp" > "$file"
    else
        printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
    rm -f "$tmp"
}

port_in_use() {
    ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1$"
}

RESERVED_PORTS=" "

free_port() { # first port from $1 that is neither listening nor already chosen in this run
    local p="$1"
    while port_in_use "$p" || [[ "$RESERVED_PORTS" == *" $p "* ]]; do p=$((p + 1)); done
    echo "$p"
}

# --- 1. Prerequisites ---------------------------------------------------------
say "Tekshiruv"
command -v docker > /dev/null || die "Docker o'rnatilmagan"
docker compose version > /dev/null 2>&1 || die "docker compose plagini yo'q"
docker info > /dev/null 2>&1 || die "Docker'ga ruxsat yo'q (foydalanuvchini docker guruhiga qo'shing yoki sudo bilan)"
command -v openssl > /dev/null || die "openssl topilmadi"

[ -f .env ] || die ".env topilmadi. Kompyuteringizdagi .env ni shu papkaga ko'chiring (yoki: cp .env.example .env && nano .env)"
for key in BOT_TOKEN CHANNEL_ID ADMIN_TELEGRAM_IDS; do
    [ -n "$(get_env .env "$key")" ] || die ".env da $key bo'sh — to'ldiring va qayta ishga tushiring"
done
if [ -z "$(get_env .env GEMINI_API_KEY)" ]; then
    warn "GEMINI_API_KEY bo'sh — ovoz va dumaloq videolar yomon tushuniladi (Groq). Tavsiya: .env ga qo'shing."
fi

# --- 2. Secrets and database URLs --------------------------------------------
say "Maxfiy sozlamalar"
PG_PASSWORD="$(get_env .env POSTGRES_PASSWORD)"
if [ -z "$PG_PASSWORD" ] || [ "$PG_PASSWORD" = "postgres" ]; then
    if docker volume inspect real_avto_pg_data > /dev/null 2>&1; then
        die "POSTGRES_PASSWORD bo'sh, lekin baza allaqachon bor — eski parolni .env ga yozing"
    fi
    PG_PASSWORD="$(openssl rand -hex 24)"
    set_env .env POSTGRES_PASSWORD "$PG_PASSWORD"
    echo "Postgres paroli yaratildi (.env da)"
fi
set_env .env DATABASE_URL "postgresql+asyncpg://postgres:${PG_PASSWORD}@db:5432/real_avto_konkurs"
# Inside Docker the bot gets REDIS_URL from docker-compose; a local value (localhost) would not work
if grep -qE '^REDIS_URL=.*(localhost|127\.0\.0\.1)' .env; then
    set_env .env REDIS_URL "redis://redis:6379/0"
fi

[ -f backend/.env ] || cp backend/.env.example backend/.env
set_env backend/.env DATABASE_URL "postgresql://postgres:${PG_PASSWORD}@db:5432/real_avto_konkurs"
set_env backend/.env NODE_ENV production
JWT="$(get_env backend/.env JWT_SECRET)"
if [ "${#JWT}" -lt 32 ]; then
    set_env backend/.env JWT_SECRET "$(openssl rand -hex 32)"
fi
CRM_USER="$(get_env backend/.env CRM_ADMIN_USER)"
[ -n "$CRM_USER" ] || { CRM_USER="admin"; set_env backend/.env CRM_ADMIN_USER "$CRM_USER"; }
CRM_PASSWORD="$(get_env backend/.env CRM_ADMIN_PASSWORD)"
NEW_CRM_PASSWORD=""
if [ -z "$CRM_PASSWORD" ] || [ "$CRM_PASSWORD" = "admin123" ] || [ "${#CRM_PASSWORD}" -lt 10 ]; then
    CRM_PASSWORD="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)"
    set_env backend/.env CRM_ADMIN_PASSWORD "$CRM_PASSWORD"
    NEW_CRM_PASSWORD="$CRM_PASSWORD"
fi
chmod 600 .env backend/.env

# --- 3. Host ports (kept once chosen) -------------------------------------------
say "Portlar"
STACK_RUNNING="$(docker compose ps -q 2>/dev/null | head -n 1 || true)"
for spec in "CRM_PORT 3000" "CATALOG_PORT 3002" "BACKEND_PORT 3001" "IG_WEBHOOK_HOST_PORT 8081"; do
    set -- $spec
    current="$(get_env .env "$1")"
    if [ -z "$current" ]; then
        chosen="$(free_port "$2")"
        RESERVED_PORTS+="$chosen "
        set_env .env "$1" "$chosen"
        echo "$1=$chosen"
    elif [ -z "$STACK_RUNNING" ] && port_in_use "$current"; then
        die "$1=$current band (boshqa dastur ishlatyapti). .env da boshqa port yozing yoki qatorni o'chiring"
    else
        RESERVED_PORTS+="$current "
        echo "$1=$current"
    fi
done
CRM_PORT="$(get_env .env CRM_PORT)"
CATALOG_PORT="$(get_env .env CATALOG_PORT)"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
# The CRM calls its API through its own nginx (same origin); this only silences the production warning
if [ -z "$(get_env backend/.env CORS_ORIGIN)" ] && [ -n "$IP" ]; then
    set_env backend/.env CORS_ORIGIN "http://${IP}:${CRM_PORT},http://${IP}:${CATALOG_PORT}"
fi

# --- 4. Build and start -------------------------------------------------------
say "Build va ishga tushirish (birinchi marta 5–10 daqiqa)"
docker compose build
docker compose up -d --remove-orphans

# --- 5. Wait for health --------------------------------------------------------
say "Holat tekshiruvi"
for _ in $(seq 1 60); do
    unhealthy="$(docker compose ps --format '{{.Service}} {{.State}} {{.Health}}' | awk '$2!="running" || ($3!="" && $3!="healthy")')"
    [ -z "$unhealthy" ] && break
    sleep 5
done
docker compose ps
if [ -n "${unhealthy:-}" ]; then
    warn "Hali tayyor emas: $unhealthy"
    warn "Loglar: docker compose logs --tail=80 bot backend"
fi

# --- 6. Cron and systemd ----------------------------------------------------------
say "Backup, watchdog va avtomatik ishga tushish"
sudo mkdir -p /opt/backups/real_avto /var/log
sudo chown "$(id -un)":"$(id -gn)" /opt/backups/real_avto
sudo touch /var/log/real_avto_backup.log /var/log/real_avto_watchdog.log
sudo chown "$(id -un)":"$(id -gn)" /var/log/real_avto_backup.log /var/log/real_avto_watchdog.log
chmod +x scripts/server_backup.sh scripts/watchdog.sh
CRON_TMP="$(mktemp)"
(crontab -l 2>/dev/null || true) | { grep -v -e real_avto_backup.log -e real_avto_watchdog.log || true; } > "$CRON_TMP"
{
    echo "0 */6 * * * $PROJECT_DIR/scripts/server_backup.sh >> /var/log/real_avto_backup.log 2>&1"
    echo "*/5 * * * * $PROJECT_DIR/scripts/watchdog.sh >> /var/log/real_avto_watchdog.log 2>&1"
} >> "$CRON_TMP"
crontab "$CRON_TMP"
rm -f "$CRON_TMP"
echo "cron: backup har 6 soatda, watchdog har 5 daqiqada"

sed "s|^WorkingDirectory=.*|WorkingDirectory=$PROJECT_DIR|" scripts/real-avto-stack.service \
    | sudo tee /etc/systemd/system/real-avto-stack.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable real-avto-stack.service > /dev/null 2>&1 && echo "systemd: server qayta yonganda avtomatik ishga tushadi"

# --- 7. Summary ----------------------------------------------------------------------
say "Tayyor"
echo "CRM:      http://${IP}:${CRM_PORT}   (login: ${CRM_USER})"
if [ -n "$NEW_CRM_PASSWORD" ]; then
    # Admins get the new CRM password in their private chat with the bot
    BOT_TOKEN_VALUE="$(get_env .env BOT_TOKEN)"
    for id in $(get_env .env ADMIN_TELEGRAM_IDS | tr ',' ' '); do
        curl -s -m 15 "https://api.telegram.org/bot${BOT_TOKEN_VALUE}/sendMessage" \
            --data-urlencode "chat_id=${id}" \
            --data-urlencode "text=🛠 Real Avto serverga o'rnatildi.
CRM: http://${IP}:${CRM_PORT}
Login: ${CRM_USER}
Parol: ${NEW_CRM_PASSWORD}
(Bu xabarni saqlab, keyin o'chirib qo'ying)" > /dev/null || true
    done
    if [ "${SETUP_QUIET_SECRETS:-0}" = "1" ]; then
        echo "          parol adminlarga Telegram'da yuborildi (serverda: backend/.env)"
    else
        echo "          parol: ${NEW_CRM_PASSWORD}   ← saqlab qo'ying (backend/.env da ham bor)"
    fi
fi
echo "Katalog:  http://${IP}:${CATALOG_PORT}"
echo "Bot logi: docker compose logs -f bot"
echo
echo "Eslatma: shu tokenli bot boshqa joyda (kompyuteringizda) ishlayotgan bo'lsa — uni to'xtating, aks holda to'qnashadi."
