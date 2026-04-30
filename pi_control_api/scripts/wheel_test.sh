#!/bin/bash
# Yahboom 바퀴 테스트: Docker 안 ROS 2로 /robot3/cmd_vel 발행 (geometry_msgs/Twist)
# 사용법:
#   ./wheel_test.sh forward | back | left | right | stop | demo
# 선택: YAHBOOM_CONTAINER=이름 ./wheel_test.sh ...

set -euo pipefail

pick_container() {
  if [[ -n "${YAHBOOM_CONTAINER:-}" ]]; then
    echo "$YAHBOOM_CONTAINER"
    return
  fi
  docker ps --format '{{.Names}}\t{{.Image}}' | awk '/yahboomtechnology\/ros-humble/ {print $1; exit}'
}

CONTAINER="$(pick_container)"
if [[ -z "$CONTAINER" ]]; then
  echo "yahboomtechnology/ros-humble 컨테이너가 없습니다. ./ros2_humble.sh 로 먼저 실행하세요." >&2
  exit 1
fi

# 선속도·각속도 (필요하면 여기 숫자만 조절)
LINEAR="${WHEEL_LINEAR:-0.18}"
ANGULAR="${WHEEL_ANGULAR:-0.35}"
FORWARD_SEC="${WHEEL_FORWARD_SEC:-1.5}"
TURN_SEC="${WHEEL_TURN_SEC:-1.0}"

ros_bash() {
  docker exec "$CONTAINER" bash -lc "export ROS_DOMAIN_ID=20
source /opt/ros/humble/setup.bash
source /root/yahboomcar_ws/install/setup.bash
$1"
}

# 정지 (한 번 발행)
cmd_stop() {
  ros_bash "ros2 topic pub --once --spin-time 2 --qos-reliability reliable /robot3/cmd_vel geometry_msgs/msg/Twist \
'{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}'"
}

# 일정 시간 동안 같은 속도로 반복 발행 (timeout으로 끊음)
cmd_pub_seconds() {
  local sec="$1"
  local twist="$2"
  # -r 20Hz 정도로 모터 드라이버가 끊기지 않게 유지
  # timeout 으로 프로세스를 끊을 때 rclpy 가 ExternalShutdownException 을 낼 수 있음 (무시해도 됨)
  ros_bash "timeout \"${sec}\" ros2 topic pub -r 20 --qos-reliability reliable /robot3/cmd_vel geometry_msgs/msg/Twist '${twist}' 2>/dev/null" || true
  cmd_stop
}

case "${1:-}" in
  forward)
    echo "앞으로 ${FORWARD_SEC}초 (linear.x=${LINEAR})"
    cmd_pub_seconds "$FORWARD_SEC" "{linear: {x: ${LINEAR}, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
    ;;
  back)
    echo "뒤로 ${FORWARD_SEC}초 (linear.x=-${LINEAR})"
    cmd_pub_seconds "$FORWARD_SEC" "{linear: {x: -${LINEAR}, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
    ;;
  left)
    echo "제자리 좌회전 ${TURN_SEC}초 (angular.z=${ANGULAR})"
    cmd_pub_seconds "$TURN_SEC" "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: ${ANGULAR}}}"
    ;;
  right)
    echo "제자리 우회전 ${TURN_SEC}초 (angular.z=-${ANGULAR})"
    cmd_pub_seconds "$TURN_SEC" "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: -${ANGULAR}}}"
    ;;
  stop)
    echo "정지"
    cmd_stop
    ;;
  demo)
    echo "데모: 앞으로 -> 정지 대기 -> 좌회전 -> 정지 대기 -> 뒤로 -> 정지"
    cmd_pub_seconds "$FORWARD_SEC" "{linear: {x: ${LINEAR}, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
    sleep 0.5
    cmd_pub_seconds "$TURN_SEC" "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: ${ANGULAR}}}"
    sleep 0.5
    cmd_pub_seconds "$FORWARD_SEC" "{linear: {x: -${LINEAR}, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
    ;;
  *)
    echo "사용법: $0 forward | back | left | right | stop | demo" >&2
    echo "환경변수: WHEEL_LINEAR WHEEL_ANGULAR WHEEL_FORWARD_SEC WHEEL_TURN_SEC YAHBOOM_CONTAINER" >&2
    exit 1
    ;;
esac
