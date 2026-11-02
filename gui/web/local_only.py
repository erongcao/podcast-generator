"""只允许本机访问会花钱、会读成片的路径。

看板不登录也能用。服务默认绑在 127.0.0.1；这层是防止以后改成
0.0.0.0 时，局域网里的人直接打到 /api、/output、/ws。

页面本身（/、/login、/editor、/health、/static）保持开放，这样看错地址时
浏览器还能打开外壳，而不是一片连接失败。

用纯 ASGI 中间件，不用 BaseHTTPMiddleware。后者会把 WebSocket 弄断。
"""
from __future__ import annotations

import json

LOCAL_HOSTS = frozenset({
    "127.0.0.1",
    "::1",
    "localhost",
    "::ffff:127.0.0.1",
    "testclient",  # Starlette TestClient 的默认 client host
})

_DENY_BODY = json.dumps({"detail": "仅允许本机访问"}, ensure_ascii=False).encode("utf-8")


def is_local_client(host: str | None) -> bool:
    if not host:
        return False
    if isinstance(host, bytes):
        host = host.decode("utf-8", "replace")
    host = str(host).strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host in LOCAL_HOSTS


def _guarded(path: str) -> bool:
    return path.startswith("/api") or path.startswith("/output") or path == "/ws"


class LocalOnlyMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if not _guarded(path):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        host = client[0] if client else None
        if is_local_client(host):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(_DENY_BODY)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": _DENY_BODY})
