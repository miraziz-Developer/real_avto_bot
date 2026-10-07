#!/bin/bash
# =============================================================================
# Real Avto — watchdog (server-side). Checks the containers and the disk, restarts an
# unhealthy bot and tells the admins in Telegram (only when the state changes).
# cron (every 5 minutes):
#   */5 * * * * /opt/real_avto_bot/scripts/watchdog.sh >> /var/log/real_avto_watchdog.log 2>&1
# =============================================================================

set -uo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
STATE_FILE="${STATE_FILE:-/tmp/real_avto_watchdog.state}"
DISK_LIMIT_PERCENT="${DISK_LIMIT_PERCENT:-90}"
cd "$PROJECT_DIR"

env_value() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- | tr -d '"'"'"; }
BOT_TOKEN="$(env_value BOT_TOKEN)"
ADMINS="$(env_value ADMIN_TELEGRAM_IDS | tr ',' ' ')"

notify() {
    [ -n "$BOT_TOKEN" ] || return 0
    for id in $ADMINS; do
        curl -s -m 15 "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${id}" --data-urlencode "text=$1" > /dev/null || true
    done
}

problems=""
for svc in db redis backend bot; do
    status="$(docker compose ps --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null | awk -v s="$svc" '$1==s {print $2, $3}')"
    case "$status" in
        "running healthy" | "running " | "running") ;;
        "running unhealthy")
            problems+="${svc}: unhealthy"$'\n'
            # Docker does not restart unhealthy containers by itself
            [ "$svc" = "bot" ] && docker compose restart bot > /dev/null 2>&1 && problems+="  → bot restarted"$'\n'
            ;;
        *) problems+="${svc}: ${status:-not running}"$'\n' ;;
    esac
done

disk="$(df -P "$PROJECT_DIR" | awk 'NR==2 {gsub("%", "", $5); print $5}')"
if [ -n "$disk" ] && [ "$disk" -ge "$DISK_LIMIT_PERCENT" ]; then
    problems+="disk: ${disk}% used"$'\n'
fi

previous="$(cat "$STATE_FILE" 2>/dev/null || true)"
if [ -n "$problems" ]; then
    echo "[$(date)] PROBLEM"$'\n'"$problems"
    if [ "$problems" != "$previous" ]; then
        notify "⚠️ Real Avto server: muammo"$'\n'"${problems}"
    fi
    printf '%s' "$problems" > "$STATE_FILE"
elif [ -n "$previous" ]; then
    echo "[$(date)] recovered"
    notify "✅ Real Avto server: hammasi yana ishlayapti"
    rm -f "$STATE_FILE"
fi
