"""
Pan / Tilt (높이는 이 구동에 없을 수 있음). mock | yahboom (servo s1/s2, Int32).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from services.yahboom_transport import YahboomTransport

log = logging.getLogger("ptz")


@dataclass
class PtzState:
    pan_deg: float = 0.0
    tilt_deg: float = 0.0
    height_mm: float = 0.0
    updated_s: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pan_deg": self.pan_deg,
            "tilt_deg": self.tilt_deg,
            "height_mm": self.height_mm,
            "updated_s": self.updated_s,
        }


class PtzService:
    def __init__(self, driver: str, yahboom: "YahboomTransport | None" = None) -> None:
        self._driver = driver
        self._t = yahboom
        self._s = PtzState()
        self._mock = driver == "mock"
        self._pan_limit = (-90.0, 20.0)
        self._tilt_limit = (-90.0, 90.0)
        self._h_limit = (0.0, 200.0)

    def set_absolute(self, pan_deg: float, tilt_deg: float, height_mm: float | None) -> dict[str, Any]:
        if self._driver == "yahboom" and (not self._t or not self._t.available):
            return {"ok": False, "error": "Yahboom ROS2 백엔드 없음 (rclpy/Docker 설정)"}
        self._s.pan_deg = self._clamp(pan_deg, *self._pan_limit)
        self._s.tilt_deg = self._clamp(tilt_deg, *self._tilt_limit)
        if height_mm is not None:
            self._s.height_mm = self._clamp(height_mm, *self._h_limit)
        self._s.updated_s = time.time()
        if self._driver == "yahboom" and self._t and self._t.available:
            r = self._t.set_pan_tilt_degrees(int(round(self._s.pan_deg)), int(round(self._s.tilt_deg)))
            extra: dict[str, Any] = {}
            if height_mm is not None and height_mm != 0:
                extra["height_note"] = "Yahboom 서보는 pan/tilt만 (높이는 별도 액츄에이터)"
            return {"ok": r.get("ok"), "driver": "yahboom", "state": self._s.to_dict(), "ros2": r, **extra}
        log.info("PTZ: pan=%.1f tilt=%.1f (mock=%s)", self._s.pan_deg, self._s.tilt_deg, self._mock)
        return {"ok": True, "state": self._s.to_dict(), "mock": self._mock, "driver": self._driver}

    def move_delta(self, d_pan: float, d_tilt: float, d_height: float) -> dict[str, Any]:
        next_pan = self._clamp(self._s.pan_deg + d_pan, *self._pan_limit)
        next_tilt = self._clamp(self._s.tilt_deg + d_tilt, *self._tilt_limit)
        next_height = self._clamp(self._s.height_mm + d_height, *self._h_limit)

        # Docker CLI + 버튼 한 축 이동이면 해당 서보만 보낸다.
        # 두 축을 모두 보내면 camera_pan.sh 명령의 --spin-time 2가 두 번 걸려 체감 4초가 된다.
        if (
            self._driver == "yahboom"
            and self._t
            and self._t.available
            and getattr(self._t, "backend_name", "") == "docker_cli"
            and d_height == 0
            and ((d_pan != 0 and d_tilt == 0) or (d_pan == 0 and d_tilt != 0))
        ):
            self._s.pan_deg = next_pan
            self._s.tilt_deg = next_tilt
            self._s.height_mm = next_height
            self._s.updated_s = time.time()
            if d_pan != 0:
                r = self._t.set_pan_degrees(int(round(self._s.pan_deg)))
                axis = "pan"
            else:
                r = self._t.set_tilt_degrees(int(round(self._s.tilt_deg)))
                axis = "tilt"
            return {
                "ok": r.get("ok"),
                "driver": "yahboom",
                "axis": axis,
                "state": self._s.to_dict(),
                "ros2": r,
            }

        return self.set_absolute(next_pan, next_tilt, next_height)

    @staticmethod
    def _clamp(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))

    @property
    def state(self) -> PtzState:
        return self._s
