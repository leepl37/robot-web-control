"""
라즈베리파이 쪽 WebRTC 송신: 메인 시그널 서버에 register 후 viewer_join을 기다렸다가
CameraVideoTrack으로 offer를 만든다. offer/answer/ICE는 WebSocket으로만 주고가며, 영상은 P2P.
같은 LAN 테스트용으로 STUN/TURN은 끔 — NAT 우회가 필요하면 RTCConfiguration에 iceServers 추가.
"""

import asyncio
import json
import logging
import signal
import sys
from typing import Any

import websockets
from aiortc import (
    RTCConfiguration,
    RTCIceCandidate,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.sdp import candidate_from_sdp, candidate_to_sdp

from camera_track import CameraVideoTrack
from config import (
    CAMERA_DEVICE,
    FPS,
    HEIGHT,
    MAIN_SERVER_WS_URL,
    ROBOT_ID,
    WIDTH,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("publisher")

# 정상 종료: systemd / Ctrl+C
_stop = asyncio.Event()


def _install_signal_handlers() -> None:
    def _h(*_: Any) -> None:
        _stop.set()

    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _h)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _h)


def _ice_from_payload(payload: dict[str, Any]) -> RTCIceCandidate | None:
    """브라우저가 보낸 RTCIceCandidateInit(JSON)을 aiortc RTCIceCandidate로 변환."""
    c = payload.get("candidate")
    if not c or not isinstance(c, dict):
        return None
    cand = c.get("candidate")
    if not cand or not isinstance(cand, str):
        return None
    # aiortc는 candidate= 키워드가 아니라 SDP 한 줄("candidate:... typ host")을 파싱
    try:
        ice = candidate_from_sdp(cand.strip())
    except Exception as e:  # noqa: BLE001
        log.warning("ICE SDP 파싱 실패: %s (일부: %r)", e, cand[:100])
        return None
    mid = c.get("sdpMid")
    idx = c.get("sdpMLineIndex")
    if mid is not None:
        ice.sdpMid = str(mid) if not isinstance(mid, str) else mid
    if idx is not None:
        ice.sdpMLineIndex = int(idx)  # type: ignore[assignment]
    elif mid is None:
        ice.sdpMLineIndex = 0
    return ice


class RobotSession:
    """한 WebSocket 세션에 붙는 RTCPeerConnection + 카메라 트랙 상태."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._pc: RTCPeerConnection | None = None
        self._track: CameraVideoTrack | None = None
        # 뷰어 쪽 ICE가 answer보다 먼저 오면 여기 쌓았다가 setRemoteDescription 이후에 추가
        self._pending_remote_ice: list[RTCIceCandidate] = []

    async def close(self) -> None:
        if self._pc is not None:
            await self._pc.close()
            self._pc = None
        if self._track is not None:
            self._track.stop()
            self._track = None
        self._pending_remote_ice.clear()
        log.info("PeerConnection 정리 완료")

    async def on_viewer_join(self) -> None:
        log.info("viewer_join → 새 RTCPeerConnection + offer")
        await self.close()
        self._track = CameraVideoTrack(CAMERA_DEVICE, WIDTH, HEIGHT, FPS)
        # 로컬 Wi-Fi 전용: STUN 없음 (필요 시 iceServers에 추가)
        self._pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self._pc.addTrack(self._track)

        @self._pc.on("icecandidate")
        async def on_ice(candidate: RTCIceCandidate | None) -> None:
            if candidate is None:
                log.info("로컬 ICE 수집 종료 (null candidate)")
                return
            # RTCIceCandidate는 .candidate 문자열이 없음 → SDP 한 줄로 직렬화
            line = candidate_to_sdp(candidate)
            if not line.startswith("candidate:"):
                line = f"candidate:{line}"
            out = {
                "type": "ice",
                "robot_id": ROBOT_ID,
                "candidate": {
                    "candidate": line,
                    "sdpMid": candidate.sdpMid,
                    "sdpMLineIndex": candidate.sdpMLineIndex,
                },
            }
            try:
                await self._ws.send(json.dumps(out))
            except Exception as e:  # noqa: BLE001
                log.warning("ICE 전송 실패: %s", e)

        @self._pc.on("connectionstatechange")
        def on_conn_state() -> None:
            p = self._pc
            if p is not None:
                log.info("connectionState=%s", p.connectionState)

        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)
        await self._ws.send(
            json.dumps(
                {
                    "type": "offer",
                    "robot_id": ROBOT_ID,
                    "sdp": self._pc.localDescription.sdp,
                    "sdp_type": "offer",
                }
            )
        )
        log.info("시그널 서버로 offer 전송됨")

    async def on_answer(self, data: dict[str, Any]) -> None:
        if self._pc is None:
            log.warning("answer 수신인데 PeerConnection 없음, 무시")
            return
        sdp = data.get("sdp")
        t = data.get("sdp_type") or "answer"
        if not sdp:
            log.warning("answer에 sdp 없음, 무시")
            return
        await self._pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=t))
        log.info("setRemoteDescription(answer) 완료")
        for c in self._pending_remote_ice:
            try:
                await self._pc.addIceCandidate(c)
            except Exception as e:  # noqa: BLE001
                log.warning("addIceCandidate (백로그): %s", e)
        self._pending_remote_ice.clear()

    async def on_remote_ice(self, data: dict[str, Any]) -> None:
        ice = _ice_from_payload(data)
        if ice is None:
            return
        if self._pc is None:
            return
        if self._pc.remoteDescription is None:
            self._pending_remote_ice.append(ice)
            log.info("원격 ICE 버퍼링 (answer 대기 중)")
            return
        try:
            await self._pc.addIceCandidate(ice)
        except Exception as e:  # noqa: BLE001
            log.warning("addIceCandidate: %s", e)


async def run_signaling() -> None:
    log.info("연결 시도: %s", MAIN_SERVER_WS_URL)
    async with websockets.connect(MAIN_SERVER_WS_URL) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "register",
                    "role": "robot",
                    "robot_id": ROBOT_ID,
                }
            )
        )
        log.info("register 전송 (robot_id=%s)", ROBOT_ID)
        session = RobotSession(ws)
        try:
            while not _stop.is_set():
                # recv()가 영구 블로킹이면 정지 시그널에 반응 못 하므로 타임아웃으로 루프
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if isinstance(msg, bytes):
                    msg = msg.decode("utf-8", errors="replace")
                try:
                    data = json.loads(msg)
                except json.JSONDecodeError:
                    log.warning("JSON 아님: %s", msg[:200])
                    continue
                mtype = data.get("type")
                if mtype == "error":
                    log.error("서버 오류: %s", data.get("message", data))
                    continue
                if mtype == "viewer_join":
                    try:
                        await session.on_viewer_join()
                    except Exception:  # noqa: BLE001
                        log.exception("viewer_join 처리 실패 (카메라/PeerConnection)")
                elif mtype == "answer":
                    try:
                        await session.on_answer(data)
                    except Exception:  # noqa: BLE001
                        log.exception("answer 처리 실패")
                elif mtype == "ice":
                    try:
                        await session.on_remote_ice(data)
                    except Exception:  # noqa: BLE001
                        log.exception("ICE 처리 실패")
                elif mtype == "leave":
                    log.info("상대 종료: %s", data.get("reason", ""))
                    await session.close()
                else:
                    log.info("처리하지 않는 메시지 type=%s", mtype)
        finally:
            await session.close()


async def main() -> None:
    _install_signal_handlers()
    delay = 5.0
    while not _stop.is_set():
        try:
            await run_signaling()
        except asyncio.CancelledError:
            break
        except Exception:  # noqa: BLE001
            log.exception("%s초 뒤 재연결", delay)
        if _stop.is_set():
            break
        await asyncio.sleep(delay)
    log.info("종료.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
