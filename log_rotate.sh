#!/bin/bash
# log_rotate.sh - Daily log rotation and monthly archival
#   - Keeps daily logs for 30 days
#   - Archives older logs into monthly .tar.gz files
#   - Runs from cron on the 1st of each month

set -euo pipefail

LOG_DIR="/home/simon0099/Lodon/logs"
ARCHIVE_DIR="$LOG_DIR/archive"
KEEP_DAYS=30

mkdir -p "$ARCHIVE_DIR"

# Archive logs older than 30 days, grouped by YYYY-MM
find "$LOG_DIR" -maxdepth 1 -name "*.log" -mtime +$KEEP_DAYS -print0 | while IFS= read -r -d '' f; do
    MONTH=$(date -r "$f" '+%Y-%m')
    STAGING="$ARCHIVE_DIR/.staging-$MONTH"
    mkdir -p "$STAGING"
    mv "$f" "$STAGING/"
done

# Compress each month's staging dir into a tar.gz
for STAGING in "$ARCHIVE_DIR"/.staging-*; do
    [ -d "$STAGING" ] || continue
    MONTH=$(basename "$STAGING" | sed 's/.staging-//')
    TARBALL="$ARCHIVE_DIR/logs-$MONTH.tar.gz"

    if [ -f "$TARBALL" ]; then
        # Append to existing archive: extract, merge, recompress
        TMP=$(mktemp -d)
        tar xzf "$TARBALL" -C "$TMP"
        mv "$STAGING"/* "$TMP/"
        tar czf "$TARBALL" -C "$TMP" .
        rm -rf "$TMP"
    else
        tar czf "$TARBALL" -C "$STAGING" .
    fi
    rm -rf "$STAGING"
done

echo "$(date '+%Y-%m-%d %H:%M:%S') Log rotation complete"
