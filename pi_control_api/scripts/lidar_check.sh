#!/bin/bash
# Quick LiDAR (/robot3/scan) health check (host -> Yahboom Docker).
# Usage:
#   ./lidar_check.sh       # info + hz (~4s) + one message sample
#   ./lidar_check.sh hz    # only rate (~5s)
# Optional: YAHBOOM_CONTAINER=name ./lidar_check.sh

set -euo pipefail

pick_container() {
  if [[ -n "${YAHBOOM_CONTAINER:-}" ]]; then echo "$YAHBOOM_CONTAINER"; return; fi
  docker ps --format '{{.Names}}\t{{.Image}}' | awk '/yahboomtechnology\/ros-humble/ {print $1; exit}'
}

CONTAINER="$(pick_container)"
if [[ -z "$CONTAINER" ]]; then
  echo "No yahboomtechnology/ros-humble container running." >&2
  exit 1
fi

ros() {
  docker exec "$CONTAINER" bash -lc "export ROS_DOMAIN_ID=20
source /opt/ros/humble/setup.bash
source /root/yahboomcar_ws/install/setup.bash
$*"
}

echo "=== Container: $CONTAINER ==="
echo "=== ros2 topic info /robot3/scan ==="
ros "ros2 topic info /robot3/scan"

if [[ "${1:-}" == "hz" ]]; then
  echo ""
  echo "=== ros2 topic hz /robot3/scan (~8s, wait for discovery) ==="
  ros "timeout 8 ros2 topic hz /robot3/scan" || true
  exit 0
fi

echo ""
echo "=== ros2 topic hz /robot3/scan (~8s) ==="
ros "timeout 8 ros2 topic hz /robot3/scan" || true

echo ""
echo "=== One message (header + scan params + first ranges) ==="
ros "timeout 4 ros2 topic echo /robot3/scan --once 2>/dev/null" | head -45
