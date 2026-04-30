"""
메인 PC: 로봇(Pi) 제어 API는 HTTP로 Pi 로 프록시되고, 여기서는
동일 출처에서 쓰기 쉬운 집계·상태 엔드포인트만 둔다.
실제 /motors, /sensors 는 GET/POST /api/robot/... → Pi 경로로 전달(robot_proxy).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter

import config

log = logging.getLogger("robot_control")

router = APIRouter(prefix="/api/robot", tags=["robot-control"])


@router.get("/status")
async def robot_status() -> dict[str, Any]:
    """
    메인 서버 + Pi(ROBOT_API_BASE) 연결 요약. 브라우저/CI에서 스모크 테스트용.
    """
    out: dict[str, Any] = {
        "ok": bool(config.ROBOT_API_BASE),
        "main": {
            "role": "main_signaling_server",
            "signaling_path": "/ws",
            "proxy_prefix": "/api/robot",
        },
        "robot_api_base": config.ROBOT_API_BASE or None,
        "pi": None,
    }
    if not config.ROBOT_API_BASE:
        out["ok"] = False
        out["error"] = "ROBOT_API_BASE is not set (e.g. http://192.168.219.108:8080 or use run_server.sh)"
        return out

    url = f"{config.ROBOT_API_BASE}/health"
    try:
        async with httpx.AsyncClient(timeout=config.ROBOT_API_PROBE_TIMEOUT) as client:
            r = await client.get(url)
        body: str | dict[str, Any]
        ct = (r.headers.get("content-type") or "").lower()
        if "json" in ct:
            try:
                body = r.json()
            except Exception:  # noqa: BLE001
                body = r.text[:2000]
        else:
            body = r.text[:2000] if r.text else ""
        out["pi"] = {
            "reachable": True,
            "health_url": url,
            "http_status": r.status_code,
            "body": body,
        }
        out["ok"] = 200 <= r.status_code < 300
    except httpx.RequestError as e:
        log.warning("Pi /health probe failed: %s", e)
        out["ok"] = False
        out["pi"] = {
            "reachable": False,
            "health_url": url,
            "error": str(e)[:300],
        }
    return out
