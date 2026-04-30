"""
바퀴: mock | yahboom (ROS2 /robot3/cmd_vel).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from services.yahboom_transport import YahboomTransport

log = logging.getLogger("motors")


@dataclass
class MotorState:
    left: float = 0.0
    right: float = 0.0
    linear_m_s: float = 0.0
    angular_rad_s: float = 0.0
    updated_s: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "left": self.left,
            "right": self.right,
            "linear_m_s": self.linear_m_s,
            "angular_rad_s": self.angular_rad_s,
            "updated_s": self.updated_s,
        }


class MotorService:
    def __init__(self, driver: str, yahboom: "YahboomTransport | None" = None) -> None:
        self._driver = driver
        self._t = yahboom
        self._state = MotorState()
        self._mock = driver == "mock"

    def set_wheels(self, left: float, right: float) -> dict[str, Any]:
        if self._driver == "yahboom" and (not self._t or not self._t.available):
            return {"ok": False, "error": "Yahboom ROS2 백엔드 없음 (rclpy/Docker 설정)"}
        self._state.left = max(-1.0, min(1.0, left))
        self._state.right = max(-1.0, min(1.0, right))
        self._state.updated_s = time.time()
        if self._driver == "yahboom" and self._t and self._t.available:
            L = 0.2
            linear = (self._state.left + self._state.right) * 0.1
            angular = (self._state.right - self._state.left) * 0.4
            r = self._t.pub_cmd_vel(linear, angular)
            return {"ok": r.get("ok"), "driver": "yahboom", "state": self._state.to_dict(), "ros2": r}
        log.info("모터: left=%.3f right=%.3f (mock=%s)", self._state.left, self._state.right, self._mock)
        return {"ok": True, "state": self._state.to_dict(), "mock": self._mock, "driver": self._driver}

    def set_twist(self, linear_m_s: float, angular_rad_s: float) -> dict[str, Any]:
        L = 0.2
        v_l = linear_m_s - (angular_rad_s * L / 2)
        v_r = linear_m_s + (angular_rad_s * L / 2)
        vmax = 1.0
        s = max(abs(v_l), abs(v_r), 1e-6)
        if s > vmax:
            v_l, v_r = v_l / s * vmax, v_r / s * vmax
        self._state.linear_m_s = linear_m_s
        self._state.angular_rad_s = angular_rad_s
        if self._driver == "yahboom" and self._t and self._t.available:
            r = self._t.pub_cmd_vel(linear_m_s, angular_rad_s)
            self._state.left = v_l
            self._state.right = v_r
            self._state.updated_s = time.time()
            return {"ok": r.get("ok"), "driver": "yahboom", "state": self._state.to_dict(), "ros2": r}
        return self.set_wheels(v_l, v_r)

    def stop(self) -> dict[str, Any]:
        if self._driver == "yahboom" and self._t and self._t.available:
            r = self._t.pub_cmd_vel(0.0, 0.0)
            self._state = MotorState()
            return {"ok": r.get("ok"), "driver": "yahboom", "state": self._state.to_dict(), "ros2": r}
        return self.set_wheels(0.0, 0.0)

    def jog(
        self,
        direction: str,
        seconds: float,
        linear_m_s: float,
        angular_rad_s: float,
    ) -> dict[str, Any]:
        """
        wheel_test.sh 처럼 짧은 시간 cmd_vel: 브라우저에서 /motors/jog 1회 호출로 사용.
        dir: forward | back | left | right
        """
        d = (direction or "").strip().lower()
        lin0 = max(0.0, min(1.0, abs(float(linear_m_s))))
        ang0 = max(0.0, min(2.0, abs(float(angular_rad_s))))
        sec = max(0.15, min(3.0, float(seconds)))
        lx, az = 0.0, 0.0
        if d == "forward":
            lx = lin0
        elif d == "back":
            lx = -lin0
        elif d == "left":
            az = ang0
        elif d == "right":
            az = -ang0
        else:
            return {
                "ok": False,
                "error": "dir must be forward, back, left, or right",
            }
        self._state.linear_m_s = lx
        self._state.angular_rad_s = az
        self._state.updated_s = time.time()
        L = 0.2
        self._state.left = lx - (az * L / 2)
        self._state.right = lx + (az * L / 2)
        if self._driver == "yahboom" and self._t and self._t.available:
            r = self._t.jog_cmd_vel(lx, az, sec)
            return {
                "ok": r.get("ok", False),
                "driver": "yahboom",
                "dir": d,
                "seconds": sec,
                "state": self._state.to_dict(),
                "ros2": r,
            }
        log.info("jog mock: %s %ss linear=%.3f ang=%.3f", d, sec, lx, az)
        return {
            "ok": True,
            "mock": True,
            "driver": self._driver,
            "dir": d,
            "seconds": sec,
            "linear_x": lx,
            "angular_z": az,
            "state": self._state.to_dict(),
        }

    @property
    def state(self) -> MotorState:
        return self._state
