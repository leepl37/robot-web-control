"""
Yahboom ROS2 제어/센서 통합 레이어.
- 우선: rclpy ( yahboom_rclpy_node ) — 토픽에 Python으로 직접 publish/subscribe
- 폴백: Docker 컨테이너 내부 `ros2 topic pub/echo` (쉘 스크립트 파일은 호출하지 않음, 동일 CLI 사용)

YAHBOOM_BACKEND=auto | rclpy | docker
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shlex
import subprocess
import threading
import time
from typing import Any, Protocol, runtime_checkable

import config as app_config
from services.yahboom_rclpy_node import RCLPY_OK, RclpyRuntime

log = logging.getLogger("yahboom_transport")

ROS_DOMAIN_ID = os.getenv("ROS_DOMAIN_ID", "20")
YAHBOOM_IMAGE_SNIPPET = "yahboomtechnology/ros-humble"
# wheel_test.sh: -r 20 으로 지속 발행. --once 는 모터에 안 먹는 경우가 많음.
YAHBOOM_CMD_VEL_BURST_SEC = float(os.getenv("YAHBOOM_CMD_VEL_BURST_SEC", "0.25"))
IMU_TOPIC = (os.getenv("YAHBOOM_IMU_TOPIC") or "/robot3/imu").strip() or "/robot3/imu"
# 테스트 기준: 검증된 shell script 명령과 최대한 같은 Docker CLI once 발행.
YAHBOOM_DOCKER_STYLE = (os.getenv("YAHBOOM_DOCKER_STYLE") or "script_once").strip()
YAHBOOM_SERVO_SPIN_TIME = float(os.getenv("YAHBOOM_SERVO_SPIN_TIME", "0.3"))


@runtime_checkable
class YahboomTransport(Protocol):
    @property
    def backend_name(self) -> str: ...
    @property
    def available(self) -> bool: ...
    def pub_cmd_vel(self, linear_x: float, angular_z: float) -> dict[str, Any]: ...
    def jog_cmd_vel(self, linear_x: float, angular_z: float, seconds: float) -> dict[str, Any]: ...
    def set_pan_tilt_degrees(self, pan_deg: int, tilt_deg: int) -> dict[str, Any]: ...
    def set_pan_degrees(self, pan_deg: int) -> dict[str, Any]: ...
    def set_tilt_degrees(self, tilt_deg: int) -> dict[str, Any]: ...
    def get_laser_scan(self) -> dict[str, Any]: ...
    def get_imu(self) -> dict[str, Any]: ...
    def stop(self) -> None: ...


# --- Docker + ros2 CLI (스크립트 없이) ---


def _pick_container() -> str | None:
    if n := (os.getenv("YAHBOOM_CONTAINER") or "").strip():
        return n
    try:
        r = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        for line in (r.stdout or "").splitlines():
            if YAHBOOM_IMAGE_SNIPPET in line:
                return line.split("\t", 1)[0].strip()
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("docker ps: %s", e)
    return None


def _docker_bash() -> str:
    return f"""export ROS_DOMAIN_ID={ROS_DOMAIN_ID}
source /opt/ros/humble/setup.bash
source /root/yahboomcar_ws/install/setup.bash
"""


_BRIDGE_WORKER_CODE = r'''
import json
import math
import sys
import threading
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Imu, LaserScan
from std_msgs.msg import Int32

IMU_TOPIC = "/robot3/imu"


def _stamp_to_float(stamp):
    return float(stamp.sec) + float(stamp.nanosec) / 1_000_000_000.0


def _laser_to_dict(m):
    if m is None:
        return {"ok": False, "error": "no_scan_yet"}
    ranges = list(m.ranges) if m.ranges else []
    sample = []
    for v in ranges[:128]:
        sample.append(None if math.isnan(v) or math.isinf(v) else float(v))
    return {
        "ok": True,
        "source": "docker_bridge",
        "topic": "/robot3/scan",
        "frame_id": m.header.frame_id,
        "stamp_s": _stamp_to_float(m.header.stamp),
        "angle_min": float(m.angle_min),
        "angle_max": float(m.angle_max),
        "angle_increment": float(m.angle_increment),
        "range_min": float(m.range_min),
        "range_max": float(m.range_max),
        "count": len(ranges),
        "ranges_m_sample": sample,
    }


def _imu_to_dict(m):
    if m is None:
        return {"ok": False, "error": "no_imu_yet", "topic": IMU_TOPIC}
    return {
        "ok": True,
        "source": "docker_bridge",
        "topic": IMU_TOPIC,
        "frame_id": m.header.frame_id,
        "stamp_s": _stamp_to_float(m.header.stamp),
        "orientation": {
            "x": float(m.orientation.x),
            "y": float(m.orientation.y),
            "z": float(m.orientation.z),
            "w": float(m.orientation.w),
        },
        "angular_velocity_rad_s": {
            "x": float(m.angular_velocity.x),
            "y": float(m.angular_velocity.y),
            "z": float(m.angular_velocity.z),
        },
        "linear_acceleration_mps2": {
            "x": float(m.linear_acceleration.x),
            "y": float(m.linear_acceleration.y),
            "z": float(m.linear_acceleration.z),
        },
    }


class BridgeNode(Node):
    def __init__(self):
        super().__init__("yahboom_command_bridge")
        self._lock = threading.Lock()
        self._last_scan = None
        self._last_imu = None
        sensor_qos = QoSProfile(
            depth=5,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            durability=DurabilityPolicy.VOLATILE,
        )
        self.pub_cmd = self.create_publisher(Twist, "/robot3/cmd_vel", 10)
        self.pub_s1 = self.create_publisher(Int32, "/robot3/servo_s1", 10)
        self.pub_s2 = self.create_publisher(Int32, "/robot3/servo_s2", 10)
        self.create_subscription(LaserScan, "/robot3/scan", self._on_scan, sensor_qos)
        self.create_subscription(Imu, IMU_TOPIC, self._on_imu, sensor_qos)

    def _on_scan(self, msg):
        with self._lock:
            self._last_scan = msg

    def _on_imu(self, msg):
        with self._lock:
            self._last_imu = msg

    def cmd_vel(self, linear_x, angular_z):
        t = Twist()
        t.linear.x = float(linear_x)
        t.angular.z = float(angular_z)
        self.pub_cmd.publish(t)

    def servo(self, s1=None, s2=None):
        if s1 is not None:
            m = Int32()
            m.data = int(s1)
            self.pub_s1.publish(m)
        if s2 is not None:
            m = Int32()
            m.data = int(s2)
            self.pub_s2.publish(m)

    def scan(self):
        with self._lock:
            return _laser_to_dict(self._last_scan)

    def imu(self):
        with self._lock:
            return _imu_to_dict(self._last_imu)


def main():
    rclpy.init()
    node = BridgeNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    print(json.dumps({"ok": True, "event": "ready", "backend": "docker_bridge"}), flush=True)
    for line in sys.stdin:
        started = time.perf_counter()
        try:
            req = json.loads(line)
            op = req.get("op")
            if op == "cmd_vel":
                node.cmd_vel(req.get("linear_x", 0.0), req.get("angular_z", 0.0))
                out = {"ok": True, "op": op}
            elif op == "servo":
                node.servo(req.get("s1"), req.get("s2"))
                out = {"ok": True, "op": op}
            elif op == "scan":
                out = node.scan()
            elif op == "imu":
                out = node.imu()
            elif op == "ping":
                out = {"ok": True, "op": "ping"}
            else:
                out = {"ok": False, "error": "unknown op", "op": op}
        except Exception as e:
            out = {"ok": False, "error": str(e)}
        out["worker_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        print(json.dumps(out), flush=True)


if __name__ == "__main__":
    main()
'''


class DockerCliTransport:
    def __init__(self) -> None:
        self._c = _pick_container()
        if self._c:
            log.info("Yahboom Docker CLI 백엔드, 컨테이너=%s", self._c)
        else:
            log.error("Yahboom Docker 이미지 컨테이너를 찾지 못함")

    @property
    def backend_name(self) -> str:
        return "docker_cli"

    @property
    def available(self) -> bool:
        return self._c is not None

    def _exec(self, ros_cli: str, timeout: float = 45.0) -> tuple[int, str, str]:
        if not self._c:
            return 1, "", "no container"
        inner = _docker_bash() + ros_cli
        p = subprocess.run(
            ["docker", "exec", self._c, "bash", "-lc", inner],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return p.returncode, p.stdout or "", p.stderr or ""

    def _exec_timed(self, ros_cli: str, timeout: float = 45.0) -> dict[str, Any]:
        started = time.perf_counter()
        code, out, err = self._exec(ros_cli, timeout=timeout)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        log.info(
            "yahboom docker_cli %s %.1fms rc=%s cmd=%s",
            YAHBOOM_DOCKER_STYLE,
            elapsed_ms,
            code,
            ros_cli.replace("\n", "; ")[:500],
        )
        return {
            "ok": code == 0,
            "backend": self.backend_name,
            "mode": YAHBOOM_DOCKER_STYLE,
            "returncode": code,
            "elapsed_ms": elapsed_ms,
            "command": ros_cli.strip(),
            "stdout": (out or "")[-1000:],
            "stderr": (err or "")[-1500:],
        }

    def pub_cmd_vel(self, linear_x: float, angular_z: float) -> dict[str, Any]:
        return self._pub_cmd_vel_once(float(linear_x), float(angular_z), label="pub")

    def jog_cmd_vel(self, linear_x: float, angular_z: float, seconds: float) -> dict[str, Any]:
        """script_once 테스트 모드: jog도 지속 발행하지 않고 검증된 --once 한 번만 보냄."""
        r = self._pub_cmd_vel_once(float(linear_x), float(angular_z), label="jog")
        r["requested_seconds"] = float(seconds)
        r["jog_note"] = "script_once mode ignores seconds and publishes one Twist message"
        return r

    def _pub_cmd_vel_once(self, linear_x: float, angular_z: float, *, label: str) -> dict[str, Any]:
        lx, az = float(linear_x), float(angular_z)
        twist = (
            f"{{linear:{{x: {lx}, y: 0.0, z: 0.0}}, "
            f"angular: {{x: 0.0, y: 0.0, z: {az}}}}}"
        )
        cmd = (
            "ros2 topic pub --once /robot3/cmd_vel geometry_msgs/msg/Twist "
            f"{shlex.quote(twist)}"
        )
        r = self._exec_timed(cmd, timeout=20.0)
        r.update(
            {
                "linear_x": lx,
                "angular_z": az,
                "label": label,
                "commands": [cmd],
            }
        )
        return r

    def _stream_cmd_vel(
        self, linear_x: float, angular_z: float, br: float, *, label: str
    ) -> dict[str, Any]:
        lx, az = float(linear_x), float(angular_z)
        twist = (
            f"{{linear: {{x: {lx}, y: 0.0, z: 0.0}}, "
            f"angular: {{x: 0.0, y: 0.0, z: {az}}}}}"
        )
        br = max(0.15, min(3.0, float(br)))
        # wheel_test.sh: timeout SEC ros2 topic pub -r 20 ... ; 끝나면 stop
        stream = (
            f"timeout {br} ros2 topic pub -r 20 --qos-reliability reliable /robot3/cmd_vel "
            f"geometry_msgs/msg/Twist '{twist}' 2>/dev/null"
        )
        stop_twist = (
            "ros2 topic pub --once --spin-time 2 --qos-reliability reliable /robot3/cmd_vel "
            "geometry_msgs/msg/Twist "
            "'{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}' 2>/dev/null"
        )
        code, out, err = self._exec(f"({stream} || true); {stop_twist}", timeout=20.0 + br)
        return {
            "ok": code == 0,
            "backend": self.backend_name,
            "returncode": code,
            "linear_x": lx,
            "angular_z": az,
            "stderr": (err or "")[-1500:],
            "mode": f"{label}_r20_{br:.2f}s",
        }

    def set_pan_tilt_degrees(self, pan_deg: int, tilt_deg: int) -> dict[str, Any]:
        p, t = int(pan_deg), int(tilt_deg)
        # 확정 매핑: servo_s1 = 좌우(pan), servo_s2 = 상하(tilt)
        return self._pub_i32(("/robot3/servo_s1", "/robot3/servo_s2"), (p, t))

    def set_pan_degrees(self, pan_deg: int) -> dict[str, Any]:
        # servo_s1 = 좌우(pan)
        return self._pub_int32("/robot3/servo_s1", int(pan_deg))

    def set_tilt_degrees(self, tilt_deg: int) -> dict[str, Any]:
        # servo_s2 = 상하(tilt)
        return self._pub_int32("/robot3/servo_s2", int(tilt_deg))

    def _pub_i32(self, topics: tuple[str, str], values: tuple[int, int]) -> dict[str, Any]:
        """camera_pan.sh ros_pub 명령을 그대로 두 번 실행(pan, tilt)."""
        t1, t2 = topics
        v1, v2 = values
        r1 = self._pub_int32(t1, v1)
        r2 = self._pub_int32(t2, v2)
        elapsed = round(float(r1.get("elapsed_ms", 0.0)) + float(r2.get("elapsed_ms", 0.0)), 1)
        commands = [str(r1.get("command", "")), str(r2.get("command", ""))]
        return {
            "ok": r1.get("ok") and r2.get("ok"),
            "backend": self.backend_name,
            "mode": YAHBOOM_DOCKER_STYLE,
            "elapsed_ms": elapsed,
            "commands": commands,
            "result_pan": r1,
            "result_tilt": r2,
        }

    def _pub_int32(self, topic: str, v: int) -> dict[str, Any]:
        """camera_pan.sh의 ros_pub와 같은 명령: --once --spin-time 2 --qos-reliability reliable."""
        t = topic if topic.startswith("/") else f"/{topic}"
        spin = max(0.0, min(2.0, YAHBOOM_SERVO_SPIN_TIME))
        cmd = (
            f"ros2 topic pub --once --spin-time {spin:g} --qos-reliability reliable "
            f"{shlex.quote(t)} std_msgs/msg/Int32 '{{data: {v}}}'"
        )
        r = self._exec_timed(cmd, timeout=20.0)
        r["topic"] = t
        r["value"] = int(v)
        return r

    def get_laser_scan(self) -> dict[str, Any]:
        cmd = "timeout 6 ros2 topic echo /robot3/scan --once 2>/dev/null"
        started = time.perf_counter()
        code, out, err = self._exec(cmd, timeout=15.0)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        log.info("yahboom docker_cli sensor %.1fms rc=%s cmd=%s", elapsed_ms, code, cmd)
        ranges = _parse_scan_ranges(out) if out else []
        return {
            "ok": bool(out),
            "backend": self.backend_name,
            "topic": "/robot3/scan",
            "command": cmd,
            "returncode": code,
            "elapsed_ms": elapsed_ms,
            "stderr": (err or "")[-1000:],
            "ranges_m_sample": ranges[:64],
            "raw_echo": out[:8000] if out else "",
        }

    def get_imu(self) -> dict[str, Any]:
        t = shlex.quote(IMU_TOPIC)
        cmd = f"timeout 4 ros2 topic echo {t} --once 2>/dev/null"
        started = time.perf_counter()
        code, out, err = self._exec(cmd, timeout=12.0)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        log.info("yahboom docker_cli sensor %.1fms rc=%s cmd=%s", elapsed_ms, code, cmd)
        if not out:
            return {
                "ok": False,
                "backend": self.backend_name,
                "error": "no imu message",
                "topic": IMU_TOPIC,
                "command": cmd,
                "returncode": code,
                "elapsed_ms": elapsed_ms,
                "stderr": (err or "")[-1000:],
            }
        return {
            "ok": True,
            "backend": self.backend_name,
            "topic": IMU_TOPIC,
            "command": cmd,
            "returncode": code,
            "elapsed_ms": elapsed_ms,
            "stderr": (err or "")[-1000:],
            "raw_echo": out[:8000],
        }

    def stop(self) -> None:
        return None


class DockerBridgeTransport:
    """Docker 안에 rclpy worker를 계속 띄워두고 JSON line으로 publish 요청."""

    def __init__(self) -> None:
        self._c = _pick_container()
        self._p: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._out_q: queue.Queue[str] = queue.Queue()
        self._err_lines: list[str] = []
        self._available = False
        if not self._c:
            log.error("Yahboom Docker bridge: 컨테이너를 찾지 못함")
            return
        self._start()

    @property
    def backend_name(self) -> str:
        return "docker_bridge"

    @property
    def available(self) -> bool:
        return self._available and self._p is not None and self._p.poll() is None

    def _start(self) -> None:
        if not self._c:
            return
        cmd = _docker_bash() + f"python3 -u -c {shlex.quote(_BRIDGE_WORKER_CODE)}"
        self._p = subprocess.Popen(
            ["docker", "exec", "-i", self._c, "bash", "-lc", cmd],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert self._p.stdout is not None
        assert self._p.stderr is not None
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        try:
            ready = self._out_q.get(timeout=8.0)
            body = json.loads(ready)
            self._available = bool(body.get("ok"))
            if self._available:
                log.info("Yahboom Docker bridge 백엔드 사용, 컨테이너=%s", self._c)
            else:
                log.error("Yahboom Docker bridge 준비 실패: %s", body)
        except Exception as e:  # noqa: BLE001
            log.error("Yahboom Docker bridge 시작 실패: %s stderr=%s", e, self._stderr_tail())
            self.stop()

    def _read_stdout(self) -> None:
        assert self._p and self._p.stdout
        for line in self._p.stdout:
            if line.strip():
                self._out_q.put(line)

    def _read_stderr(self) -> None:
        assert self._p and self._p.stderr
        for line in self._p.stderr:
            if line.strip():
                self._err_lines.append(line.strip())
                self._err_lines = self._err_lines[-30:]
                log.debug("docker_bridge stderr: %s", line.strip())

    def _stderr_tail(self) -> str:
        return "\n".join(self._err_lines[-10:])

    def _request(self, payload: dict[str, Any], timeout: float = 2.0) -> dict[str, Any]:
        if not self.available:
            return {"ok": False, "backend": self.backend_name, "error": "bridge not running"}
        started = time.perf_counter()
        with self._lock:
            assert self._p and self._p.stdin
            self._p.stdin.write(json.dumps(payload) + "\n")
            self._p.stdin.flush()
            try:
                line = self._out_q.get(timeout=timeout)
            except queue.Empty:
                return {
                    "ok": False,
                    "backend": self.backend_name,
                    "error": "bridge response timeout",
                    "stderr": self._stderr_tail(),
                }
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        try:
            out = json.loads(line)
        except json.JSONDecodeError:
            out = {"ok": False, "error": "bad bridge response", "raw": line[-1000:]}
        out.setdefault("ok", False)
        out["backend"] = self.backend_name
        out["elapsed_ms"] = elapsed_ms
        out["request"] = payload
        if self._err_lines:
            out["stderr_tail"] = self._stderr_tail()
        log.info("yahboom docker_bridge %.1fms op=%s ok=%s", elapsed_ms, payload.get("op"), out.get("ok"))
        return out

    def pub_cmd_vel(self, linear_x: float, angular_z: float) -> dict[str, Any]:
        return self._request(
            {"op": "cmd_vel", "linear_x": float(linear_x), "angular_z": float(angular_z)}
        )

    def jog_cmd_vel(self, linear_x: float, angular_z: float, seconds: float) -> dict[str, Any]:
        r = self.pub_cmd_vel(linear_x, angular_z)
        r["requested_seconds"] = float(seconds)
        r["jog_note"] = "docker_bridge publishes one Twist immediately; seconds is ignored"
        return r

    def set_pan_tilt_degrees(self, pan_deg: int, tilt_deg: int) -> dict[str, Any]:
        return self._request({"op": "servo", "s1": int(pan_deg), "s2": int(tilt_deg)})

    def set_pan_degrees(self, pan_deg: int) -> dict[str, Any]:
        return self._request({"op": "servo", "s1": int(pan_deg)})

    def set_tilt_degrees(self, tilt_deg: int) -> dict[str, Any]:
        return self._request({"op": "servo", "s2": int(tilt_deg)})

    def get_laser_scan(self) -> dict[str, Any]:
        return self._request({"op": "scan"}, timeout=2.0)

    def get_imu(self) -> dict[str, Any]:
        return self._request({"op": "imu"}, timeout=2.0)

    def stop(self) -> None:
        self._available = False
        p = self._p
        self._p = None
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=2.0)
            except Exception:  # noqa: BLE001
                try:
                    p.kill()
                except Exception:  # noqa: BLE001
                    pass


def _parse_scan_ranges(text: str) -> list[float | None]:
    m = re.search(r"ranges:\s*\[([^\]]*)\]", text, re.DOTALL)
    if not m:
        return []
    body = m.group(1)
    out: list[float | None] = []
    for part in body.split(","):
        part = part.strip()
        if not part or part == "...":
            continue
        try:
            if part.lower() in ("nan", "inf", "-inf"):
                out.append(None)
            else:
                out.append(float(part))
        except ValueError:
            break
    return out


# --- rclpy ---


class RclpyTransport:
    def __init__(self) -> None:
        self._rt = RclpyRuntime()
        self._ok = self._rt.start() and self._rt.node is not None
        if self._ok:
            log.info("Yahboom rclpy 백엔드 사용")

    @property
    def backend_name(self) -> str:
        return "rclpy"

    @property
    def available(self) -> bool:
        return self._ok and self._rt.node is not None

    def stop(self) -> None:
        self._rt.stop()

    def pub_cmd_vel(self, linear_x: float, angular_z: float) -> dict[str, Any]:
        n = self._rt.node
        if not n:
            return {"ok": False, "error": "no node"}
        try:
            n.publish_cmd_vel(float(linear_x), float(angular_z))
            return {"ok": True, "backend": self.backend_name, "linear_x": linear_x, "angular_z": angular_z}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "backend": self.backend_name, "error": str(e)}

    def jog_cmd_vel(self, linear_x: float, angular_z: float, seconds: float) -> dict[str, Any]:
        n = self._rt.node
        if not n:
            return {"ok": False, "error": "no node"}
        try:
            n.hold_cmd_vel(float(linear_x), float(angular_z), float(seconds))
            return {
                "ok": True,
                "backend": self.backend_name,
                "mode": "rclpy_hold",
                "linear_x": float(linear_x),
                "angular_z": float(angular_z),
                "seconds": float(seconds),
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "backend": self.backend_name, "error": str(e)}

    def set_pan_tilt_degrees(self, pan_deg: int, tilt_deg: int) -> dict[str, Any]:
        n = self._rt.node
        if not n:
            return {"ok": False, "error": "no node"}
        try:
            n.publish_pan_tilt(int(pan_deg), int(tilt_deg))
            return {"ok": True, "backend": self.backend_name, "pan": int(pan_deg), "tilt": int(tilt_deg)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def set_pan_degrees(self, pan_deg: int) -> dict[str, Any]:
        return {"ok": False, "backend": self.backend_name, "error": "single-axis pan unsupported for rclpy"}

    def set_tilt_degrees(self, tilt_deg: int) -> dict[str, Any]:
        return {"ok": False, "backend": self.backend_name, "error": "single-axis tilt unsupported for rclpy"}

    def get_laser_scan(self) -> dict[str, Any]:
        n = self._rt.node
        if not n:
            return {"ok": False, "error": "no node"}
        d = n.get_scan_dict()
        d["backend"] = self.backend_name
        return d

    def get_imu(self) -> dict[str, Any]:
        n = self._rt.node
        if not n:
            return {"ok": False, "error": "no node"}
        d = n.get_imu_dict()
        d["backend"] = self.backend_name
        d["topic"] = IMU_TOPIC
        return d


class NullTransport:
    @property
    def backend_name(self) -> str:
        return "none"

    @property
    def available(self) -> bool:
        return False

    def pub_cmd_vel(self, linear_x: float, angular_z: float) -> dict[str, Any]:
        return {"ok": False, "error": "yahboom transport not available"}

    def jog_cmd_vel(self, linear_x: float, angular_z: float, seconds: float) -> dict[str, Any]:
        return {"ok": False, "error": "yahboom transport not available"}

    def set_pan_tilt_degrees(self, pan_deg: int, tilt_deg: int) -> dict[str, Any]:
        return {"ok": False, "error": "yahboom transport not available"}

    def set_pan_degrees(self, pan_deg: int) -> dict[str, Any]:
        return {"ok": False, "error": "yahboom transport not available"}

    def set_tilt_degrees(self, tilt_deg: int) -> dict[str, Any]:
        return {"ok": False, "error": "yahboom transport not available"}

    def get_laser_scan(self) -> dict[str, Any]:
        return {"ok": False, "error": "yahboom transport not available"}

    def get_imu(self) -> dict[str, Any]:
        return {"ok": False, "error": "yahboom transport not available"}

    def stop(self) -> None:
        return None


def _want_yahboom() -> bool:
    return any(
        d == "yahboom"
        for d in (
            app_config.MOTOR_DRIVER,
            app_config.PTZ_DRIVER,
            app_config.LIDAR_DRIVER,
            app_config.IMU_DRIVER,
        )
    )


def create_yahboom_transport() -> YahboomTransport:
    """YAHBOOM_BACKEND: auto | rclpy | bridge | docker."""
    if not _want_yahboom():
        return NullTransport()

    mode = (os.getenv("YAHBOOM_BACKEND") or app_config.YAHBOOM_BACKEND or "auto").lower()

    if mode in ("auto", "rclpy") and RCLPY_OK:
        t = RclpyTransport()
        if t.available:
            return t
        t.stop()
        if mode == "rclpy":
            log.error("YAHBOOM_BACKEND=rclpy 인데 rclpy 실패 → null")
            return NullTransport()

    if mode in ("auto", "bridge", "docker_bridge"):
        b = DockerBridgeTransport()
        if b.available:
            return b
        b.stop()
        if mode in ("bridge", "docker_bridge"):
            log.error("YAHBOOM_BACKEND=bridge 인데 Docker bridge 실패 → null")
            return NullTransport()

    if mode in ("auto", "docker"):
        d = DockerCliTransport()
        if d.available:
            return d

    log.error("Yahboom 백엔드(rclpy/bridge/docker) 모두 사용 불가")
    return NullTransport()
