#!/bin/bash
# =============================================================================
# Real Avto — PostgreSQL backup script (server-side)
# Runs every 6 hours and keeps the last 14 backups (3.5 days)
# =============================================================================

set -euo pipefail

BACKUP_DIR="/opt/backups/real_avto"
DB_NAME="real_avto_konkurs"
DB_USER="postgres"
DB_CONTAINER="real_avto_konkurs-db-1"
RETAIN_COUNT=14

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/real_avto_${TIMESTAMP}.sql.gz"

echo "[$(date)] Starting backup: $BACKUP_FILE"

# PostgreSQL dump (inside the container)
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"

FILE_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[$(date)] Backup finished: $BACKUP_FILE ($FILE_SIZE)"

# Remove old backups (keep the last RETAIN_COUNT)
ls -1t "$BACKUP_DIR"/real_avto_*.sql.gz 2>/dev/null | tail -n +$((RETAIN_COUNT + 1)) | while read -r f; do
    echo "[$(date)] Removing old backup: $f"
    rm -f "$f"
done

echo "[$(date)] Backup complete."
