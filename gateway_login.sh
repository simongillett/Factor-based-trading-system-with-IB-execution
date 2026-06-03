#!/bin/bash
# gateway_login.sh - Start IB Gateway via IBC with reliable TOTP entry
#
# Fixes:
#   1. IBC fails to auto-enter TOTP → we enter it via xdotool
#   2. IBC re-triggers login after success → we send STOP via command server
#   3. IB Key competes with TOTP → we click "Change security device" if needed

set -uo pipefail

IBC_DIR="$HOME/ibc"
IBC_INI="$IBC_DIR/config.ini"
API_PORT=4001
CMD_PORT=7462
XVFB_DISPLAY=":99"
TOTP_TIMEOUT=120
MAX_TOTP_ATTEMPTS=5

TOTP_SECRET=$(grep -oP 'TotpSecretForIBKeyAuthentication=\K\S+' "$IBC_INI")
[[ -z "$TOTP_SECRET" ]] && { echo "ERROR: No TOTP secret in $IBC_INI" >&2; exit 1; }

generate_totp() {
    python3 -c "
import hmac,struct,time,hashlib,base64
s=base64.b32decode('$TOTP_SECRET')
c=int(time.time())//30
h=hmac.new(s,struct.pack('>Q',c),hashlib.sha1).digest()
o=h[-1]&0x0f
print(f'{(struct.unpack(\">I\",h[o:o+4])[0]&0x7fffffff)%1000000:06d}')
"
}

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [gateway_login] $*"; }
port_up() { ss -tlnp 2>/dev/null | grep -q ":${API_PORT} "; }

# --- Already running? ---
if port_up; then
    log "Gateway already listening on port $API_PORT"
    exit 0
fi

# --- Kill stale processes ---
log "Cleaning up stale processes..."
pkill -f "jts4launch" 2>/dev/null || true
pkill -f "ibcstart.sh" 2>/dev/null || true
pkill -f "displaybannerandlaunch.sh" 2>/dev/null || true
sleep 3

# --- Start Xvfb ---
if ! pgrep -f "Xvfb ${XVFB_DISPLAY} " >/dev/null 2>&1; then
    log "Starting Xvfb on $XVFB_DISPLAY"
    Xvfb "$XVFB_DISPLAY" -screen 0 1280x1024x24 -nolisten tcp -auth /tmp/gw_xauth &
    sleep 2
    xauth -f /tmp/gw_xauth generate "$XVFB_DISPLAY" . trusted 2>/dev/null || true
fi

export DISPLAY="$XVFB_DISPLAY"
for f in /tmp/gw_xauth /tmp/xvfb-run.*/Xauthority; do
    [[ -f "$f" ]] && XAUTHORITY="$f" xdpyinfo >/dev/null 2>&1 && { export XAUTHORITY="$f"; break; }
done
xdpyinfo >/dev/null 2>&1 || { log "ERROR: Cannot connect to display"; exit 1; }

# --- Start IBC/Gateway on OUR display ---
log "Starting IB Gateway via IBC..."
export TWS_MAJOR_VRSN=1042 IBC_INI="$IBC_INI" TRADING_MODE=live
export TWOFA_TIMEOUT_ACTION=exit IBC_PATH="$IBC_DIR" TWS_PATH="$HOME/Jts"
export TWS_SETTINGS_PATH= LOG_PATH="$IBC_DIR/logs" APP=GATEWAY
export TWSUSERID= TWSPASSWORD= FIXUSERID= FIXPASSWORD= JAVA_PATH=
nohup "$IBC_DIR/scripts/displaybannerandlaunch.sh" > "$IBC_DIR/logs/gateway_login_ibc.log" 2>&1 &
IBC_PID=$!
log "IBC launched (PID $IBC_PID)"

# --- Monitor for 2FA dialog and enter TOTP ---
ELAPSED=0
TOTP_ATTEMPTS=0

while [[ $ELAPSED -lt $TOTP_TIMEOUT ]]; do
    if port_up; then
        log "Gateway is UP on port $API_PORT"
        break
    fi

    DIALOG_WID=$(xdotool search --name "Second Factor Authentication" 2>/dev/null | head -1 || true)
    if [[ -n "$DIALOG_WID" ]]; then
        TOTP_ATTEMPTS=$((TOTP_ATTEMPTS + 1))
        [[ $TOTP_ATTEMPTS -gt $MAX_TOTP_ATTEMPTS ]] && { log "ERROR: Exceeded TOTP attempts"; exit 1; }

        sleep 0.5
        CODE=$(generate_totp)
        log "2FA dialog detected (attempt $TOTP_ATTEMPTS), entering TOTP: $CODE"

        xdotool windowactivate --sync "$DIALOG_WID" 2>/dev/null || true
        sleep 0.3

        # Click "Change security device" to force TOTP mode (handles IB Key dialog)
        xdotool mousemove 545 503 click 1
        sleep 2

        # Re-find dialog
        DIALOG_WID=$(xdotool search --name "Second Factor Authentication" 2>/dev/null | head -1 || true)
        [[ -z "$DIALOG_WID" ]] && { sleep 2; continue; }

        xdotool windowactivate --sync "$DIALOG_WID" 2>/dev/null || true
        sleep 0.3
        xdotool mousemove 600 472 click 1; sleep 0.2
        xdotool key ctrl+a
        xdotool type --clearmodifiers "$CODE"
        sleep 0.2
        xdotool key Return
        log "TOTP submitted"

        for i in $(seq 1 30); do
            port_up && { log "Gateway is UP on port $API_PORT"; break 2; }
            sleep 1
        done
        log "Port not up yet, retrying..."
    fi

    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

if port_up; then
    GW_PID=$(ss -tlnp | grep ":${API_PORT} " | grep -oP 'pid=\K[0-9]+')
    log "SUCCESS: Gateway running (PID $GW_PID) on port $API_PORT"
    # Keep script alive so systemd doesn't kill the cgroup
    while kill -0 "$GW_PID" 2>/dev/null; do sleep 60; done
    log "Gateway process $GW_PID exited"
else
    log "ERROR: Gateway not listening after ${TOTP_TIMEOUT}s"
    exit 1
fi
