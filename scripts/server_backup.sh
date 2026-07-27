#!/bin/bash
# =============================================================================
# Real Avto — PostgreSQL backup script (server-side)
# Har 6 soatda ishga tushadi, oxirgi 14 ta backup ni saqlaydi (3.5 kun)
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

echo "[$(date)] Backup boshlanmoqda: $BACKUP_FILE"

# PostgreSQL dump (container ichidan)
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"

FILE_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[$(date)] Backup tugadi: $BACKUP_FILE ($FILE_SIZE)"

# Eskiraganlarni o'chirish (oxirgi RETAIN_COUNT ta qoldirish)
ls -1t "$BACKUP_DIR"/real_avto_*.sql.gz 2>/dev/null | tail -n +$((RETAIN_COUNT + 1)) | while read -r f; do
    echo "[$(date)] Eskiragan backup o'chirilmoqda: $f"
    rm -f "$f"
done

echo "[$(date)] Backup jarayoni yakunlandi."
