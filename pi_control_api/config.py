"""라즈베리파이 FastAPI 제어·센서 서버 설정. 하드에 맞게 핀/장치를 수정하세요."""

import os

# Uvicorn
HOST = os.getenv("PI_CONTROL_HOST", "0.0.0.0")
PORT = int(os.getenv("PI_CONTROL_PORT", "8080"))

# True면 실제 GPIO/I2C 대신 내부 모의 데이터만 사용 (목업 끄려면 PI_MOCK_HW=0)
MOCK_HARDWARE = os.getenv("PI_MOCK_HW", "0") not in ("0", "false", "False")

# USB 카메라: v4l2-ctl로 노출/밝기 조절할 때 사용
V4L2_DEVICE = os.getenv("V4L2_DEVICE", "/dev/video0")

# IMU: 예) MPU6050 I2C — 실제 보드에 맞게
IMU_I2C_BUS = int(os.getenv("IMU_I2C_BUS", "1"))
IMU_I2C_ADDR = int(os.getenv("IMU_I2C_ADDR", "0x68"), 0)

# LiDAR: 시리얼 포트 (예: RPLidar USB)
LIDAR_SERIAL_PORT = os.getenv("LIDAR_SERIAL_PORT", "/dev/ttyUSB0")

# 모터: mock | yahboom (홈의 wheel_test.sh — Docker ros2 /robot3/cmd_vel)
MOTOR_DRIVER = os.getenv("MOTOR_DRIVER", "yahboom")

# Pan/Tilt: mock | yahboom (홈의 camera_pan.sh — servo_s1/s2, CAMERA_SWAP_SERVOS)
PTZ_DRIVER = os.getenv("PTZ_DRIVER", "yahboom")

# LiDAR: mock | yahboom — ROS2 /robot3/scan
LIDAR_DRIVER = os.getenv("LIDAR_DRIVER", "yahboom")

# IMU: mock | yahboom — ROS2 YAHBOOM_IMU_TOPIC (기본 /robot3/imu)
IMU_DRIVER = os.getenv("IMU_DRIVER", "yahboom")

# auto: rclpy 시도 → 실패 시 Docker 내부 ros2 CLI. rclpy | docker 강제
YAHBOOM_BACKEND = os.getenv("YAHBOOM_BACKEND", "auto")
