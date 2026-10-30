#!/bin/sh
set -e

# Save Docker env vars so cron jobs can read them later
printenv | grep -E '^(MONGO_URI|MONGO_DB|TZ)=' > /app/cron.env || true

crontab /app/crontab

echo "[scheduler] Cron installed:"
crontab -l

echo "[scheduler] Running once at startup..."
/bin/sh /app/run_all.sh >> /app/output/cron.log 2>&1 || true

echo "[scheduler] Starting cron..."
exec cron -f