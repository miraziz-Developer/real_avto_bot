#!/bin/bash
# =============================================================================
# Real Avto — Local backup pull script (Mac/development machine)
# Downloads the latest backup from the server.
# Scheduled every 6 hours with launchd.
# =============================================================================

set -euo pipefail

# --- Configuration ---
SERVER_IP="167.172.80.246"
SERVER_USER="root"
SERVER_PASS="mirR@2007aziz"
SERVER_BACKUP_DIR="/opt/backups/real_avto"
LOCAL_BACKUP_DIR="$HOME/Desktop/RealAvto_Backups"
RETAIN_COUNT=30  # keep 7.5 days locally

# --- Create the directory ---
mkdir -p "$LOCAL_BACKUP_DIR"

echo "[$(date)] Downloading backup from the server..."

# Find the latest backup file name
LATEST_FILE=$(sshpass -p "$SERVER_PASS" ssh \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "${SERVER_USER}@${SERVER_IP}" \
    "ls -1t ${SERVER_BACKUP_DIR}/real_avto_*.sql.gz 2>/dev/null | head -1")

if [ -z "$LATEST_FILE" ]; then
    echo "[$(date)] ERROR: no backup found on the server!"
    exit 1
fi

FILENAME=$(basename "$LATEST_FILE")
echo "[$(date)] Found: $FILENAME"

# Download
sshpass -p "$SERVER_PASS" scp \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "${SERVER_USER}@${SERVER_IP}:${LATEST_FILE}" \
    "${LOCAL_BACKUP_DIR}/${FILENAME}"

FILE_SIZE=$(du -h "${LOCAL_BACKUP_DIR}/${FILENAME}" | cut -f1)
echo "[$(date)] Downloaded: ${LOCAL_BACKUP_DIR}/${FILENAME} ($FILE_SIZE)"

# Remove old local backups
ls -1t "$LOCAL_BACKUP_DIR"/real_avto_*.sql.gz 2>/dev/null | tail -n +$((RETAIN_COUNT + 1)) | while read -r f; do
    echo "[$(date)] Removing old local backup: $(basename "$f")"
    rm -f "$f"
done

echo "[$(date)] Done."
