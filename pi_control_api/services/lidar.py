"""2D LiDAR: mock | yahboom (ROS2 /robot3/scan)."""

from __future__ import annotations

import logging
import math
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from services.yahboom_transport import YahboomTransport

log = logging.getLogger("lidar")


class LidarService:
    def __init__(self, driver: str, yahboom: "YahboomTransport | None" = None) -> None:
        self._driver = driver
        self._t = yahboom
        self._mock = driver == "mock"
        self._i = 0.0

    def scan_2d(self) -> dict[str, Any]:
        if self._driver == "yahboom":
            if not self._t or not self._t.available:
                return {"ok": False, "error": "Yahboom ROS2 백엔드 없음 (rclpy/Docker 설정)"}
            return self._t.get_laser_scan()
        self._i += 0.1
        if self._mock:
            n = 36
            angles = [2 * math.pi * k / n for k in range(n)]
            ranges = [2.0 + 0.3 * math.sin(a + self._i) for a in angles]
            return {
                "ok": True,
                "mock": True,
                "n": n,
                "angle_min_rad": 0.0,
                "angle_max_rad": 2 * math.pi,
                "ranges_m": ranges,
                "t_s": time.time(),
            }
        return {"ok": False, "error": "lidar: driver not configured"}
