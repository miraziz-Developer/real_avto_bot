#!/bin/bash
# =============================================================================
# Real Avto — change public settings in the server .env and restart the bot (no secrets here).
#
#   bash scripts/env_update.sh CHANNEL_ID=-100123 CHANNEL_USERNAME=realavto [--archive-old-channel]
#
# Only the keys in ALLOWED can be changed. --archive-old-channel archives the cars that are on sale
# from the previous CHANNEL_ID (test channel), so they do not show up in the catalog and AI answers.
# =============================================================================

set -euo pipefail
[ -f "$0" ] && cd "$(cd "$(dirname "$0")/.." && pwd)"
[ -f docker-compose.yml ] || { echo "docker-compose.yml topilmadi: $(pwd)"; exit 1; }

ALLOWED=" CHANNEL_ID CHANNEL_USERNAME DISCUSSION_GROUP_ID LEADERBOARD_CHANNEL_ID REVIEWS_CHANNEL_ID INSTAGRAM_USERNAME BUSINESS_NAME BUSINESS_ADDRESS BUSINESS_HOURS SALES_PHONE REAL_AVTO_MAP_URL "

get_env() { { grep -E "^$1=" .env || true; } | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'; }
set_env() {
    local key="$1" value="$2" tmp
    tmp="$(mktemp)"
    if grep -qE "^$key=" .env; then
        awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' .env > "$tmp"
        cat "$tmp" > .env
    else
        printf '%s=%s\n' "$key" "$value" >> .env
    fi
    rm -f "$tmp"
}

ARCHIVE=0
OLD_CHANNEL="$(get_env CHANNEL_ID)"
for arg in "$@"; do
    if [ "$arg" = "--archive-old-channel" ]; then ARCHIVE=1; continue; fi
    key="${arg%%=*}"; value="${arg#*=}"
    [[ "$ALLOWED" == *" $key "* ]] || { echo "Ruxsat yo'q: $key"; exit 1; }
    [[ "$value" != *$'\n'* ]] || { echo "Noto'g'ri qiymat: $key"; exit 1; }
    echo "$key: '$(get_env "$key")' → '$value'"
    set_env "$key" "$value"
done

if [ "$ARCHIVE" = 1 ] && [ -n "$OLD_CHANNEL" ] && [[ "$OLD_CHANNEL" =~ ^-?[0-9]+$ ]] && [ "$OLD_CHANNEL" != "$(get_env CHANNEL_ID)" ]; then
    n="$(docker compose exec -T db psql -U postgres -d real_avto_konkurs -At -c \
        "with a as (update cars set status='archived' where channel_chat_id=$OLD_CHANNEL and status in ('active','reserved','review') returning 1) select count(*) from a" < /dev/null)"
    echo "Eski kanal ($OLD_CHANNEL) mashinalari arxivlandi: $n"
fi

docker compose up -d --force-recreate bot backend < /dev/null
sleep 25
docker compose ps bot backend
docker compose logs --tail=25 bot < /dev/null | grep -E "kanal|Kanal|ERROR|polling" || true
