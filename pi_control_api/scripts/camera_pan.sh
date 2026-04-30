#!/bin/bash
# Yahboom camera PWM servos via ROS 2 (host -> Docker).
#   S2 = pan (left/right)   -> /robot3/servo_s2  range about [-90, 20]
#   S1 = tilt (up/down)     -> /robot3/servo_s1  range about [-90, 90]
#
# 보드에 서보 핀이 위/아래 두 줄 있을 때: 이 기기에서는 아래 줄은 동작하지 않고
# 위 줄만 쓰면 됨. 좌우는 ‘아래쪽에서 나온 선’을 위 줄에, 상하는 ‘위쪽 선’을 위 줄에 꽂는 식.
#
# Usage — 좌우만:
#   ./camera_pan.sh left | right | center | angle <n>
# Usage — 상하만:
#   ./camera_pan.sh tilt up | tilt down | tilt center | tilt angle <n> | tilt demo
# Usage — 상하+좌우 한 번에 (servo_s2 상하 30/-30/0 → servo_s1 좌우 30/-30/0):
#   ./camera_pan.sh demo
#
# Optional:
#   YAHBOOM_CONTAINER=name ./camera_pan.sh ...
#   문서와 같은 배선(S2=좌우, S1=상하)이면: CAMERA_SWAP_SERVOS=0 ./camera_pan.sh ...
#
# 이 Pi 에서 실측: S1=좌우, S2=상하 → 아래 기본값 1 (swap) 이 맞음.

set -euo pipefail
: "${CAMERA_SWAP_SERVOS:=1}"

pick_container() {
  if [[ -n "${YAHBOOM_CONTAINER:-}" ]]; then
    echo "$YAHBOOM_CONTAINER"
    return
  fi
  docker ps --format '{{.Names}}\t{{.Image}}' | awk '/yahboomtechnology\/ros-humble/ {print $1; exit}'
}

CONTAINER="$(pick_container)"
if [[ -z "$CONTAINER" ]]; then
  echo "No running container with image yahboomtechnology/ros-humble. Start ./ros2_humble.sh first." >&2
  exit 1
fi

ros_pub() {
  local topic="$1"
  local angle="$2"
  docker exec "$CONTAINER" bash -lc "export ROS_DOMAIN_ID=20
source /opt/ros/humble/setup.bash
source /root/yahboomcar_ws/install/setup.bash
ros2 topic pub --once --spin-time 2 --qos-reliability reliable $topic std_msgs/msg/Int32 '{data: $angle}'"
}

ros_pub_s1() { ros_pub /robot3/servo_s1 "$1"; }
ros_pub_s2() { ros_pub /robot3/servo_s2 "$1"; }

# CAMERA_SWAP_SERVOS=1 (기본): 좌우→servo_s1, 상하→servo_s2
# CAMERA_SWAP_SERVOS=0: 문서대로 좌우→servo_s2, 상하→servo_s1
if [[ "${CAMERA_SWAP_SERVOS}" == "1" ]]; then
  pan_pub() { ros_pub_s1 "$1"; }
  tilt_pub() { ros_pub_s2 "$1"; }
  PAN_TOPIC=servo_s1
  TILT_TOPIC=servo_s2
else
  pan_pub() { ros_pub_s2 "$1"; }
  tilt_pub() { ros_pub_s1 "$1"; }
  PAN_TOPIC=servo_s2
  TILT_TOPIC=servo_s1
fi

if [[ "${1:-}" == "tilt" ]]; then
  shift
  case "${1:-}" in
    up)     tilt_pub 90 ;;
    down)   tilt_pub -90 ;;
    center) tilt_pub 0 ;;
    angle)
      if [[ -z "${2:-}" ]]; then
        echo "usage: $0 tilt angle <integer approx -90_to_90>" >&2
        exit 1
      fi
      tilt_pub "$2"
      ;;
    demo)
      echo "Tilt demo: up (90) -> down (-90) -> center (0)  (${TILT_TOPIC})"
      for a in 90 -90 0; do
        echo "${TILT_TOPIC} -> $a"
        tilt_pub "$a"
        sleep 1.2
      done
      ;;
    *)
      echo "usage: $0 tilt {up|down|center|angle <n>|demo}" >&2
      exit 1
      ;;
  esac
  exit 0
fi

case "${1:-}" in
  left)   pan_pub -90 ;;
  right)  pan_pub 20 ;;
  center) pan_pub -60 ;;
  angle)
    if [[ -z "${2:-}" ]]; then
      echo "usage: $0 angle <integer_in_-90_to_20>   (pan only)" >&2
      exit 1
    fi
    pan_pub "$2"
    ;;
  demo)
    # 이 Pi 실측: S2=상하, S1=좌우 (아래는 토픽 고정, swap 과 무관)
    echo "데모: 상하(/robot3/servo_s2) 30 → -30 → 0, 이어서 좌우(/robot3/servo_s1) 30 → -30 → 0"
    for a in 30 -30 0; do
      echo "servo_s2 (상하) -> $a"
      ros_pub_s2 "$a"
      sleep 1.2
    done
    sleep 0.4
    for a in 30 -30 0; do
      echo "servo_s1 (좌우) -> $a"
      ros_pub_s1 "$a"
      sleep 1.2
    done
    ;;
  *)
    echo "usage: $0 {left|right|center|angle <n>|demo}" >&2
    echo "       demo = 상하(s2)+좌우(s1) 각 30/-30/0" >&2
    echo "       $0 tilt {up|down|center|angle <n>|demo}" >&2
    exit 1
    ;;
esac
