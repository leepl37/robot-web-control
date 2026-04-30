#!/usr/bin/env bash
# 메인 PC에서 main_signaling_server + ROBOT_API_BASE=... 실행 후, 같은 머신에서:
#   MAIN_TEST_URL=http://127.0.0.1:8000 ./test_main_robot.sh
# Pi 없이 보려면 Pi 없이 status 만 ok=false 로 뜨는지 확인.
set -euo pipefail
MAIN="${MAIN_TEST_URL:-http://127.0.0.1:8000}"
echo "=== Main robot control smoke (MAIN=$MAIN) ==="
if ! command -v curl >/dev/null 2>&1; then
  echo "Install curl" >&2
  exit 1
fi
echo "1) GET /api/robot/status (Pi /health include)"
curl -sS "$MAIN/api/robot/status" | head -c 4000; echo; echo
echo "2) GET /api/robot/_proxy_status"
curl -sS "$MAIN/api/robot/_proxy_status"; echo; echo
echo "3) GET /api/robot/health (→ Pi, ROBOT_API_BASE set)"
curl -sS -w "\n(HTTP %{http_code})\n" "$MAIN/api/robot/health" | head -c 2000; echo
echo "=== done ==="
