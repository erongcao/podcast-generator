"""本机限制。用一个小的 FastAPI，不导入 gui.web.app（那会在启动时改真实数据库）。

这台机器上的 Starlette 把 TestClient 的 client 写死成 testclient，
构造函数没有 client= 参数。自定义来源地址改走 ASGI scope。
"""
import json

import anyio
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from gui.web.local_only import LocalOnlyMiddleware, is_local_client


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(LocalOnlyMiddleware)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/")
    def index():
        return {"page": "index"}

    @app.get("/api/version")
    def version():
        return {"version": "test"}

    @app.get("/output/a.mp3")
    def output_file():
        return {"file": True}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"type": "hello"})
        await websocket.receive_text()

    return app


def _http(app, path: str, host: str):
    messages = []

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": (host, 50000),
        "server": ("testserver", 80),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    async def run():
        await app(scope, receive, send)

    anyio.run(run)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return start["status"], headers, body


def _ws_messages(app, host: str):
    messages = []
    incoming = [
        {"type": "websocket.connect"},
        {"type": "websocket.receive", "text": "ping"},
    ]

    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": "/ws",
        "raw_path": b"/ws",
        "query_string": b"",
        "headers": [],
        "client": (host, 50000),
        "server": ("testserver", 80),
        "subprotocols": [],
    }

    async def receive():
        if incoming:
            return incoming.pop(0)
        return {"type": "websocket.disconnect", "code": 1000}

    async def send(message):
        messages.append(message)

    async def run():
        await app(scope, receive, send)

    anyio.run(run)
    return messages


def test_is_local_client_hosts():
    assert is_local_client("127.0.0.1")
    assert is_local_client("::1")
    assert is_local_client("localhost")
    assert is_local_client("::ffff:127.0.0.1")
    assert is_local_client("testclient")
    assert not is_local_client("203.0.113.5")
    assert not is_local_client(None)
    assert not is_local_client("")


def test_remote_api_and_output_are_forbidden():
    app = _app()
    for path in ("/api/version", "/output/a.mp3"):
        status, headers, body = _http(app, path, "203.0.113.5")
        assert status == 403, path
        assert headers["content-type"].startswith("application/json")
        assert json.loads(body) == {"detail": "仅允许本机访问"}
        assert headers["content-length"] == str(len(body))


def test_remote_pages_stay_open():
    app = _app()
    status, _, body = _http(app, "/health", "203.0.113.5")
    assert status == 200
    assert json.loads(body) == {"ok": True}
    status, _, body = _http(app, "/", "203.0.113.5")
    assert status == 200
    assert json.loads(body) == {"page": "index"}


def test_loopback_api_is_allowed():
    app = _app()
    status, _, body = _http(app, "/api/version", "127.0.0.1")
    assert status == 200
    assert json.loads(body) == {"version": "test"}
    status, _, body = _http(app, "/output/a.mp3", "::1")
    assert status == 200
    assert json.loads(body) == {"file": True}


def test_testclient_default_host_is_allowed():
    client = TestClient(_app())
    assert client.get("/api/version").status_code == 200
    assert client.get("/health").json() == {"ok": True}


def test_remote_websocket_closes():
    messages = _ws_messages(_app(), "203.0.113.5")
    assert messages[0]["type"] == "websocket.close"
    assert messages[0]["code"] == 1008


def test_local_websocket_connects():
    messages = _ws_messages(_app(), "127.0.0.1")
    assert messages[0]["type"] == "websocket.accept"
    assert any(m.get("type") == "websocket.send" for m in messages)
