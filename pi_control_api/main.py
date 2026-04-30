"""
라즈베리파이 로봇 제어·센서 API (FastAPI).
ROS2 는 rclpy(우선) 또는 Docker 내 ros2 CLI 로 스크립트 .sh 를 쓰지 않고 토픽에 연동.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import config
from services.camera_control import CameraControlService
from services.imu import ImuService
from services.lidar import LidarService
from services.motors import MotorService
from services.ptz import PtzService
from services.yahboom_transport import RclpyTransport, YahboomTransport, create_yahboom_transport

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("pi_control")

# --- 요청 본문 모델 ---


class WheelsBody(BaseModel):
    left: float = Field(..., ge=-1.0, le=1.0)
    right: float = Field(..., ge=-1.0, le=1.0)


class TwistBody(BaseModel):
    linear_m_s: float = 0.0
    angular_rad_s: float = 0.0


class JogBody(BaseModel):
    """wheel_test.sh 와 같이 한 번에 몇 초 cmd_vel (웹은 /motors/jog 권장)."""

    dir: str  # forward | back | left | right
    seconds: float = Field(0.7, ge=0.15, le=3.0)
    linear_m_s: float = Field(0.18, ge=0.0, le=1.0)
    angular_rad_s: float = Field(0.35, ge=0.0, le=2.0)


class PtzAbsoluteBody(BaseModel):
    pan_deg: float = 0.0
    tilt_deg: float = 0.0
    height_mm: float | None = None


class PtzDeltaBody(BaseModel):
    d_pan: float = 0.0
    d_tilt: float = 0.0
    d_height: float = 0.0


class CameraControlBody(BaseModel):
    brightness: int | None = None
    exposure_auto: int | None = None


motors: MotorService
ptz: PtzService
cam: CameraControlService
imu: ImuService
lidar: LidarService
_yahboom: YahboomTransport | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global motors, ptz, cam, imu, lidar, _yahboom
    log.info(
        "MOCK_HW=%s MOTOR=%s PTZ=%s LIDAR=%s IMU=%s YAHBOOM_BACKEND=%s",
        config.MOCK_HARDWARE,
        config.MOTOR_DRIVER,
        config.PTZ_DRIVER,
        config.LIDAR_DRIVER,
        config.IMU_DRIVER,
        config.YAHBOOM_BACKEND,
    )
    _yahboom = create_yahboom_transport()
    yb = _yahboom
    motors = MotorService(
        config.MOTOR_DRIVER,
        yb if config.MOTOR_DRIVER == "yahboom" else None,
    )
    ptz = PtzService(
        config.PTZ_DRIVER,
        yb if config.PTZ_DRIVER == "yahboom" else None,
    )
    cam = CameraControlService(config.V4L2_DEVICE)
    imu = ImuService(
        bool(config.MOCK_HARDWARE),
        driver=config.IMU_DRIVER,
        yahboom=yb if config.IMU_DRIVER == "yahboom" else None,
    )
    lidar = LidarService(
        config.LIDAR_DRIVER,
        yb if config.LIDAR_DRIVER == "yahboom" else None,
    )
    yield
    if _yahboom:
        _yahboom.stop()
    log.info("종료.")


app = FastAPI(
    title="Pi Robot Control API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, Any]:
    yb = _yahboom
    return {
        "ok": True,
        "mock_hardware": bool(config.MOCK_HARDWARE),
        "v4l2_device": config.V4L2_DEVICE,
        "motor_driver": config.MOTOR_DRIVER,
        "ptz_driver": config.PTZ_DRIVER,
        "lidar_driver": config.LIDAR_DRIVER,
        "imu_driver": config.IMU_DRIVER,
        "yahboom_backend": getattr(yb, "backend_name", None) if yb else None,
        "yahboom_available": bool(yb and yb.available),
    }


# --- 모터 ---


@app.post("/motors/wheels")
async def post_wheels(body: WheelsBody) -> dict[str, Any]:
    return await run_in_threadpool(motors.set_wheels, body.left, body.right)


@app.post("/motors/twist")
async def post_twist(body: TwistBody) -> dict[str, Any]:
    return await run_in_threadpool(
        motors.set_twist,
        body.linear_m_s,
        body.angular_rad_s,
    )


@app.post("/motors/stop")
async def post_stop() -> dict[str, Any]:
    return await run_in_threadpool(motors.stop)


@app.post("/motors/jog")
async def post_motor_jog(body: JogBody) -> dict[str, Any]:
    """Docker exec 는 수 초 걸릴 수 있어 스레드에서 실행."""
    return await run_in_threadpool(
        motors.jog,
        body.dir,
        body.seconds,
        body.linear_m_s,
        body.angular_rad_s,
    )


@app.get("/motors/state")
async def get_motor_state() -> dict[str, Any]:
    return {"ok": True, "state": motors.state.to_dict()}


# --- Pan/Tilt ---


@app.post("/ptz/absolute")
async def post_ptz_abs(body: PtzAbsoluteBody) -> dict[str, Any]:
    """Docker+rclpy 동기 블록을 스레드에서 실행해 다른 HTTP 요청이 멈추지 않게 함."""
    return await run_in_threadpool(
        ptz.set_absolute,
        body.pan_deg,
        body.tilt_deg,
        body.height_mm,
    )


@app.post("/ptz/delta")
async def post_ptz_delta(body: PtzDeltaBody) -> dict[str, Any]:
    return await run_in_threadpool(
        ptz.move_delta,
        body.d_pan,
        body.d_tilt,
        body.d_height,
    )


@app.get("/ptz/state")
async def get_ptz() -> dict[str, Any]:
    return {"ok": True, "state": ptz.state.to_dict()}


# --- 카메라 (V4L2) ---


@app.get("/camera/capabilities")
async def get_cam_cap() -> dict[str, Any]:
    return cam.get_capabilities()


@app.post("/camera/controls")
async def post_cam_ctrl(body: CameraControlBody) -> dict[str, Any]:
    return cam.set_controls(body.brightness, body.exposure_auto)


@app.get("/camera/state")
async def get_cam_st() -> dict[str, Any]:
    return {"ok": True, "state": cam.state.to_dict()}


# --- IMU / LiDAR ---


@app.get("/sensors/imu")
async def get_imu() -> dict[str, Any]:
    return imu.read()


@app.get("/sensors/lidar/scan")
async def get_lidar() -> dict[str, Any]:
    return lidar.scan_2d()


@app.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket) -> None:
    await ws.accept()
    n = 0
    try:
        while True:
            imu_p = imu.read()
            out: dict[str, Any] = {"imu": imu_p}
            if n % 5 == 0:
                out["lidar"] = lidar.scan_2d()
            n += 1
            await ws.send_text(json.dumps(out, default=str))
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.warning("telemetry ws: %s", e)
