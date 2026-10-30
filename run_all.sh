#!/bin/sh

echo "Cron identity: $(id)"
if [ -r /host-logs/asl/2026.10.27.G80.asl ]; then
  echo "ASL file readable"
else
  echo "ASL file not readable"
fi

# cron runs with an empty environment, so load the saved Docker env first
if [ -f /app/cron.env ]; then
  set -a
  . /app/cron.env
  set +a
fi

export MONGO_URI=${MONGO_URI:-mongodb://db:27017}
export MONGO_DB=${MONGO_DB:-health_monitor}
export PATH=/usr/local/bin:/usr/bin:/bin

echo "========================================"
echo "[$(date -Iseconds)] Cron run starting"

python -c 'p="/host-logs/asl/2026.10.27.G80.asl"; f=open(p,"rb"); f.read(1); f.close(); print("Python open succeeded")' \
  || echo "Python open failed"

echo "[$(date -Iseconds)] Collecting logs..."
python /app/chuck.py \
  --path /host-logs \
  --hours 1 \
  --output /app/data/inbox/logs.json

echo "[$(date -Iseconds)] Running health check..."
python /app/chip.py \
  --prompt system_health \
  --timeout 60 \
  --output /app/data/inbox/health.json

echo "[$(date -Iseconds)] Cron run finished"