#!/bin/zsh

set -u
unsetopt BG_NICE

WORKBENCH_DIR="${0:A:h}"
WORKSPACE_DIR="${WORKBENCH_DIR:h}"
DATA_DIR="${WORKBENCH_DIR}/data"
LOG_DIR="${DATA_DIR}/logs"
STATIC_DIR="${WORKBENCH_DIR}/frontend"
PROJECTS_DIR="${WORKSPACE_DIR}/work"
BUNDLED_RUNTIME_PYTHON="${HOME:-}/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [[ -x "${BUNDLED_RUNTIME_PYTHON}" ]]; then
  PYTHON_BIN="${BUNDLED_RUNTIME_PYTHON}"
else
  PYTHON_BIN="$(command -v python3)"
fi

mkdir -p "${LOG_DIR}"

if [[ ! -t 1 ]]; then
  exec >>"${LOG_DIR}/launcher.log" 2>&1
fi

notify_failure() {
  local failure_message="$1"
  print -r -- "${failure_message}"
  if [[ ! -t 1 ]]; then
    /usr/bin/osascript \
      -e 'on run argv' \
      -e 'display alert "镜流工作台无法启动" message (item 1 of argv) as critical' \
      -e 'end run' \
      "${failure_message}" >/dev/null 2>&1 || true
  fi
}

if [[ -z "${PYTHON_BIN}" ]]; then
  notify_failure "未找到 Python 3，工作台无法启动。"
  exit 1
fi

open_workbench_window() {
  local target_url="$1"
  if [[ -d "/Applications/Google Chrome.app" ]]; then
    /usr/bin/open -na "Google Chrome" --args --app="${target_url}"
  elif [[ -d "/Applications/Microsoft Edge.app" ]]; then
    /usr/bin/open -na "Microsoft Edge" --args --app="${target_url}"
  else
    /usr/bin/open "${target_url}"
  fi
}

for EXISTING_PORT in {8765..8799}; do
  EXISTING_BOOTSTRAP="$(/usr/bin/curl -fsS --max-time 0.35 "http://127.0.0.1:${EXISTING_PORT}/api/v1/bootstrap" 2>/dev/null || true)"
  if [[ -z "${EXISTING_BOOTSTRAP}" ]]; then
    continue
  fi
  EXISTING_PROJECTS_ROOT="$(print -r -- "${EXISTING_BOOTSTRAP}" | "${PYTHON_BIN}" -c 'import json,sys; print((json.load(sys.stdin).get("server") or {}).get("projects_root") or "")' 2>/dev/null || true)"
  if [[ "${EXISTING_PROJECTS_ROOT}" == "${PROJECTS_DIR}" ]]; then
    EXISTING_URL="http://127.0.0.1:${EXISTING_PORT}"
    echo "检测到同一项目目录的镜流工作台已经运行，直接复用：${EXISTING_URL}"
    open_workbench_window "${EXISTING_URL}"
    exit 0
  fi
done

PORT=8765
while (( PORT < 8800 )); do
  if ! /usr/sbin/lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    break
  fi
  PORT=$((PORT + 1))
done

if (( PORT >= 8800 )); then
  notify_failure "8765–8799 端口均被占用，工作台无法启动。"
  exit 1
fi

URL="http://127.0.0.1:${PORT}"
LOG_FILE="${LOG_DIR}/workbench-${PORT}.log"
ARGS=(
  "${WORKBENCH_DIR}/backend/app.py"
  --host 127.0.0.1
  --port "${PORT}"
  --data-root "${DATA_DIR}"
  --static-root "${STATIC_DIR}"
)

if [[ -d "${PROJECTS_DIR}" ]]; then
  ARGS+=(--projects-root "${PROJECTS_DIR}")
fi

export PYTHONPYCACHEPREFIX="/tmp/jingliu-workbench-pycache"
"${PYTHON_BIN}" "${ARGS[@]}" >"${LOG_FILE}" 2>&1 &
SERVER_PID=$!

cleanup() {
  if kill -0 "${SERVER_PID}" >/dev/null 2>&1; then
    kill "${SERVER_PID}" >/dev/null 2>&1
    wait "${SERVER_PID}" >/dev/null 2>&1
  fi
}
trap cleanup EXIT INT TERM

READY=0
for _ in {1..50}; do
  if /usr/bin/curl -fsS "${URL}/api/v1/health" >/dev/null 2>&1; then
    READY=1
    break
  fi
  if ! kill -0 "${SERVER_PID}" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

if (( READY == 0 )); then
  notify_failure "工作台启动失败。日志：${LOG_FILE}"
  tail -n 40 "${LOG_FILE}"
  exit 1
fi

echo "镜流工作台已启动：${URL}"
echo "项目目录：${PROJECTS_DIR}"
echo "运行日志：${LOG_FILE}"
echo "关闭这个终端窗口即可停止本地服务。"

open_workbench_window "${URL}"

wait "${SERVER_PID}"
