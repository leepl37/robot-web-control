"""V4L2 USB 카메라에서 MJPEG 프레임을 읽어 aiortc VideoStreamTrack으로 넘긴다 (예: /dev/video0)."""

import logging

import cv2
import numpy as np
from aiortc import VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from av import VideoFrame

log = logging.getLogger("camera_track")


class CameraVideoTrack(VideoStreamTrack):
    """OpenCV 캡처 (V4L2, MJPG, 640×480, 30fps) → av VideoFrame (bgr24)."""

    kind = "video"

    def __init__(self, device: str, width: int, height: int, fps: int) -> None:
        super().__init__()
        self._device = device
        self._width = width
        self._height = height
        self._fps = fps
        self._cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        if not self._cap.isOpened():
            msg = (
                f"카메라를 열 수 없음: {device!r}. "
                "케이블·권한(video 그룹)·다른 프로세스 점유(MJPEG 서버 등)를 확인하세요. "
                "lsof / fuser /dev/video0 로 점유 확인."
            )
            log.error(msg)
            raise RuntimeError(msg)

        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        self._cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_FPS, fps)

        actual_fourcc = int(self._cap.get(cv2.CAP_PROP_FOURCC))
        fourcc_str = "".join([chr((actual_fourcc >> 8 * i) & 0xFF) for i in range(4)])
        log.info(
            "카메라 열림: %s, FOURCC=%r, 해상도=%sx%s, fps=%s",
            device,
            fourcc_str,
            int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            self._cap.get(cv2.CAP_PROP_FPS),
        )

    def stop(self) -> None:  # type: ignore[override]
        if hasattr(self, "_cap") and self._cap is not None:
            self._cap.release()
            self._cap = None
            log.info("카메라 핸들 해제 (%s)", self._device)
        super().stop()

    async def recv(self) -> VideoFrame:
        if self.readyState != "live":
            raise MediaStreamError

        # 기본 VideoStreamTrack.next_timestamp()가 30fps 타이밍(pts, time_base)을 맞춤
        pts, time_base = await self.next_timestamp()
        ok, bgr = self._cap.read()
        if not ok or bgr is None:
            log.warning("프레임 읽기 실패, 검은 화면으로 대체")
            bgr = np.zeros((self._height, self._width, 3), dtype=np.uint8)

        frame = VideoFrame.from_ndarray(bgr, format="bgr24")
        frame.pts = pts
        frame.time_base = time_base
        return frame
