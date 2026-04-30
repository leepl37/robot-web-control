"""라즈베리파이 WebRTC 송신 쪽 설정 (LAN에 맞게 MAIN_SERVER_WS_URL 수정)."""

# 시그널링 서버·브라우저 페이지의 robot_id와 동일해야 함
ROBOT_ID = "robot3"

# uvicorn을 띄운 PC의 LAN IP와 포트 (예: 메인 PC가 192.168.1.50이면 그 주소)
MAIN_SERVER_WS_URL = "ws://192.168.1.100:8000/ws"

CAMERA_DEVICE = "/dev/video0"

WIDTH = 640
HEIGHT = 480
FPS = 30
