"""进程内单线程任务队列。

合成（三个引擎）和脚本生成共用这一条队列。一次只跑一个，WebSocket
日志不会搅在一起，也不会同时打两份云端额度。

task_id 放在 ContextVar 里，而且必须在工作线程里 set。ContextVar 不会
跨线程。豆包的日志 handler 跑在同一个线程的 asyncio.run 里面，所以
这里 set 一次，后面的 ws_manager.push 都能读到。

本模块不 import ws，避免和 ws.py 循环引用。
"""
from __future__ import annotations

import logging
import queue
import threading
from contextvars import ContextVar
from typing import Any, Callable

log = logging.getLogger(__name__)

current_task_id: ContextVar[str | None] = ContextVar("podcast_current_task_id", default=None)

_q: queue.Queue[tuple[Callable[..., Any], tuple, dict, str | None]] = queue.Queue()
_lock = threading.Lock()
_inflight = 0
_worker: threading.Thread | None = None


def enqueue(fn: Callable[..., Any], *args: Any, task_id: str | None = None, **kwargs: Any) -> int:
    """入队。返回这个任务前面还有几个（正在跑的算一个）。"""
    start_worker()
    with _lock:
        ahead = _inflight + _q.qsize()
    _q.put((fn, args, kwargs, task_id))
    return ahead


def start_worker() -> None:
    """启动守护线程。已经活着就什么都不做。"""
    global _worker
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_loop, name="podcast-jobs", daemon=True)
        _worker.start()


def _loop() -> None:
    global _inflight
    while True:
        fn, args, kwargs, task_id = _q.get()
        token = current_task_id.set(task_id)
        with _lock:
            _inflight += 1
        try:
            fn(*args, **kwargs)
        except Exception:
            log.exception("job failed: %s", task_id or getattr(fn, "__name__", fn))
        finally:
            current_task_id.reset(token)
            with _lock:
                _inflight -= 1
            _q.task_done()


def wait_idle(timeout: float = 30.0) -> bool:
    """等到已经入队的任务都跑完。给测试用。

    用 Queue.join，不用「inflight == 0 且队列空」轮询。工作线程在
    get() 和 inflight += 1 之间有一小段空窗，轮询会把那一窗当成已经空闲。
    """
    done = threading.Event()

    def _join() -> None:
        _q.join()
        done.set()

    threading.Thread(target=_join, name="podcast-jobs-join", daemon=True).start()
    return done.wait(timeout)
