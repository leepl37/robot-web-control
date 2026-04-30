#!/usr/bin/env bash
# Pi에서 pi_control_api(기본 8080) 띄운 뒤 같은 기기에서 실행.
#   chmod +x test_pi_control.sh
#   ./test_pi_control.sh
# 원격(메인 PC 등)에서 Pi IP로 쓰려면:
#   PI_CONTROL_TEST_URL=http://192.168.219.108:8080 ./test_pi_control.sh

set -euo pipefail
BASE="${PI_CONTROL_TEST_URL:-http://127.0.0.1:8080}"
echo "=== Pi Control API 테스트 (BASE=$BASE) ==="
if ! command -v curl >/dev/null 2>&1; then
  echo "curl 이 필요합니다: sudo apt install -y curl"
  exit 1
fi

get() { echo "[GET]  $*"; curl -sS -w "\n(HTTP %{http_code})\n" "$@" | head -c 4000; echo; }
post_json() { local url=$1; shift; echo "[POST] $url"; curl -sS -w "\n(HTTP %{http_code})\n" -X POST "$url" -H "Content-Type: application/json" -d "$*"; echo; }

echo "1) /health"
get "$BASE/health"
echo
echo "2) /sensors/imu (mock이면 수식 데이터)"
get "$BASE/sensors/imu"
echo
echo "3) /sensors/lidar/scan (mock이면 수식 데이터)"
get "$BASE/sensors/lidar/scan"
echo
echo "4) /camera/capabilities (v4l2 있으면 길이 있음)"
get "$BASE/camera/capabilities"
echo
echo "5) /ptz/absolute  pan=0 tilt=0 (yahboom이면 실제 서보, mock이면 상태만)"
post_json "$BASE/ptz/absolute" '{"pan_deg":0,"tilt_deg":0,"height_mm":null}'
echo
echo "6) /motors/twist  linear=0 angular=0 (yahboom이면 /cmd_vel 한 번, mock이면 OK)"
post_json "$BASE/motors/twist" '{"linear_m_s":0.0,"angular_rad_s":0.0}'
echo
echo "7) /motors/stop"
post_json "$BASE/motors/stop" '{}'
echo
echo "=== 끝. 전부 2xx/JSON 이 나오면 기본 OK (yahboom/Docker은 환경 따로) ==="
