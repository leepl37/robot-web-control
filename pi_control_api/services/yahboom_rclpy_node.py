"""
ROS2 (rclpy) — 스크립트가 쓰는 것과 동일 토픽/메시지로 직접 publish/subscribe ( .sh 미호출 ).

- /robot3/cmd_vel : geometry_msgs/Twist
- /robot3/servo_s1, /robot3/servo_s2 : std_msgs/Int32  (CAMERA_SWAP_SERVOS 로 pan/tilt 매핑)
- /robot3/scan : sensor_msgs/LaserScan  (최신 메시지 캐시)
- YAHBOOM_IMU_TOPIC (기본 /robot3/imu) : sensor_msgs/Imu

rclpy 미설치·init 실패 시 yahboom_transport 가 Docker+ros2 CLI 백엔드 사용.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

log = logging.getLogger("yahboom_rclpy")

RCLPY_OK = False

try:
    import rclpy
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node
    from rclpy.callback_groups import ReentrantCallbackGroup
    from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
    from geometry_msgs.msg import Twist
    from std_msgs.msg import Int32
    from sensor_msgs.msg import LaserScan, Imu

    RCLPY_OK = True
except ImportError as e:
    log.debug("rclpy import 실패 (정상일 수 있음): %s", e)

IMU_TOPIC = (os.getenv("YAHBOOM_IMU_TOPIC") or "/robot3/imu").strip() or "/robot3/imu"

# sensor 토픽에 흔한 QoS
if RCLPY_OK:
    _SCAN_QOS = QoSProfile(
        depth=5,
        reliability=ReliabilityPolicy.BEST_EFFORT,
        history=HistoryPolicy.KEEP_LAST,
        durability=DurabilityPolicy.VOLATILE,
    )


def _laser_to_dict(m: Any) -> dict[str, Any]:
    if m is None:
        return {"ok": False, "error": "no_scan_yet"}
    rlist = list(m.ranges) if m.ranges else []
    return {
        "ok": True,
        "source": "rclpy",
        "frame_id": m.header.frame_id,
        "angle_min": float(m.angle_min),
        "angle_max": float(m.angle_max),
        "angle_increment": float(m.angle_increment),
        "time_increment": float(m.time_increment),
        "scan_time": float(m.scan_time),
        "range_min": float(m.range_min),
        "range_max": float(m.range_max),
        "n_ranges": len(rlist),
        "ranges_m": rlist[:512],
    }


def _imu_to_dict(m: Any) -> dict[str, Any]:
    if m is None:
        return {"ok": False, "error": "no_imu_yet"}
    o, a, la = m.orientation, m.angular_velocity, m.linear_acceleration
    return {
        "ok": True,
        "source": "rclpy",
        "frame_id": m.header.frame_id,
        "orientation": {"x": float(o.x), "y": float(o.y), "z": float(o.z), "w": float(o.w)},
        "angular_velocity": {"x": float(a.x), "y": float(a.y), "z": float(a.z)},
        "linear_acceleration": {"x": float(la.x), "y": float(la.y), "z": float(la.z)},
    }


if RCLPY_OK:
    class _RobotBridgeNode(Node):
        """cmd_vel: wheel_test.sh 처럼 20Hz 유지(모터가 한 번 pub 만으로는 멈출 수 있음)."""

        def __init__(self) -> None:
            super().__init__("pi_control_api_ros2_bridge")
            g = ReentrantCallbackGroup()
            self._lock = threading.Lock()
            self._last_scan: Any = None
            self._last_imu: Any = None
            self._cmd_lin_x: float = 0.0
            self._cmd_ang_z: float = 0.0
            # 마감 이전까지만 마지막 twist 재발행 (API 호출마다 연장)
            self._cmd_deadline: float = 0.0
            self._pub_cmd = self.create_publisher(Twist, "/robot3/cmd_vel", 10, callback_group=g)
            self._pub_s1 = self.create_publisher(Int32, "/robot3/servo_s1", 10, callback_group=g)
            self._pub_s2 = self.create_publisher(Int32, "/robot3/servo_s2", 10, callback_group=g)
            self.create_subscription(
                LaserScan, "/robot3/scan", self._cb_scan, _SCAN_QOS, callback_group=g
            )
            self.create_subscription(
                Imu, IMU_TOPIC, self._cb_imu, _SCAN_QOS, callback_group=g
            )
            # 20Hz: wheel_test.sh `ros2 topic pub -r 20` 와 같음
            self.create_timer(0.05, self._on_cmd_stream_tick, callback_group=g)
            self.get_logger().info("bridge: cmd_vel, servo s1/s2, scan, imu=%s", IMU_TOPIC)

        def _cb_scan(self, msg: Any) -> None:
            with self._lock:
                self._last_scan = msg

        def _cb_imu(self, msg: Any) -> None:
            with self._lock:
                self._last_imu = msg

        def _on_cmd_stream_tick(self) -> None:
            now = time.monotonic()
            t = Twist()
            with self._lock:
                if now < self._cmd_deadline:
                    t.linear.x = self._cmd_lin_x
                    t.angular.z = self._cmd_ang_z
                else:
                    t.linear.x = 0.0
                    t.angular.z = 0.0
                self._pub_cmd.publish(t)

        def publish_cmd_vel(self, linear_x: float, angular_z: float) -> None:
            """즉시 1회 + 타이머로 ~20Hz 유지(마감 0.5s 갱신)."""
            lx, az = float(linear_x), float(angular_z)
            t = Twist()
            t.linear.x, t.angular.z = lx, az
            with self._lock:
                self._cmd_lin_x, self._cmd_ang_z = lx, az
                self._cmd_deadline = time.monotonic() + 0.5
                self._pub_cmd.publish(t)

        def hold_cmd_vel(self, linear_x: float, angular_z: float, seconds: float) -> None:
            """wheel_test.sh forward N초: 한 요청으로 cmd_vel 를 sec 동안 20Hz 유지."""
            lx, az = float(linear_x), float(angular_z)
            sec = max(0.1, min(5.0, float(seconds)))
            t = Twist()
            t.linear.x, t.angular.z = lx, az
            with self._lock:
                self._cmd_lin_x, self._cmd_ang_z = lx, az
                self._cmd_deadline = time.monotonic() + sec
                self._pub_cmd.publish(t)

        def publish_servo(self, s1: int, s2: int) -> None:
            a, b = Int32(), Int32()
            a.data, b.data = int(s1), int(s2)
            with self._lock:
                self._pub_s1.publish(a)
                self._pub_s2.publish(b)

        def publish_pan_tilt(self, pan_deg: int, tilt_deg: int) -> None:
            p, t = int(pan_deg), int(tilt_deg)
            # 확정 매핑: servo_s1 = 좌우(pan), servo_s2 = 상하(tilt)
            self.publish_servo(p, t)

        def get_scan_dict(self) -> dict[str, Any]:
            with self._lock:
                return _laser_to_dict(self._last_scan)

        def get_imu_dict(self) -> dict[str, Any]:
            with self._lock:
                return _imu_to_dict(self._last_imu)

    class RclpyRuntime:
        """rclpy.init + MultiThreadedExecutor(백그라운드) + 단일 Node."""

        def __init__(self) -> None:
            self._node: _RobotBridgeNode | None = None
            self._executor: MultiThreadedExecutor | None = None
            self._thread: threading.Thread | None = None
            self._started = False

        def start(self) -> bool:
            if not RCLPY_OK:
                return False
            if self._started and self._node is not None:
                return True
            try:
                if not rclpy.ok():
                    rclpy.init()
                self._node = _RobotBridgeNode()
                self._executor = MultiThreadedExecutor()
                self._executor.add_node(self._node)
                self._thread = threading.Thread(
                    target=self._executor.spin,
                    name="rclpy-exec",
                    daemon=True,
                )
                self._thread.start()
                time.sleep(0.25)
                self._started = True
                log.info("rclpy 런타임 OK (같은 ROS2 그래프에 붙는지 = DDS/도메인 확인)")
                return True
            except Exception as e:  # noqa: BLE001
                log.exception("rclpy 시작 실패, Docker CLI로 폴백 가능: %s", e)
                self.stop()
                return False

        @property
        def node(self) -> _RobotBridgeNode | None:
            return self._node

        def stop(self) -> None:
            try:
                if self._node is not None:
                    self._node.destroy_node()
            except Exception:  # noqa: BLE001
                pass
            self._node = None
            try:
                if self._executor is not None:
                    self._executor.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self._executor = None
            try:
                if rclpy.ok():
                    rclpy.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self._started = False

else:
    # 스텁 — 타입 힌트용
    class RclpyRuntime:  # type: ignore[no-redef]
        def start(self) -> bool:
            return False

        def stop(self) -> None:
            return

        @property
        def node(self) -> None:
            return None
