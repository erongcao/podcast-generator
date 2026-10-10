#!/usr/bin/env python3
"""
GUI 共享工具模块
"""

import asyncio
import functools
import threading
import tkinter as tk


# ============== 线程安全检查 ==============

def is_main_thread() -> bool:
    """当前线程是不是 Tk 主线程。Tk widget 调用必须在主线程，否则会触发
    canvas GC 崩溃（macOS 27 / Tcl-Tk 9 上 `Tk_FreeGC + ConfigurePolygon`
    的 SIGSEGV，实测确认）。"""
    return threading.current_thread() is threading.main_thread()


def assert_main_thread(context: str = ""):
    """调试用：在非主线程调用时直接抛 RuntimeError，避免安静的死循环调试。"""
    if not is_main_thread():
        raise RuntimeError(
            f"Tk widget 调用不在主线程！{context}\n"
            f"  当前线程: {threading.current_thread().name}\n"
            f"  用 self._ui(...) 走 after(0,...) 跳回主线程"
        )


# ============== run_async：在 worker 线程跑协程 ==============

def run_async(coro):
    """在新线程 + 新事件循环中运行异步协程。

    ⚠️ 调用约束：coro 内部不能直接调 Tk widget 方法（set_status / progress.set /
    configure / winfo_* / log 等）。所有 widget 操作必须通过 self._ui(fn, *args)
    跳回主线程。这是 macOS 27 / Tcl-Tk 9 上 `Tk_FreeGC + ConfigurePolygon`
    SIGSEGV 的根因 —— CTk * 内部都嵌 Tk canvas，worker 线程碰 canvas 必崩。

    旧代码（崩溃前）直接 self.set_status(...) 在 worker 线程里跑，会间歇性
    触发 canvas GC 段错误。
    """
    def _thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()
    threading.Thread(target=_thread, daemon=True).start()

