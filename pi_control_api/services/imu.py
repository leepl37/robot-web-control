"""
IMU. mock | yahboom (ROS2 sensor_msgs/Imu, 토픽 YAHBOOM_IMU_TOPIC).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from services.yahboom_transport import YahboomTransport

log = logging.getLogger("imu")


@dataclass
class ImuSample:
    acc_x: float
    acc_y: float
    acc_z: float
    gyr_x: float
    gyr_y: float
    gyr_z: float
    t_s: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accel_mps2": {"x": self.acc_x, "y": self.acc_y, "z": self.acc_z},
            "gyr_rad_s": {"x": self.gyr_x, "y": self.gyr_y, "z": self.gyr_z},
            "t_s": self.t_s,
        }


class ImuService:
    def __init__(
        self,
        mock_hw_flag: bool,
        driver: str = "mock",
        yahboom: "YahboomTransport | None" = None,
    ) -> None:
        self._mock_hw = mock_hw_flag
        self._driver = driver
        self._t = yahboom
        self._i = 0.0

    def read(self) -> dict[str, Any]:
        if self._driver == "yahboom" and self._t and self._t.available:
            r = self._t.get_imu()
            r.setdefault("driver", "yahboom")
            return r
        self._i += 0.05
        if self._driver == "mock" or self._mock_hw:
            t = time.time()
            s = ImuSample(
                acc_x=0.0,
                acc_y=0.0,
                acc_z=9.81 + 0.1 * math.sin(self._i),
                gyr_x=0.0,
                gyr_y=0.0,
                gyr_z=0.01 * math.cos(self._i),
                t_s=t,
            )
            return {"ok": True, "mock": True, "sample": s.to_dict()}
        return {
            "ok": False,
            "error": "IMU: mock이 아닌데 yahboom도 아님; IMU_DRIVER 설정",
        }
