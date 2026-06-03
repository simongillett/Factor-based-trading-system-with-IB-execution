#!/bin/bash
# Pre-market IB Gateway healthcheck
# Runs at 9:00 AM weekdays, 25 minutes before the engine restarts at 9:25 AM
# Verifies the gateway is reachable on port 4001; restarts if not.

LOG=/opt/trading/logs/healthcheck.log
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

check_gateway() {
    # Try a TCP connect to port 4001; timeout after 5 seconds
    timeout 5 bash -c 'echo > /dev/tcp/127.0.0.1/4001' 2>/dev/null
    return $?
}

if check_gateway; then
    echo "$TIMESTAMP [OK] IB Gateway is reachable on :4001" >> "$LOG"
    exit 0
fi

echo "$TIMESTAMP [WARN] IB Gateway not reachable on :4001 — restarting ibgateway.service" >> "$LOG"
systemctl restart ibgateway.service

# Wait up to 90 seconds for gateway to come back
for i in $(seq 1 18); do
    sleep 5
    if check_gateway; then
        echo "$TIMESTAMP [OK] IB Gateway recovered after $((i * 5))s" >> "$LOG"
        exit 0
    fi
done

echo "$TIMESTAMP [ERROR] IB Gateway did not recover within 90 seconds — manual intervention required" >> "$LOG"
exit 1
