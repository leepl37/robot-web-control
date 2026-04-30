"""
WebRTC 시그널링 전용 서버: robot_id 기준으로 Pi(로봇)와 브라우저(뷰어) 사이에서
offer / answer / ICE만 중계한다. 영상은 P2P로 직접 전달되며 이 서버는 미디어를 처리하지 않는다.
로봇 제어(모터/센서/PTZ)는 /api/robot/... 또는 동일 의미의 /motors/... 를 Pi(pi_control_api)로 HTTP 프록시한다.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from robot_control import router as robot_control_router
from robot_proxy import (
    aclose_httpx_client,
    motors_alias_router,
    router as robot_proxy_router,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("signaling")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    await aclose_httpx_client()


app = FastAPI(
    title="WebRTC Signaling + Robot Control Proxy",
    description=(
        "WebSocket /ws for WebRTC; HTTP /api/robot/* or /motors/* → Pi when ROBOT_API_BASE is set."
    ),
    lifespan=_lifespan,
)

# 브라우저에서 /api/robot (다른 origin·file) 호출용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# robot_id당 로봇 WebSocket 1개, 뷰어 WebSocket 1개 (나중에 뷰어를 리스트로 확장 가능)
robots: dict[str, WebSocket] = {}
viewers: dict[str, WebSocket] = {}


def _get_static_index_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "static", "index.html")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_get_static_index_path(), media_type="text/html")

# 정적 파일 (index.html에서 /static/ 경로 쓸 때)
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

# Pi FastAPI(제어/센서): 집계 /status 는 먼저, 일반 경로는 robot_proxy 캐치올(아래)
app.include_router(robot_control_router)
# export ROBOT_API_BASE=http://192.168.219.108:8080
app.include_router(robot_proxy_router)
app.include_router(motors_alias_router)


async def _send_error(ws: WebSocket, message: str) -> None:
    try:
        await ws.send_text(json.dumps({"type": "error", "message": message}))
    except Exception as e:  # noqa: BLE001
        log.warning("클라이언트에 에러 전송 실패: %s", e)


async def _send_leave_peer(peer_ws: WebSocket | None, robot_id: str, reason: str) -> None:
    if not peer_ws:
        return
    try:
        await peer_ws.send_text(
            json.dumps(
                {
                    "type": "leave",
                    "robot_id": robot_id,
                    "reason": reason,
                }
            )
        )
    except Exception as e:  # noqa: BLE001
        log.warning("상대에게 leave 전송 실패: %s", e)


def _parse_message(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
        if not isinstance(data, dict) or "type" not in data:
            return None
        return data
    except json.JSONDecodeError:
        return None


@app.websocket("/ws")
async def signaling_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    client = f"{websocket.client.host if websocket.client else '?'}"
    log.info("WebSocket 연결: %s", client)

    robot_id_local: str | None = None
    is_robot = False
    is_viewer = False

    try:
        while True:
            text = await websocket.receive_text()
            data = _parse_message(text)
            if not data:
                await _send_error(websocket, "Invalid JSON or missing 'type'")
                continue

            mtype = data.get("type")
            rid = data.get("robot_id")

            if mtype == "register":
                if data.get("role") != "robot" or not rid or not isinstance(rid, str):
                    await _send_error(websocket, "register requires role=robot and robot_id")
                    continue
                old = robots.get(rid)
                if old and old is not websocket:
                    try:
                        await old.close(code=1000, reason="replaced by new robot")
                    except Exception:  # noqa: BLE001
                        pass
                robots[rid] = websocket
                robot_id_local = rid
                is_robot = True
                log.info("로봇 등록: robot_id=%s", rid)
                continue

            if mtype == "viewer_join":
                if not rid or not isinstance(rid, str):
                    await _send_error(websocket, "viewer_join requires robot_id")
                    continue
                if rid not in robots:
                    await _send_error(
                        websocket,
                        f"No robot registered for robot_id={rid!r} (connect the Pi first).",
                    )
                    continue
                old_v = viewers.get(rid)
                if old_v and old_v is not websocket:
                    try:
                        await _send_leave_peer(old_v, rid, "replaced by new viewer")
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        await old_v.close(code=1000, reason="replaced by new viewer")
                    except Exception:  # noqa: BLE001
                        pass
                viewers[rid] = websocket
                robot_id_local = rid
                is_viewer = True
                log.info("뷰어 참가: robot_id=%s", rid)
                # Pi가 offer를 만들 수 있도록 viewer_join을 로봇 쪽으로 그대로 전달
                try:
                    await robots[rid].send_text(
                        json.dumps(
                            {
                                "type": "viewer_join",
                                "robot_id": rid,
                            }
                        )
                    )
                except Exception as e:  # noqa: BLE001
                    log.error("viewer_join을 로봇에 전달 실패: %s", e)
                    await _send_error(websocket, "Failed to reach robot; try again.")
                continue

            if mtype in ("offer", "answer", "ice"):
                if not rid or not isinstance(rid, str):
                    await _send_error(websocket, f"{mtype} requires robot_id")
                    continue
                # 로봇이 보낸 offer/ice → 뷰어로, 뷰어가 보낸 answer/ice → 로봇으로
                if mtype in ("offer", "ice") and is_robot:
                    peer = viewers.get(rid)
                    if not peer:
                        await _send_error(websocket, "No viewer connected for this robot_id")
                        continue
                    try:
                        await peer.send_text(text)
                        log.info("중계 %s 로봇→뷰어 robot_id=%s", mtype, rid)
                    except Exception as e:  # noqa: BLE001
                        log.error("뷰어로 중계 실패 %s: %s", mtype, e)
                        await _send_error(websocket, "Peer disconnected")
                elif mtype in ("answer", "ice") and is_viewer:
                    peer = robots.get(rid)
                    if not peer:
                        await _send_error(websocket, "No robot connected for this robot_id")
                        continue
                    try:
                        await peer.send_text(text)
                        log.info("중계 %s 뷰어→로봇 robot_id=%s", mtype, rid)
                    except Exception as e:  # noqa: BLE001
                        log.error("로봇으로 중계 실패 %s: %s", mtype, e)
                        await _send_error(websocket, "Peer disconnected")
                elif mtype == "answer" and is_robot:
                    await _send_error(websocket, "answer only from viewer")
                elif mtype == "offer" and is_viewer:
                    await _send_error(websocket, "offer only from robot")
                else:
                    await _send_error(websocket, "Message role mismatch: reconnect as robot or viewer")
                continue

            if mtype == "leave":
                if not rid or not isinstance(rid, str):
                    await _send_error(websocket, "leave requires robot_id")
                    continue
                log.info("leave 요청 robot_id=%s 출처=%s", rid, "robot" if is_robot else "viewer" if is_viewer else "?")
                if is_viewer and viewers.get(rid) is websocket:
                    del viewers[rid]
                    r = robots.get(rid)
                    if r:
                        await _send_leave_peer(r, rid, "viewer_left")
                elif is_robot and robots.get(rid) is websocket:
                    del robots[rid]
                    v = viewers.get(rid)
                    if v:
                        await _send_leave_peer(v, rid, "robot_left")
                continue

            await _send_error(websocket, f"Unknown or unsupported type: {mtype}")

    except WebSocketDisconnect:
        log.info("WebSocket 끊김: %s robot_id=%s", client, robot_id_local)
    finally:
        # 소켓이 끊기면 등록만 정리하고, 남은 쪽에는 leave 알림
        if robot_id_local and is_robot and robots.get(robot_id_local) is websocket:
            del robots[robot_id_local]
            v = viewers.get(robot_id_local)
            if v:
                await _send_leave_peer(v, robot_id_local, "robot_disconnected")
            log.info("로봇 제거: %s", robot_id_local)
        if robot_id_local and is_viewer and viewers.get(robot_id_local) is websocket:
            del viewers[robot_id_local]
            r = robots.get(robot_id_local)
            if r:
                await _send_leave_peer(r, robot_id_local, "viewer_disconnected")
            log.info("뷰어 제거: %s", robot_id_local)
