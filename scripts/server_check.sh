#!/bin/bash
# =============================================================================
# Real Avto — end-to-end health check on the server (read-only, prints no secrets).
#
#   cd /opt/real_avto_bot && bash scripts/server_check.sh
#
# Checks: containers, CRM (login + API through its nginx), public catalog API, Telegram bot
# (token, no webhook / second instance, channel rights), AI key, database contents,
# recent errors in the bot log, backups, cron, disk and memory.
# Exit code = number of failed checks.
# =============================================================================

set -uo pipefail
# Run as a file → its project; piped ("bash -s") → the current directory
[ -f "$0" ] && cd "$(cd "$(dirname "$0")/.." && pwd)"
[ -f docker-compose.yml ] || { echo "docker-compose.yml topilmadi: $(pwd)"; exit 1; }

FAILS=0
ok()   { printf '  \033[1;32m✔\033[0m %s\n' "$*"; }
bad()  { printf '  \033[1;31m✘\033[0m %s\n' "$*"; FAILS=$((FAILS + 1)); }
note() { printf '  \033[1;33m•\033[0m %s\n' "$*"; }
head_() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }

env_value() { # file key
    { grep -E "^$2=" "$1" 2>/dev/null || true; } | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

BOT_TOKEN="$(env_value .env BOT_TOKEN)"
CRM_PORT="$(env_value .env CRM_PORT)"; CRM_PORT="${CRM_PORT:-3000}"
CATALOG_PORT="$(env_value .env CATALOG_PORT)"; CATALOG_PORT="${CATALOG_PORT:-3002}"
BACKEND_PORT="$(env_value .env BACKEND_PORT)"; BACKEND_PORT="${BACKEND_PORT:-3001}"
TG="https://api.telegram.org/bot${BOT_TOKEN}"

json() { python3 -c "import sys,json; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1" 2>/dev/null; }

# --- Containers ----------------------------------------------------------------
head_ "Konteynerlar"
for svc in db redis backend bot frontend catalog; do
    st="$(docker compose ps --format '{{.Service}} {{.State}} {{.Health}}' | awk -v s="$svc" '$1==s {print $2, $3}')"
    case "$st" in
        "running healthy") ok "$svc: $st" ;;
        *) bad "$svc: ${st:-ishlamayapti}" ;;
    esac
done
restarts="$(docker inspect -f '{{.Name}} {{.RestartCount}}' $(docker compose ps -q) 2>/dev/null | awk '$2>0')"
[ -z "$restarts" ] && ok "qayta ishga tushishlar yo'q" || note "qayta ishga tushgan: $restarts"

# --- CRM -------------------------------------------------------------------------
head_ "CRM"
[ "$(curl -s -o /dev/null -w '%{http_code}' -m 10 "http://127.0.0.1:${BACKEND_PORT}/health")" = 200 ] \
    && ok "backend /health" || bad "backend /health javob bermadi"
[ "$(curl -s -o /dev/null -w '%{http_code}' -m 10 "http://127.0.0.1:${CRM_PORT}/")" = 200 ] \
    && ok "CRM sahifasi (:${CRM_PORT})" || bad "CRM sahifasi ochilmadi (:${CRM_PORT})"
CRM_USER="$(env_value backend/.env CRM_ADMIN_USER)"
CRM_PASS="$(env_value backend/.env CRM_ADMIN_PASSWORD)"
login_body="$(python3 -c 'import json,sys; print(json.dumps({"username": sys.argv[1], "password": sys.argv[2]}))' "$CRM_USER" "$CRM_PASS")"
TOKEN="$(curl -s -m 10 -H 'Content-Type: application/json' -d "$login_body" "http://127.0.0.1:${CRM_PORT}/api/auth/login" | json 'd.get("token","")')"
if [ -n "$TOKEN" ]; then
    ok "CRM login (${CRM_USER}) ishlaydi"
    for path in stats listings clients; do
        code="$(curl -s -o /dev/null -w '%{http_code}' -m 15 -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:${CRM_PORT}/api/${path}")"
        [ "$code" = 200 ] && ok "CRM API /${path}" || bad "CRM API /${path}: HTTP $code"
    done
else
    bad "CRM login muvaffaqiyatsiz"
fi

# --- Catalog -----------------------------------------------------------------------
head_ "Katalog"
[ "$(curl -s -o /dev/null -w '%{http_code}' -m 10 "http://127.0.0.1:${CATALOG_PORT}/")" = 200 ] \
    && ok "katalog sahifasi (:${CATALOG_PORT})" || bad "katalog sahifasi ochilmadi"
cars_json="$(curl -s -m 15 "http://127.0.0.1:${CATALOG_PORT}/api/public/cars")"
n="$(printf '%s' "$cars_json" | json 'd["total"]')"
[ -n "$n" ] && ok "katalog API: ${n} ta mashina ko'rinadi" || bad "katalog API javobi noto'g'ri"
[ "$(curl -s -o /dev/null -w '%{http_code}' -m 10 "http://127.0.0.1:${CATALOG_PORT}/api/stats")" = 404 ] \
    && ok "CRM API katalog orqali yopiq" || bad "CRM API katalog orqali ochiq!"

# --- Telegram bot ------------------------------------------------------------------
head_ "Telegram bot"
me="$(curl -s -m 15 "$TG/getMe")"
if [ "$(printf '%s' "$me" | json 'd["ok"]')" = True ]; then
    ok "token ishlaydi: @$(printf '%s' "$me" | json 'd["result"]["username"]')"
    [ "$(printf '%s' "$me" | json 'd["result"].get("can_read_all_group_messages")')" = True ] \
        && ok "guruh xabarlarini o'qiy oladi (privacy off)" \
        || note "privacy mode yoqilgan — bot guruhda faqat admin bo'lsa hamma xabarni ko'radi"
    hook="$(curl -s -m 15 "$TG/getWebhookInfo" | json 'd["result"].get("url","")')"
    [ -z "$hook" ] && ok "webhook yo'q (polling)" || bad "webhook o'rnatilgan — polling bilan to'qnashadi"
    BOT_ID="$(printf '%s' "$me" | json 'd["result"]["id"]')"
    for key in CHANNEL_ID DISCUSSION_GROUP_ID; do
        chat="$(env_value .env "$key")"
        [ -n "$chat" ] || continue
        member="$(curl -s -m 15 "$TG/getChatMember" --data-urlencode "chat_id=$chat" --data-urlencode "user_id=$BOT_ID")"
        status="$(printf '%s' "$member" | json 'd["result"]["status"]')"
        title="$(curl -s -m 15 "$TG/getChat" --data-urlencode "chat_id=$chat" | json 'd["result"].get("title","")')"
        case "$status" in
            administrator|creator) ok "$key «$title»: bot admin" ;;
            *) bad "$key «$title»: bot admin emas (${status:-xato})" ;;
        esac
    done
else
    bad "BOT_TOKEN ishlamayapti"
fi
conflicts="$(docker compose logs --since 30m bot 2>/dev/null | grep -c 'TelegramConflictError\|terminated by other getUpdates' || true)"
[ "${conflicts:-0}" = 0 ] && ok "boshqa joyda shu token bilan bot ishlamayapti" \
    || bad "to'qnashuv: shu token bilan boshqa bot ham ishlayapti (${conflicts} ta xabar, 30 daqiqa) — kompyuterdagisini to'xtating"

# --- AI ---------------------------------------------------------------------------------
head_ "AI"
ai_out="$(docker compose exec -T bot python - <<'PY' 2>&1 | tail -n 3
import asyncio
from bot.ai import get_ai, close_ai
async def main():
    ai = get_ai()
    r = await ai.chat_json('Return JSON {"ok": true}.', "ping", max_tokens=20)
    print("AI_OK", type(ai).__name__, r.get("ok"))
    await close_ai()
asyncio.run(main())
PY
)"
case "$ai_out" in
    *AI_OK*True*) ok "AI javob berdi ($(printf '%s' "$ai_out" | grep AI_OK | awk '{print $2}'))" ;;
    *) bad "AI ishlamadi: $(printf '%s' "$ai_out" | tail -n 1 | cut -c1-200)" ;;
esac

# --- Database ------------------------------------------------------------------------------
head_ "Baza"
psql() { docker compose exec -T db psql -U postgres -d real_avto_konkurs -At -c "$1" 2>/dev/null; }
cars="$(psql "select status, count(*) from cars group by status order by status" | tr '\n' ' ')"
[ -n "$cars" ] && ok "mashinalar: ${cars}" || note "mashinalar jadvali bo'sh"
ok "leadlar: $(psql 'select count(*) from leads' || echo '?'), foydalanuvchilar: $(psql 'select count(*) from users' || echo '?')"

# --- Bot log ----------------------------------------------------------------------------------
head_ "Bot logi (oxirgi 1 soat)"
errors="$(docker compose logs --since 1h bot 2>/dev/null | grep -E ' ERROR |Traceback|CRITICAL' | grep -v 'TelegramConflictError' | tail -n 10)"
[ -z "$errors" ] && ok "xato yo'q" || { bad "xatolar bor:"; printf '%s\n' "$errors" | cut -c1-240; }

# --- Ops -------------------------------------------------------------------------------------------
head_ "Backup, cron, resurslar"
crontab -l 2>/dev/null | grep -q real_avto_backup.log && ok "backup cron o'rnatilgan" || bad "backup cron yo'q"
crontab -l 2>/dev/null | grep -q real_avto_watchdog.log && ok "watchdog cron o'rnatilgan" || bad "watchdog cron yo'q"
systemctl is-enabled real-avto-stack.service > /dev/null 2>&1 && ok "systemd: avtomatik ishga tushish yoqilgan" \
    || bad "systemd unit yoqilmagan"
if [ "${RUN_BACKUP:-0}" = 1 ]; then
    bash scripts/server_backup.sh > /tmp/real_avto_check_backup.log 2>&1 && ok "backup qo'lda ishga tushirildi" \
        || bad "backup xatosi: $(tail -n 2 /tmp/real_avto_check_backup.log)"
fi
last_backup="$(ls -1t /opt/backups/real_avto/*.sql.gz 2>/dev/null | head -n 1)"
[ -n "$last_backup" ] && ok "oxirgi backup: $(basename "$last_backup") ($(du -h "$last_backup" | cut -f1))" \
    || note "hali backup yo'q (har 6 soatda olinadi)"
disk="$(df -P / | awk 'NR==2 {print $5}')"
ok "disk: ${disk} band; xotira: $(free -h | awk '/Mem:/ {print $3 " / " $2}')"

head_ "Natija"
if [ "$FAILS" = 0 ]; then ok "hammasi ishlayapti"; else bad "${FAILS} ta muammo"; fi
exit "$FAILS"
