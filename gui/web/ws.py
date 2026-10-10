"""
ws.py — WebSocket 连接管理器

设计：
    - 浏览器连 `/ws` → 服务端 accept → 加入 clients
    - 合成跑在 FastAPI BackgroundTasks（线程池）里
    - 合成线程的 on_log / on_progress 回调 → ws_manager.push(...)
    - push 通过 asyncio.run_coroutine_threadsafe 把消息投回主事件循环 → 广播给所有 client

为什么需要这个间接层：
    FastAPI 用 asyncio 主循环；TTS 合成是阻塞调用，必须放线程池。
    线程池里没有 asyncio 上下文，没法直接 await websocket.send_json()。
    asyncio.run_coroutine_threadsafe 是 Python 官方推荐的跨线程→事件循环桥。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Set

from fastapi import WebSocket

log = logging.getLogger(__name__)


class WSManager:
    def __init__(self) -> None:
        self.clients: Set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """主事件循环启动时调用一次，让 push 能从任何线程广播。"""
        self.loop = loop
        log.info("WSManager: 主事件循环已注册")

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)
        log.info(f"WSManager: 客户端连接 (当前 {len(self.clients)} 个)")
        # 立刻回一条 hello，让前端确认通道通了
        try:
            await ws.send_json({"type": "hello", "message": "WebSocket 已连接"})
        except Exception as e:
            log.warning(f"WSManager: 初始 hello 失败: {e}")

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)
        log.info(f"WSManager: 客户端断开 (剩 {len(self.clients)} 个)")

    def push(self, message: dict) -> None:
        """
        线程安全广播。从合成线程调用，调度到主事件循环给所有客户端发。

        用法：
            ws_manager.push({"type": "log", "level": "info", "message": "..."})
            ws_manager.push({"type": "progress", "done": 5, "total": 32})
            ws_manager.push({"type": "done", "output": "output/xxx.mp3", "order": "..."})
            ws_manager.push({"type": "error", "message": "..."})
        """
        if not self.clients or not self.loop:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._send_all(message), self.loop)
        except Exception as e:
            log.warning(f"WSManager: push 失败: {e}")

    async def _send_all(self, message: dict) -> None:
        # 复制一份避免迭代时修改
        for client in list(self.clients):
            try:
                await client.send_json(message)
            except Exception:
                # 客户端断开了，清理掉
                self.clients.discard(client)


# 全局单例（FastAPI 进程内）
ws_manager = WSManager()
