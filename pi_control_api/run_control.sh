#!/usr/bin/env bash
# Pi에서 실기: .env.example 을 .env 로 복사해 PI_MOCK_HW=0, MOTOR_DRIVER=yahboom 등 설정
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
if [[ -f "${DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${DIR}/.env"
  set +a
fi
VENV="${DIR}/.venv"
if [[ ! -d "$VENV" ]]; then
  echo "오류: ${VENV} 없음 — python3 -m venv .venv && pip install -r requirements.txt" >&2
  exit 1
fi
# shellcheck disable=SC1091
source "${VENV}/bin/activate"
echo "pi_control_api: PI_MOCK_HW=${PI_MOCK_HW:-} MOTOR_DRIVER=${MOTOR_DRIVER:-} PTZ_DRIVER=${PTZ_DRIVER:-} port=${PI_CONTROL_PORT:-8080}"
exec uvicorn main:app --host "${PI_CONTROL_HOST:-0.0.0.0}" --port "${PI_CONTROL_PORT:-8080}"
