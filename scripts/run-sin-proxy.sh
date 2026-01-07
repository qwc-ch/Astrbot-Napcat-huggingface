#!/usr/bin/env bash
set -euo pipefail

# 临时代理（sing-box VMess + WS）
# - 对外路径固定：/ws（由 OpenResty 反代到 127.0.0.1:10000）
# - UUID 固定：e713f696-719a-4209-9548-59ca7dbb5086
# - 运行 60 秒后自动退出并清理（包含 sing-box 二进制）

UUID="e713f696-719a-4209-9548-59ca7dbb5086"
WS_PATH="/ws"
LISTEN_PORT="10000"
RUN_SECONDS="60"
RULE_ID="sin-proxy-${UUID}"

SING_BOX_VERSION="${SIN_PROXY_SING_BOX_VERSION:-1.12.14}"
LOG_LEVEL="${SIN_PROXY_LOG_LEVEL:-info}"

RUNTIME_DIR="/tmp/${RULE_ID}"
SB_BIN="${RUNTIME_DIR}/sing-box"
CONFIG_JSON="${RUNTIME_DIR}/config.json"

ADMIN_BASE="http://127.0.0.1:7860"
ADMIN_UI="${ADMIN_BASE}/admin/ui/"
ADMIN_ROUTES="${ADMIN_BASE}/admin/routes.json"
ADMIN_CONFIG="/home/user/nginx/admin_config.json"
DEFAULT_CONFIG="/home/user/nginx/default_admin_config.json"

sb_pid=""

log() {
  echo "[sin-proxy] $*"
}

read_admin_password() {
  local cfg=""
  if [ -f "${ADMIN_CONFIG}" ]; then
    cfg="${ADMIN_CONFIG}"
  elif [ -f "${DEFAULT_CONFIG}" ]; then
    cfg="${DEFAULT_CONFIG}"
  else
    return 1
  fi

  jq -r '.admin_password // ""' "${cfg}" 2>/dev/null || true
}

wait_nginx() {
  local i
  for i in $(seq 1 30); do
    if curl -fsS --max-time 2 "${ADMIN_UI}" -o /dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

ensure_ws_route() {
  local pass="$1"
  local routes_json new_rules payload

  routes_json="$(curl -fsS --max-time 5 -H "X-Admin-Password: ${pass}" "${ADMIN_ROUTES}")" || return 1
  new_rules="$(echo "${routes_json}" | jq \
    --arg id "${RULE_ID}" \
    --arg backend "http://127.0.0.1:${LISTEN_PORT}" \
    --arg ws_path "${WS_PATH}" \
    '
      .rules
      | if (map(select(.id == $id)) | length) > 0 then .
        else . + [
          {
            "match": { "path_equal": $ws_path },
            "backend": $backend,
            "action": "proxy",
            "id": $id,
            "priority": 210
          }
        ]
        end
    ')" || return 1

  payload="$(jq -n --argjson rules "${new_rules}" '{rules: $rules}')" || return 1
  curl -fsS --max-time 5 -X POST \
    -H "X-Admin-Password: ${pass}" \
    -H "Content-Type: application/json" \
    --data "${payload}" \
    "${ADMIN_ROUTES}" >/dev/null || return 1
}

remove_ws_route() {
  local pass="$1"
  local routes_json new_rules payload

  routes_json="$(curl -fsS --max-time 5 -H "X-Admin-Password: ${pass}" "${ADMIN_ROUTES}")" || return 1
  new_rules="$(echo "${routes_json}" | jq --arg id "${RULE_ID}" '.rules | map(select(.id != $id))')" || return 1
  payload="$(jq -n --argjson rules "${new_rules}" '{rules: $rules}')" || return 1
  curl -fsS --max-time 5 -X POST \
    -H "X-Admin-Password: ${pass}" \
    -H "Content-Type: application/json" \
    --data "${payload}" \
    "${ADMIN_ROUTES}" >/dev/null || return 1
}

cleanup() {
  set +e

  if [ -n "${sb_pid}" ] && kill -0 "${sb_pid}" 2>/dev/null; then
    log "停止 sing-box (pid=${sb_pid})..."
    kill -TERM "${sb_pid}" 2>/dev/null || true
    for _ in $(seq 1 10); do
      kill -0 "${sb_pid}" 2>/dev/null || break
      sleep 1
    done
    kill -KILL "${sb_pid}" 2>/dev/null || true
    wait "${sb_pid}" 2>/dev/null || true
  fi

  # 尝试移除 /ws 路由（只移除本脚本创建的 RULE_ID）
  local pass
  pass="$(read_admin_password || true)"
  if [ -n "${pass}" ]; then
    remove_ws_route "${pass}" 2>/dev/null || true
  fi

  # 删除所有相关文件（包含 sing-box 二进制）
  rm -rf "${RUNTIME_DIR}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

log "准备启动临时代理：UUID=${UUID}，对外路径=${WS_PATH}，监听 127.0.0.1:${LISTEN_PORT}，运行 ${RUN_SECONDS}s"

rm -rf "${RUNTIME_DIR}"
mkdir -p "${RUNTIME_DIR}"

arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
case "${arch}" in
  amd64|x86_64) sb_arch="amd64" ;;
  arm64|aarch64) sb_arch="arm64" ;;
  *)
    log "不支持的架构: ${arch}"
    exit 1
    ;;
esac

tarball="${RUNTIME_DIR}/sing-box.tar.gz"
url="https://github.com/SagerNet/sing-box/releases/download/v${SING_BOX_VERSION}/sing-box-${SING_BOX_VERSION}-linux-${sb_arch}.tar.gz"
log "下载 sing-box: ${url}"
curl -fL --retry 3 --retry-delay 1 --max-time 60 -o "${tarball}" "${url}"
tar -C "${RUNTIME_DIR}" -xzf "${tarball}"
install -m 0755 "${RUNTIME_DIR}/sing-box-${SING_BOX_VERSION}-linux-${sb_arch}/sing-box" "${SB_BIN}"
rm -rf "${tarball}" "${RUNTIME_DIR}/sing-box-${SING_BOX_VERSION}-linux-${sb_arch}"

cat > "${CONFIG_JSON}" <<EOF
{
  "log": {
    "level": "${LOG_LEVEL}",
    "timestamp": true
  },
  "inbounds": [
    {
      "type": "vmess",
      "tag": "vmess-in",
      "listen": "127.0.0.1",
      "listen_port": ${LISTEN_PORT},
      "users": [
        {
          "uuid": "${UUID}",
          "alterId": 0
        }
      ],
      "transport": {
        "type": "ws",
        "path": "${WS_PATH}"
      }
    }
  ],
  "outbounds": [
    {
      "type": "direct",
      "tag": "direct"
    }
  ]
}
EOF

if wait_nginx; then
  pass="$(read_admin_password || true)"
  if [ -n "${pass}" ]; then
    ensure_ws_route "${pass}" || log "添加 /ws 路由失败（可能已存在或权限不足）"
  else
    log "未能读取管理员密码，跳过自动添加 /ws 路由（可在 /admin/ui/ 手动添加）"
  fi
else
  log "等待 nginx 就绪超时，跳过自动添加 /ws 路由（可在 /admin/ui/ 手动添加）"
fi

log "启动 sing-box..."
"${SB_BIN}" run -c "${CONFIG_JSON}" &
sb_pid="$!"

for _ in $(seq 1 "${RUN_SECONDS}"); do
  if ! kill -0 "${sb_pid}" 2>/dev/null; then
    wait "${sb_pid}"
    exit "$?"
  fi
  sleep 1
done

log "已运行 ${RUN_SECONDS}s，退出并清理"
exit 0
