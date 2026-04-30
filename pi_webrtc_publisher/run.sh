#!/usr/bin/env bash
# 이 프로젝트 폴더에서 publisher.py 실행 (.venv 활성화)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

VENV="${DIR}/.venv"
if [[ ! -d "$VENV" ]]; then
  echo "오류: ${VENV} 가 없습니다." >&2
  echo "먼저 실행: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "${VENV}/bin/activate"
exec python "${DIR}/publisher.py" "$@"
