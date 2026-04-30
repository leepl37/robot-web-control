"""
메인 시그널/로봇 프록시 서버 설정. 환경 변수는 프로세스 시작 시 읽힌다.
"""

import os

# Pi pi_control_api — 예: http://192.168.219.108:8080 (run_server.sh 기본)
ROBOT_API_BASE: str = os.getenv("ROBOT_API_BASE", "").rstrip("/")

# Pi /health 등 프로브용 타임아웃(초)
ROBOT_API_PROBE_TIMEOUT: float = float(os.getenv("ROBOT_API_PROBE_TIMEOUT", "3.0"))
