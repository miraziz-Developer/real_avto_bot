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

# --- HTTPS and webhooks -----------------------------------------------------------------
head_ "HTTPS va webhooklar"
IG_PORT="$(env_value .env IG_WEBHOOK_HOST_PORT)"; IG_PORT="${IG_PORT:-8081}"
if [ -n "$(env_value .env MANYCHAT_SECRET)" ]; then
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST "http://127.0.0.1:${IG_PORT}/manychat/instagram")"
    [ "$code" = 401 ] && ok "ManyChat endpoint ishlayapti (kalitsiz so'rov rad etildi)" || bad "ManyChat endpoint: HTTP $code"
else
    note "MANYCHAT_SECRET yo'q — ManyChat endpoint o'chiq"
fi
for key in CATALOG_URL CRM_URL; do
    url="$(env_value .env "$key")"
    [ -n "$url" ] || { note "$key bo'sh"; continue; }
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 20 "$url/")"
    [ "$code" = 200 ] && ok "$key $url" || bad "$key $url → HTTP $code"
done

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

# --- Telegram Mini App -------------------------------------------------------------------
head_ "Mini App"
[ -n "$(env_value .env BOT_USERNAME)" ] && ok "BOT_USERNAME: @$(env_value .env BOT_USERNAME)" || bad "BOT_USERNAME bo'sh — katalogdagi «Savol berish» tugmalari ishlamaydi"
menu="$(curl -s -m 15 "$TG/getChatMenuButton" | json 'd["result"].get("web_app",{}).get("url","")')"
[[ "$menu" == https://* ]] && ok "bot menyusi: 🚗 Katalog → $menu" || bad "bot menyu tugmasi Mini App emas (${menu:-yo‘q})"
ADMIN_ID="$(env_value .env ADMIN_TELEGRAM_IDS | tr ',;' '  ' | awk '{print $1}' || true)"
if [ -n "$ADMIN_ID" ]; then
    amenu="$(curl -s -m 15 "$TG/getChatMenuButton" --data-urlencode "chat_id=$ADMIN_ID" | json 'd["result"].get("web_app",{}).get("url","")')"
    [[ "$amenu" == https://* ]] && ok "admin menyusi: 🛠 Admin panel → $amenu" || note "admin menyusida CRM yo'q (${amenu:-—})"
    # Telegram imzolagan initData'ni taqlid qilamiz: admin CRM'ga parolsiz kira oladimi
    init_data="$(BOT_TOKEN="$BOT_TOKEN" CHECK_ADMIN="$ADMIN_ID" python3 -c '
import hashlib, hmac, json, os, time, urllib.parse
user = json.dumps({"id": int(os.environ["CHECK_ADMIN"]), "first_name": "Check"}, separators=(",", ":"))
fields = {"auth_date": str(int(time.time())), "query_id": "check", "user": user}
check = chr(10).join(f"{k}={v}" for k, v in sorted(fields.items()))
secret = hmac.new(b"WebAppData", os.environ["BOT_TOKEN"].encode(), hashlib.sha256).digest()
fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
print(urllib.parse.urlencode(fields))
')"
    body="$(python3 -c 'import json,sys; print(json.dumps({"init_data": sys.argv[1]}))' "$init_data")"
    tok="$(curl -s -m 10 -H 'Content-Type: application/json' -d "$body" "http://127.0.0.1:${CRM_PORT}/api/auth/telegram" | json 'd.get("token","")')"
    [ -n "$tok" ] && ok "CRM Mini App: admin Telegram orqali parolsiz kiradi" || bad "CRM Mini App: Telegram orqali kirish ishlamadi"
fi
code="$(curl -s -o /dev/null -w '%{http_code}' -m 10 -H 'Content-Type: application/json' -d '{"brand":"Chevrolet"}' "http://127.0.0.1:${CATALOG_PORT}/api/public/alerts")"
[ "$code" = 401 ] && ok "katalog «Xabar bering»: Telegram imzosisiz rad etiladi" || bad "katalog /alerts: HTTP $code"

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
# Every "docker compose exec" gets </dev/null: when this script is piped ("bash -s") it would swallow the rest of it
psql() { docker compose exec -T db psql -U postgres -d real_avto_konkurs -At -c "$1" 2>/dev/null < /dev/null; }
cars="$(psql "select status, count(*) from cars group by status order by status" | tr '\n' ' ')"
[ -n "$cars" ] && ok "mashinalar: ${cars}" || note "mashinalar jadvali bo'sh"
ok "leadlar: $(psql 'select count(*) from leads' || echo '?'), foydalanuvchilar: $(psql 'select count(*) from users' || echo '?')"
last="$(psql "select '#' || id || ' ' || coalesce(model,'?') || ' ' || coalesce(year::text,'') || ' · ' || status || ' · xabarlar ' || array_to_string(channel_message_ids, ',') || ' · tinglangan video ' || (length(raw_text) - length(replace(raw_text, '[Ovoz]', ''))) / 6 || ' · rang ' || coalesce(color,'-') from cars where source = 'channel' order by id desc limit 3" | tr '\n' ';')"
ok "oxirgi kanal mashinalari: ${last:-yo‘q}"
fixes="$(psql "select '#' || car_id || ' ' || kind || ' ' || coalesce(data::text,'') from car_events where created_at > now() - interval '1 day' and (kind = 'auto_fix' or data::text like '%nasiya_down_payment%' or data::text like '%merged_car%') order by id" | tr '\n' ';')"
[ -n "$fixes" ] && note "bazadagi tuzatishlar (oxirgi 1 kun): ${fixes}"
note "sotuvdagi kanal mashinalari: $(psql "select string_agg('#' || id || ' ' || coalesce(model,'?') || ' ' || coalesce(year::text,'') || ' narx ' || coalesce(price_usd::text,'-'), ', ' order by id) from cars where status = 'active'")"
ig="$(psql "select coalesce((select left(value::jsonb->>'since', 16) || ' · postlar: ' || (select count(*) from jsonb_object_keys(value::jsonb->'counts')) || ' · kutilmoqda: ' || (case jsonb_typeof(value::jsonb->'pending') when 'object' then (select count(*) from jsonb_object_keys(value::jsonb->'pending')) when 'array' then jsonb_array_length(value::jsonb->'pending') else 0 end) from app_meta where key = 'ig_poll_state'), 'yo''q')")"
ok "Instagram tekshiruvi: holat ${ig:-?} · javob berilgan kommentlar: $(psql 'select count(*) from instagram_comments' || echo 0)"

# --- Bot log ----------------------------------------------------------------------------------
head_ "Bot logi (oxirgi 1 soat)"
errors="$(docker compose logs --since 1h bot 2>/dev/null | grep -E ' ERROR |Traceback|CRITICAL' | grep -v 'TelegramConflictError' | tail -n 10)"
[ -z "$errors" ] && ok "xato yo'q" || { bad "xatolar bor:"; printf '%s\n' "$errors" | cut -c1-240; }
hooks="$(docker compose logs --since 3h bot 2>/dev/null | grep -E 'webhooks/make|manychat/instagram' | tail -n 6)"
[ -n "$hooks" ] && { note "Instagram so'rovlari (oxirgi 3 soat):"; printf '%s\n' "$hooks" | cut -c1-200; }
sync="$(docker compose logs --since 6h bot 2>/dev/null | grep -E 'Kanal sinxronizatsiyasi|Kunlik hisobot|arxivlan|IG poll|IG javob|Instagram emoji|ManyChat komment|tuzatildi|qayta tinglandi|Traceback' | tail -n 6)"
[ -n "$sync" ] && { ok "avtomatik kanal kuzatuvi:"; printf '%s\n' "$sync" | cut -c1-240; } || note "kanal sinxronizatsiyasi logi yo'q"

# --- Ops -------------------------------------------------------------------------------------------
head_ "Backup, cron, resurslar"
crontab -l 2>/dev/null | grep -q real_avto_backup.log && ok "backup cron o'rnatilgan" || bad "backup cron yo'q"
crontab -l 2>/dev/null | grep -q real_avto_watchdog.log && ok "watchdog cron o'rnatilgan" || bad "watchdog cron yo'q"
systemctl is-enabled real-avto-stack.service > /dev/null 2>&1 && ok "systemd: avtomatik ishga tushish yoqilgan" \
    || bad "systemd unit yoqilmagan"
if [ "${RUN_BACKUP:-0}" = 1 ]; then
    bash scripts/server_backup.sh > /tmp/real_avto_check_backup.log 2>&1 < /dev/null && ok "backup qo'lda ishga tushirildi" \
        || bad "backup xatosi: $(tail -n 2 /tmp/real_avto_check_backup.log)"
fi
last_backup="$(ls -1t /opt/backups/real_avto/*.sql.gz 2>/dev/null | head -n 1)"
[ -n "$last_backup" ] && ok "oxirgi backup: $(basename "$last_backup") ($(du -h "$last_backup" | cut -f1))" \
    || note "hali backup yo'q (har 6 soatda olinadi)"
disk="$(df -P / | awk 'NR==2 {print $5}')"
ok "disk: ${disk} band; xotira: $(free -h | awk '/Mem:/ {print $3 " / " $2}')"
note "docker joyi: $(docker system df --format '{{.Type}} {{.Size}} (bo‘shatsa bo‘ladi {{.Reclaimable}})' 2>/dev/null | tr '\n' ';')"
note "katta papkalar: $(sudo -n du -xsh /var/lib/docker /var/log /opt/backups /home 2>/dev/null | tr '\t\n' ' ;')"

# --- Network (how the server is exposed) -------------------------------------------------------------
head_ "Tarmoq"
if command -v ufw > /dev/null; then note "ufw: $(sudo -n ufw status 2>/dev/null | head -n 1 || echo '?')"; fi
note "80/443 ni tinglayotgan: $(sudo -n ss -ltnpH 2>/dev/null | awk '$4 ~ /:(80|443)$/ {print $4, $6}' | tr '\n' ' ' || true)"
for d in /etc/nginx/sites-enabled /etc/nginx/conf.d; do
    [ -d "$d" ] && note "nginx $d: $(grep -rhoE 'server_name[^;]+' "$d" 2>/dev/null | sort -u | tr '\n' ' ')"
done
command -v caddy > /dev/null && note "caddy o'rnatilgan"
note "docker portlari: $(docker ps --format '{{.Names}} {{.Ports}}' | grep -E ':(80|443)->' | tr '\n' ' ')"
[ -f /opt/bozorliii/docker-compose.prod.yml ] && note "bozorliii domenlari: $(grep -rhoE '[a-z0-9.-]+\.(uz|com|net|org|app|io|dev)' /opt/bozorliii/docker-compose.prod.yml /opt/bozorliii/nginx* /opt/bozorliii/*.conf /opt/bozorliii/Caddyfile 2>/dev/null | sort -u | head -n 10 | tr '\n' ' ')"

if [ -f /opt/bozorliii/docker-compose.prod.yml ]; then
    note "bozorliii portlari: $(grep -E '^\s+- "?[0-9.:]*[0-9]+:[0-9]+' /opt/bozorliii/docker-compose.prod.yml | tr -d ' "' | tr '\n' ' ')"
    note "bozorliii xizmatlari: $(docker compose -f /opt/bozorliii/docker-compose.prod.yml config --services 2>/dev/null < /dev/null | tr '\n' ' ')"
fi
note "ufw qoidalari: $(sudo -n ufw status 2>/dev/null | awk 'NR>4 && /ALLOW/ {print $1}' | sort -u | tr '\n' ' ')"

head_ "Natija"
if [ "$FAILS" = 0 ]; then ok "hammasi ishlayapti"; else bad "${FAILS} ta muammo"; fi
exit "$FAILS"
