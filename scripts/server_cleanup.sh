#!/bin/bash
# =============================================================================
# Real Avto — kundalik server tozalash (har kuni 04:30, cron). Faqat KERAKSIZ narsalar o'chadi:
#   - hech bir konteyner (ishlab turgan yoki to'xtagan) ishlatmayotgan, 7 kundan eski Docker image'lar;
#   - eski Docker build keshi (oxirgi 1 GB qoladi — keyingi deploy tez bo'lsin);
#   - to'xtab qolgan, 7 kundan eski konteynerlar va ularning keraksiz tarmoqlari;
#   - tizim jurnali 200 MB gacha, apt yuklamalari keshi;
#   - o'zimizning log fayllarimiz 20 MB dan oshsa — oxirgi 5000 qatori qoladi.
# TEGILMAYDI: ishlab turgan konteynerlar va ularning image'lari, ma'lumot (volume), baza backup'lari, .env.
#
#   30 4 * * * /opt/real_avto_bot/scripts/server_cleanup.sh >> /var/log/real_avto_cleanup.log 2>&1
# =============================================================================

set -uo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
used() { df -P "$PROJECT_DIR" | awk 'NR==2 {print $5}'; }
before="$(used)"

docker container prune -f --filter "until=168h" > /dev/null 2>&1 || true
docker image prune -af --filter "until=168h" > /dev/null 2>&1 || true
docker builder prune -f --keep-storage 1GB > /dev/null 2>&1 || docker builder prune -f --filter "until=72h" > /dev/null 2>&1 || true
docker network prune -f --filter "until=168h" > /dev/null 2>&1 || true

sudo -n journalctl --vacuum-size=200M > /dev/null 2>&1 || true
sudo -n apt-get clean > /dev/null 2>&1 || true

for f in /var/log/real_avto_watchdog.log /var/log/real_avto_backup.log /var/log/real_avto_cleanup.log; do
    if [ -f "$f" ] && [ "$(stat -c %s "$f" 2>/dev/null || echo 0)" -gt $((20 * 1024 * 1024)) ]; then
        tail -n 5000 "$f" > "$f.tmp" && cat "$f.tmp" > "$f" && rm -f "$f.tmp"
    fi
done

echo "[$(date)] tozalash: disk ${before} → $(used)"
