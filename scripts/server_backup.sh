#!/bin/bash
# =============================================================================
# Real Avto — PostgreSQL backup (server-side)
# cron (every 6 hours, keeps the last 14 = 3.5 days):
#   0 */6 * * * /opt/real_avto_bot/scripts/server_backup.sh >> /var/log/real_avto_backup.log 2>&1
# =============================================================================

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BACKUP_DIR="${BACKUP_DIR:-/opt/backups/real_avto}"
DB_NAME="real_avto_konkurs"
DB_USER="postgres"
RETAIN_COUNT="${RETAIN_COUNT:-14}"

mkdir -p "$BACKUP_DIR"
cd "$PROJECT_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/real_avto_${TIMESTAMP}.sql.gz"

echo "[$(date)] Starting backup: $BACKUP_FILE"

# Dump through docker compose, so it works whatever the project (container) name is
if ! docker compose exec -T db pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"; then
    echo "[$(date)] ERROR: pg_dump failed"
    rm -f "$BACKUP_FILE"
    exit 1
fi

# An empty dump means pg_dump failed — keep older backups and fail loudly
if [ "$(gzip -dc "$BACKUP_FILE" | head -c 100 | wc -c)" -lt 100 ]; then
    echo "[$(date)] ERROR: backup is empty, removing it"
    rm -f "$BACKUP_FILE"
    exit 1
fi

FILE_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[$(date)] Backup finished: $BACKUP_FILE ($FILE_SIZE)"

# Remove old backups (keep the last RETAIN_COUNT)
ls -1t "$BACKUP_DIR"/real_avto_*.sql.gz 2>/dev/null | tail -n +$((RETAIN_COUNT + 1)) | while read -r f; do
    echo "[$(date)] Removing old backup: $f"
    rm -f "$f"
done

echo "[$(date)] Backup complete."
