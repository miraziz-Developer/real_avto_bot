#!/bin/bash
# =============================================================================
# Real Avto — Local backup pull script (Mac/development machine)
# Serverdan oxirgi backup ni yuklab oladi.
# Har 6 soatda ishga tushirish uchun launchd bilan ishlatiladi.
# =============================================================================

set -euo pipefail

# --- Konfiguratsiya ---
SERVER_IP="167.172.80.246"
SERVER_USER="root"
SERVER_PASS="mirR@2007aziz"
SERVER_BACKUP_DIR="/opt/backups/real_avto"
LOCAL_BACKUP_DIR="$HOME/Desktop/RealAvto_Backups"
RETAIN_COUNT=30  # Kompyuterda 7.5 kun saqlash

# --- Direktoriya yaratish ---
mkdir -p "$LOCAL_BACKUP_DIR"

echo "[$(date)] Serverdan backup yuklanmoqda..."

# Oxirgi backup fayl nomini aniqlash
LATEST_FILE=$(sshpass -p "$SERVER_PASS" ssh \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "${SERVER_USER}@${SERVER_IP}" \
    "ls -1t ${SERVER_BACKUP_DIR}/real_avto_*.sql.gz 2>/dev/null | head -1")

if [ -z "$LATEST_FILE" ]; then
    echo "[$(date)] XATOLIK: Serverda backup topilmadi!"
    exit 1
fi

FILENAME=$(basename "$LATEST_FILE")
echo "[$(date)] Topildi: $FILENAME"

# Yuklab olish
sshpass -p "$SERVER_PASS" scp \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "${SERVER_USER}@${SERVER_IP}:${LATEST_FILE}" \
    "${LOCAL_BACKUP_DIR}/${FILENAME}"

FILE_SIZE=$(du -h "${LOCAL_BACKUP_DIR}/${FILENAME}" | cut -f1)
echo "[$(date)] Yuklab olindi: ${LOCAL_BACKUP_DIR}/${FILENAME} ($FILE_SIZE)"

# Eskiragan local backuplarni o'chirish
ls -1t "$LOCAL_BACKUP_DIR"/real_avto_*.sql.gz 2>/dev/null | tail -n +$((RETAIN_COUNT + 1)) | while read -r f; do
    echo "[$(date)] Local eskiragan backup o'chirilmoqda: $(basename "$f")"
    rm -f "$f"
done

echo "[$(date)] Jarayon yakunlandi."
