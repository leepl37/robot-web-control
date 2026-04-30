"""
USB 카메라( V4L2 ) 보조 제어. v4l2-ctl 이 있으면 밝기/노출 등 설정.
없으면 no-op + 상태만 JSON으로 반환.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from time import time
from typing import Any

log = logging.getLogger("camera_control")


@dataclass
class CameraState:
    brightness: int | None = None
    exposure_auto: int | None = None
    updated_s: float = field(default_factory=time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "brightness": self.brightness,
            "exposure_auto": self.exposure_auto,
            "updated_s": self.updated_s,
        }


class CameraControlService:
    def __init__(self, device: str) -> None:
        self._dev = device
        self._state = CameraState()
        self._v4l2 = shutil.which("v4l2-ctl") is not None

    def get_capabilities(self) -> dict[str, Any]:
        if not self._v4l2:
            return {"v4l2_ctl": False, "message": "v4l2-ctl not installed. apt install v4l-utils"}
        try:
            out = subprocess.check_output(
                ["v4l2-ctl", "-d", self._dev, "-l"],
                text=True,
                timeout=2,
            )
            return {"v4l2_ctl": True, "controls": out[:4000]}
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
            log.warning("v4l2-ctl -l failed: %s", e)
            return {"v4l2_ctl": True, "error": str(e)}

    def set_controls(self, brightness: int | None, exposure_auto: int | None) -> dict[str, Any]:
        if brightness is not None:
            self._state.brightness = int(brightness)
        if exposure_auto is not None:
            self._state.exposure_auto = int(exposure_auto)
        self._state.updated_s = time()

        if not self._v4l2:
            return {
                "ok": True,
                "applied": False,
                "reason": "v4l2-ctl not available; values stored in API only",
                "state": self._state.to_dict(),
            }
        try:
            args = ["v4l2-ctl", "-d", self._dev]
            if self._state.brightness is not None:
                args.extend(["-c", f"brightness={self._state.brightness}"])
            if self._state.exposure_auto is not None:
                args.extend(["-c", f"exposure_auto={self._state.exposure_auto}"])
            if len(args) > 3:
                subprocess.check_call(args, timeout=2)
            return {"ok": True, "applied": True, "state": self._state.to_dict()}
        except (subprocess.CalledProcessError, OSError) as e:
            log.error("v4l2-ctl: %s", e)
            return {"ok": False, "error": str(e), "state": self._state.to_dict()}

    @property
    def state(self) -> CameraState:
        return self._state
