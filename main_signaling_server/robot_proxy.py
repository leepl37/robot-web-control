"""
메인 PC의 FastAPI가 브라우저(동일 출처)에서 Pi 제어 API를 부를 수 있도록
HTTP 요청을 config.ROBOT_API_BASE(예: http://192.168.219.108:8080)로 그대로 전달.
ROBOT_API_BASE가 비어 있으면 503 반환.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response

import config

log = logging.getLogger("robot_proxy")

router = APIRouter(prefix="/api/robot", tags=["robot-proxy"])
# 브라우저에서 /api/robot 없이 호출할 때용 (예: POST /motors/jog → Pi /motors/jog)
motors_alias_router = APIRouter(tags=["robot-proxy"])

_httpx: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _httpx
    if _httpx is None:
        if not config.ROBOT_API_BASE:
            raise RuntimeError("ROBOT_API_BASE is not set")
        _httpx = httpx.AsyncClient(base_url=config.ROBOT_API_BASE, timeout=30.0)
    return _httpx


async def aclose_httpx_client() -> None:
    global _httpx
    if _httpx is not None:
        await _httpx.aclose()
        _httpx = None


@router.get("/_proxy_status")
async def proxy_status() -> dict[str, Any]:
    return {
        "enabled": bool(config.ROBOT_API_BASE),
        "robot_api_base": config.ROBOT_API_BASE or None,
    }


async def proxy_pi_path(path: str, request: Request) -> Response:
    """Pi ROBOT_API_BASE 기준 상대 경로 path (예 motors/jog, 빈 문자열이면 /)."""
    if not config.ROBOT_API_BASE:
        return Response(
            content=json.dumps(
                {"ok": False, "error": "ROBOT_API_BASE not set on main server"}
            ),
            status_code=503,
            media_type="application/json",
        )
    client = get_client()
    body = await request.body()
    url = f"/{path}" if path else "/"
    if not url.startswith("/"):
        url = "/" + url
    try:
        r = await client.request(
            request.method,
            url,
            content=body if body else None,
            params=request.query_params,
            headers={
                k: v
                for k, v in request.headers.items()
                if k.lower() not in ("host", "connection", "content-length")
            },
        )
    except httpx.RequestError as e:
        log.error("프록시 실패: %s", e)
        return Response(
            content=json.dumps(
                {"ok": False, "error": "proxy request failed: " + str(e)[:200]}
            ),
            status_code=502,
            media_type="application/json",
        )
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/json"),
    )


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def forward_to_robot(path: str, request: Request) -> Response:
    return await proxy_pi_path(path, request)


@motors_alias_router.api_route(
    "/motors/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def forward_motors_shortcut(path: str, request: Request) -> Response:
    """Pi의 /motors/* 와 동일. 클라이언트가 /api/robot 접두 없이 호출할 때."""
    return await proxy_pi_path(f"motors/{path}", request)
