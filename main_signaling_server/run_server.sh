#!/usr/bin/env bash
# main_signaling_server: WebSocket 시그널 + /api/robot 프록시가 같은 FastAPI 앱.
# 환경 변수(ROBOT_API_BASE 등)는 프로세스 시작 시만 적용 → 설정 바꾼 뒤 restart.
#
#   chmod +x run_server.sh
#   # Pi IP 끝자리 .108, 포트 8080 (또는 직접 URL 지정)
#   ./run_server.sh restart
#   # 또는: export ROBOT_API_BASE=http://192.168.219.108:8080
#
#   ./run_server.sh stop|start|restart|status
#
# 환경:
#   MAIN_PORT           — 기본 8000
#   MAIN_SERVER_LOG     — 기본: 프로젝트 루트 .server.log
#   MAIN_SERVER_PIDFILE — 기본: 프로젝트 루트 .server.pid
#   ROBOT_API_BASE      — Pi 루트 (미지정 시 http://${ROBOT_HOST:-192.168.219.108}:${ROBOT_API_PORT:-8080})
#   ROBOT_HOST          — Pi IP (기본 192.168.219.108)
#   ROBOT_API_PORT      — Pi 제어 API 포트 (기본 8080)

set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
VENV="${DIR}/.venv"
PORT="${MAIN_PORT:-8000}"
LOG="${MAIN_SERVER_LOG:-${DIR}/.server.log}"
PIDFILE="${MAIN_SERVER_PIDFILE:-${DIR}/.server.pid}"

_ensure_venv() {
  if [[ ! -d "$VENV" ]]; then
    echo "가상환경 없음: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
  fi
}

_port_pids() {
  # macOS / Linux: 포트를 쓰는 PID
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti:"$PORT" 2>/dev/null || true
  fi
}

_stop_one_pid() {
  local p="$1"
  if ! kill -0 "$p" 2>/dev/null; then
    return 0
  fi
  kill -15 "$p" 2>/dev/null || true
  local n=0
  while kill -0 "$p" 2>/dev/null && (( n < 20 )); do
    sleep 0.1
    ((n++)) || true
  done
  if kill -0 "$p" 2>/dev/null; then
    kill -9 "$p" 2>/dev/null || true
  fi
}

cmd_stop() {
  if [[ -f "$PIDFILE" ]]; then
    local saved
    saved="$(tr -d ' \n' < "$PIDFILE" 2>/dev/null || true)"
    if [[ -n "${saved}" ]]; then
      echo "PID 파일 기준 중지: $saved"
      _stop_one_pid "$saved" || true
    fi
    rm -f "$PIDFILE"
  fi
  # 같은 포트에 남은 uvicorn(또는 좀비) 정리
  local p
  p="$(_port_pids)"
  if [[ -n "$p" ]]; then
    echo "포트 ${PORT} 사용 중인 프로세스 중지: $p"
    for i in $p; do
      _stop_one_pid "$i" || true
    done
  else
    echo "포트 ${PORT} — 실행 중인 프로세스 없음"
  fi
}

_wait_ready() {
  local url="http://127.0.0.1:${PORT}/api/robot/_proxy_status"
  local n=0
  while (( n < 50 )); do
    if curl -fsS --connect-timeout 1 "$url" >/dev/null 2>&1; then
      echo "서버 응답 OK: $url"
      return 0
    fi
    sleep 0.1
    ((n++)) || true
  done
  echo "경고: ${url} (50회) — 로그: $LOG" >&2
  return 1
}

cmd_start() {
  _ensure_venv
  if [[ -z "${ROBOT_API_BASE:-}" ]]; then
    export ROBOT_API_BASE="http://${ROBOT_HOST:-192.168.219.108}:${ROBOT_API_PORT:-8080}"
  fi
  echo "ROBOT_API_BASE=$ROBOT_API_BASE"
  if [[ -n "$(_port_pids)" ]]; then
    echo "이미 포트 ${PORT} 사용 중. ./run_server.sh stop 또는 restart." >&2
    exit 1
  fi
  # nohup으로 백그라운드(터미널 끊어도 유지)
  # venv가 이동된 경우 activate/uvicorn 스크립트의 절대 경로가 깨질 수 있어 python -m으로 실행한다.
  nohup "${VENV}/bin/python" -m uvicorn main:app --host "${MAIN_BIND:-0.0.0.0}" --port "$PORT" \
    >"$LOG" 2>&1 &
  local pid=$!
  echo "$pid" >"$PIDFILE"
  echo "시작: PID $pid, 포트 $PORT, 로그 $LOG"
  if _wait_ready; then
    return 0
  fi
  echo "마지막 로그:" >&2
  tail -n 30 "$LOG" >&2 || true
  exit 1
}

cmd_status() {
  if [[ -f "$PIDFILE" ]]; then
    local saved
    saved="$(tr -d ' \n' < "$PIDFILE" 2>/dev/null || true)"
    echo "PID 파일: $saved"
    if [[ -n "$saved" ]] && kill -0 "$saved" 2>/dev/null; then
      echo "프로세스 살아 있음: $saved"
    else
      echo "PID 파일은 있으나 프로세스 없음"
    fi
  else
    echo "PID 파일 없음"
  fi
  local p
  p="$(_port_pids)"
  if [[ -n "$p" ]]; then
    echo "포트 $PORT: PID $p"
  else
    echo "포트 $PORT: (비어 있음)"
  fi
  if curl -fsS "http://127.0.0.1:${PORT}/api/robot/_proxy_status" 2>/dev/null; then
    echo
  else
    echo "HTTP 프로브 실패 (서버 꺼짐 또는 아직 기동 전)"
  fi
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_stop; sleep 0.2; cmd_start ;;
  status) cmd_status ;;
  *)
    echo "사용법: $0 {start|stop|restart|status}" >&2
    exit 1
    ;;
esac
