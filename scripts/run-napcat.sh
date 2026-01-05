#!/usr/bin/env bash
set -euo pipefail

# Ensure display env
export DISPLAY="${DISPLAY:-:1}"
export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"

LOG_DIR="/home/user/logs"
mkdir -p "${LOG_DIR}"

# Prefer /app layout to match official images
export HOME="/app"
export XDG_CONFIG_HOME="/app/.config"
mkdir -p /app/.config/QQ /app/napcat/config || true

# SOCKS5 proxy forwarding via xray-core
# Environment variables: PROXY_SOCKS5_HOST, PROXY_SOCKS5_PORT, PROXY_SOCKS5_USER, PROXY_SOCKS5_PASS
XRAY_LOCAL_PORT=10800
if [ -n "${PROXY_SOCKS5_HOST:-}" ] && [ -n "${PROXY_SOCKS5_PORT:-}" ]; then
  echo "[napcat] Starting xray proxy forwarder..."

  XRAY_CONFIG="${LOG_DIR}/xray-config.json"

  if [ -n "${PROXY_SOCKS5_USER:-}" ] && [ -n "${PROXY_SOCKS5_PASS:-}" ]; then
    # Authenticated SOCKS5 proxy
    cat > "${XRAY_CONFIG}" <<EOF
{
  "log": {"loglevel": "warning"},
  "inbounds": [{
    "port": ${XRAY_LOCAL_PORT},
    "listen": "127.0.0.1",
    "protocol": "socks",
    "settings": {"auth": "noauth", "udp": true}
  }],
  "outbounds": [{
    "protocol": "socks",
    "settings": {
      "servers": [{
        "address": "${PROXY_SOCKS5_HOST}",
        "port": ${PROXY_SOCKS5_PORT},
        "users": [{"user": "${PROXY_SOCKS5_USER}", "pass": "${PROXY_SOCKS5_PASS}"}]
      }]
    }
  }]
}
EOF
    echo "[napcat] xray forwarding to ${PROXY_SOCKS5_HOST}:${PROXY_SOCKS5_PORT} (authenticated)"
  else
    # Non-authenticated SOCKS5 proxy
    cat > "${XRAY_CONFIG}" <<EOF
{
  "log": {"loglevel": "warning"},
  "inbounds": [{
    "port": ${XRAY_LOCAL_PORT},
    "listen": "127.0.0.1",
    "protocol": "socks",
    "settings": {"auth": "noauth", "udp": true}
  }],
  "outbounds": [{
    "protocol": "socks",
    "settings": {
      "servers": [{
        "address": "${PROXY_SOCKS5_HOST}",
        "port": ${PROXY_SOCKS5_PORT}
      }]
    }
  }]
}
EOF
    echo "[napcat] xray forwarding to ${PROXY_SOCKS5_HOST}:${PROXY_SOCKS5_PORT}"
  fi

  # Start xray in background
  /home/user/xray run -c "${XRAY_CONFIG}" >> "${LOG_DIR}/xray.log" 2>&1 &

  # Wait for xray to start
  sleep 1

  # Set NapCat proxy environment variables
  export NAPCAT_PROXY_ADDRESS="127.0.0.1"
  export NAPCAT_PROXY_PORT="${XRAY_LOCAL_PORT}"
  echo "[napcat] NapCat proxy set to 127.0.0.1:${XRAY_LOCAL_PORT}"
fi

# Try AppImage extract-and-run first for correct runtime env
if [ -x /home/user/QQ.AppImage ]; then
  exec /home/user/QQ.AppImage --appimage-extract-and-run ${NAPCAT_FLAGS:-}
fi

# Fallback to extracted AppRun
exec /home/user/napcat/AppRun ${NAPCAT_FLAGS:-}
