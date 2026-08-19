#!/usr/bin/env bash
set -euo pipefail

# Ensure display env
export DISPLAY="${DISPLAY:-:1}"
export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"

LOG_DIR="/home/user/logs"
mkdir -p "${LOG_DIR}"

# 运行开关：ENABLE_NAPCAT=false 时直接退出（exit 0 不会被 supervisor 重启）
if [ "${ENABLE_NAPCAT:-true}" != "true" ]; then
  echo "[run-napcat] ENABLE_NAPCAT=false，NapCat 已禁用"
  echo "[run-napcat] 如需启用请修改 config.env 后重新构建"
  exit 0
fi

# Prefer /app layout to match official images
export HOME="/app"
export XDG_CONFIG_HOME="/app/.config"
mkdir -p /app/.config/QQ /app/napcat/config || true

# Non-root container: Chromium SUID sandbox is unavailable, must disable it
export ELECTRON_DISABLE_SANDBOX=1

# Try AppImage extract-and-run first for correct runtime env
if [ -x /home/user/QQ.AppImage ]; then
  exec /home/user/QQ.AppImage --appimage-extract-and-run --no-sandbox --disable-dev-shm-usage ${NAPCAT_FLAGS:-}
fi

# Fallback to extracted AppRun
exec /home/user/napcat/AppRun --no-sandbox --disable-dev-shm-usage ${NAPCAT_FLAGS:-}
