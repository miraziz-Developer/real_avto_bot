#!/bin/bash
# =============================================================================
# Real Avto — N kunlik ish tahlili (read-only, sirlar chiqmaydi). Natija ochiq Actions logiga chiqadi, shuning uchun
# shaxsiy ma'lumot berkitiladi: telefonlar, uzun raqamlar (Telegram ID), @username'lar; ismlar umuman olinmaydi,
# mijoz xabarlari qisqartiriladi.
#
#   DAYS=2 bash -s < scripts/analysis_report.sh      (loyiha papkasida)
# =============================================================================

set -uo pipefail
[ -f "$0" ] && cd "$(cd "$(dirname "$0")/.." && pwd)"
DAYS="${DAYS:-2}"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

q() { docker compose exec -T db psql -U postgres -d real_avto_konkurs -P pager=off -c "$1" < /dev/null 2>&1; }
since="now() - interval '${DAYS} days'"

{
echo "### Davr: oxirgi ${DAYS} kun, server vaqti $(date -u +%FT%TZ)"
echo "### Konteynerlar"; docker compose ps --format '{{.Service}} {{.State}} {{.Status}}'

echo "### Mashinalar holati"; q "select status, source, count(*) from cars group by 1,2 order by 1,2"
echo "### Davrda yaratilgan/yangilangan mashinalar"
q "select id, status, source, brand, model, year, price_usd, mileage_km, color, ai_confidence, published_at, created_at, updated_at, channel_message_ids, left(replace(raw_text, E'\n', ' | '), 900) raw from cars where created_at > $since or updated_at > $since order by id"
echo "### Mashina hodisalari"; q "select e.id, e.car_id, e.kind, e.data, e.actor_telegram_id, e.created_at from car_events e where e.created_at > $since order by e.id"
echo "### Hodisalar turi bo'yicha"; q "select kind, count(*) from car_events where created_at > $since group by 1 order by 2 desc"

echo "### Leadlar"; q "select id, channel, status, score, car_id, budget_usd, wants, handoff_reason, human_mode, handed_off_at, created_at, last_message_at, left(summary, 300) from leads where created_at > $since or last_message_at > $since order by id"
echo "### Suhbatlar"; q "select m.lead_id, m.role, m.created_at, left(replace(m.content, E'\n', ' | '), 220) from agent_messages m where m.created_at > $since order by m.lead_id, m.id"
echo "### Mijozlar (clients)"; q "select id, source, status, created_at from clients where created_at > $since order by id"
echo "### Wishlist"; q "select count(*) from wishlist where created_at > $since"
echo "### Bot orqali e'lonlar"; q "select id, status, created_at from listing_submissions where created_at > $since order by id"
echo "### Kanal kommentlar threadlari"; q "select count(*) from channel_threads where created_at > $since"
echo "### Instagram javob berilgan kommentlar"; q "select media_id, count(*), min(created_at), max(created_at) from instagram_comments where created_at > $since group by 1 order by 3"
echo "### Instagram postlar"; q "select id, ref, car_id, source, left(caption, 120), created_at from instagram_posts order by id desc limit 40"
echo "### AppMeta"; q "select key, left(value, 1500) from app_meta where key not ilike '%token%' and key not ilike '%secret%' and key not ilike '%key%' order by key"
echo "### Yangi foydalanuvchilar"; q "select count(*) from users where created_at > $since"

echo "### BOT LOGI (${DAYS} kun, to'liq)"
docker compose logs --no-color --since "$((DAYS * 24))h" bot 2>&1 | grep -v -E 'Update id=.* is handled|keep.?alive' | tail -n 20000
echo "### BACKEND LOGI (xatolar)"
docker compose logs --no-color --since "$((DAYS * 24))h" backend 2>&1 | grep -iE 'error|warn|exception' | tail -n 300
} > "$OUT/report.txt" 2>&1

# Shaxsiy ma'lumotni berkitish: telefonlar, 9+ xonali raqamlar (Telegram ID), @username
sed -E \
    -e 's/Bearer [^ "&]+/Bearer [yashirin]/Ig' \
    -e 's/[0-9]{6,}:[A-Za-z0-9_-]{30,}/[token]/g' \
    -e 's/((key|token|secret|password)["=: ]+)[^ &"]+/\1[yashirin]/Ig' \
    -e 's/(AIza|gsk_|EAA|IGQ)[A-Za-z0-9_-]{10,}/[kalit]/g' \
    -e 's/\+?998[ ()0-9-]{9,16}[0-9]/[tel]/g' \
    -e 's/\b[0-9]{2}[ -]?[0-9]{3}[ -]?[0-9]{2}[ -]?[0-9]{2}\b/[tel]/g' \
    -e 's/\b-?[0-9]{9,}\b/[id]/g' \
    -e 's/@[A-Za-z0-9_]{4,}/@[user]/g' \
    "$OUT/report.txt"
