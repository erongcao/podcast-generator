#!/usr/bin/env python3
"""
Qt 异步工具 —— 用 QThreadPool + QRunnable + Signal 替代旧 customtkinter 版的
`run_async` + `_ui()` + `_ui_get()` 那套 hack。

设计原则（Qt 的做法 vs. 旧 hack）：
  - 旧：worker 线程跑 asyncio；widget 调 _ui(self.fn, *args) 走 after(0,...) 跳回主线程
  - 新：worker 线程跑 QRunnable；通过 Signal + 驻主线程的 Controller QObject 跨线程回主线程
  - 旧：UI 读 StringVar 用 queue.Queue 同步阻塞等 after(0) 返回
  - 新：UI 读用 QMetaObject.invokeMethod + bridge 对象（moveToThread）同步等主线程返回

为什么这比 Tk 干净：
  - Qt 的 Signal/Slot 系统是**框架级**的跨线程原语，不像 after(0) 是应用层 hack
  - Qt 主线程规则和 Tk 一样（widget 必须在主线程），但 Qt 提供工具让你**写错都难**
  - 不需要任何 _ui/_ui_get monkey patch

注意一个坑：
  - signal.connect(callable_lambda, QueuedConnection) **不会**跨线程 —— Qt 只对 QObject
    的方法跨线程。lambda 没有 thread affinity，会在 emit 线程直接调。
  - 解法：worker emit → Controller(QObject, 驻主线程) slot → Controller 调 Python 回调。
"""

from __future__ import annotations

import functools
import sys
import traceback
from typing import Any, Callable, Optional

from PySide6.QtCore import (
    QCoreApplication,
    QMetaObject,
    QObject,
    QRunnable,
    QThread,
    QThreadPool,
    Qt,
    Signal,
    Slot,
)


_GLOBAL_POOL: Optional[QThreadPool] = None


def get_pool() -> QThreadPool:
    """全局共享线程池（懒初始化）。"""
    global _GLOBAL_POOL
    if _GLOBAL_POOL is None:
        _GLOBAL_POOL = QThreadPool.globalInstance()
    return _GLOBAL_POOL


# ============== 跨线程桥接 Controller ==============

class _Controller(QObject):
    """驻主线程：把 Worker 发的 signal 转成 Python 回调调用。

    Python lambda 没有 thread affinity，Qt 不会自动跨线程。
    这个 QObject 显式声明自己属于主线程，所有 slot 自动在主线程跑。
    """

    def __init__(self, on_result=None, on_error=None, on_progress=None,
                 on_log=None, on_status=None, on_finished=None):
        super().__init__()
        # moveToThread 不需要：构造函数在主线程里调，affinity 就是主线程
        self._cb_result = on_result
        self._cb_error = on_error
        self._cb_progress = on_progress
        self._cb_log = on_log
        self._cb_status = on_status
        self._cb_finished = on_finished

    @Slot(object)
    def slot_result(self, v):
        if self._cb_result:
            try:
                self._cb_result(v)
            except Exception as e:
                print(f"[qasync] on_result callback raised: {e}", file=sys.stderr)
                traceback.print_exc()

    @Slot(str)
    def slot_error(self, e):
        if self._cb_error:
            try:
                self._cb_error(e)
            except Exception as exc:
                print(f"[qasync] on_error callback raised: {exc}", file=sys.stderr)

    @Slot(object)
    def slot_progress(self, v):
        if self._cb_progress:
            try:
                self._cb_progress(v)
            except Exception as e:
                print(f"[qasync] on_progress callback raised: {e}", file=sys.stderr)

    @Slot(str)
    def slot_log(self, line):
        if self._cb_log:
            try:
                self._cb_log(line)
            except Exception as e:
                print(f"[qasync] on_log callback raised: {e}", file=sys.stderr)

    @Slot(str)
    def slot_status(self, s):
        if self._cb_status:
            try:
                self._cb_status(s)
            except Exception as e:
                print(f"[qasync] on_status callback raised: {e}", file=sys.stderr)

    @Slot()
    def slot_finished(self):
        if self._cb_finished:
            try:
                self._cb_finished()
            except Exception as e:
                print(f"[qasync] on_finished callback raised: {e}", file=sys.stderr)


class Worker(QRunnable):
    """把任意 Python 函数扔到线程池跑；结果通过 Controller 回到主线程。"""

    def __init__(
        self,
        fn: Callable,
        *args,
        on_result: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[Any], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        # WorkerSignals 必须有 QObject parent 才能 emit；不能直接挂 QRunnable 因为
        # QRunnable 不是 QObject。所以单独建一个 signals 对象即可。
        class _S(QObject):
            started = Signal()
            finished = Signal()
            progress = Signal(object)
            result = Signal(object)
            error = Signal(str)
            log = Signal(str)
            status = Signal(str)
        self.signals = _S()

        # Controller 驻主线程；所有 signal → controller slot 都是 QueuedConnection
        # （sender 是 _S 自身，跨线程 emit；receiver 是 controller，在主线程）
        self._ctrl = _Controller(
            on_result=on_result, on_error=on_error,
            on_progress=on_progress, on_log=on_log,
            on_status=on_status, on_finished=on_finished,
        )
        self.signals.result.connect(self._ctrl.slot_result, Qt.QueuedConnection)
        self.signals.error.connect(self._ctrl.slot_error, Qt.QueuedConnection)
        self.signals.progress.connect(self._ctrl.slot_progress, Qt.QueuedConnection)
        self.signals.log.connect(self._ctrl.slot_log, Qt.QueuedConnection)
        self.signals.status.connect(self._ctrl.slot_status, Qt.QueuedConnection)
        self.signals.finished.connect(self._ctrl.slot_finished, Qt.QueuedConnection)

    @Slot()
    def run(self) -> None:
        try:
            self.signals.started.emit()
            result = self.fn(*self.args, **self.kwargs)
            self.signals.result.emit(result)
        except Exception as e:
            tb = traceback.format_exc()
            self.signals.error.emit(f"{type(e).__name__}: {e}\n\n{tb}")
        finally:
            self.signals.finished.emit()


# ============== 同步阻塞调主线程（替代 queue.Queue 那套） ==============

class _MainThreadBridge(QObject):
    """call_on_main_thread 用的桥接对象：moveToThread 到主线程，invokeMethod 跑 fn。"""

    def __init__(self, fn: Callable, args: tuple, kwargs: dict):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._result: Any = None
        self._error: Optional[BaseException] = None

    @Slot()
    def _run(self) -> None:
        try:
            self._result = self._fn(*self._args, **self._kwargs)
        except BaseException as e:
            self._error = e


def call_on_main_thread(fn: Callable, *args, timeout_ms: int = 30_000, **kwargs) -> Any:
    """同步阻塞：在主线程跑 fn(*args, **kwargs)，返回结果。

    如果当前已经在主线程，直接调（避免死锁 BlockingQueuedConnection）。
    """
    app = QCoreApplication.instance()
    if app is None:
        raise RuntimeError("call_on_main_thread 必须在 QApplication 创建后调用")

    if QThread.currentThread() is app.thread():
        return fn(*args, **kwargs)

    bridge = _MainThreadBridge(fn, args, kwargs)
    bridge.moveToThread(app.thread())

    try:
        ok = QMetaObject.invokeMethod(
            bridge, "_run",
            Qt.BlockingQueuedConnection,
        )
        if not ok:
            raise RuntimeError("主线程 invokeMethod 失败（事件循环被阻塞？）")
    finally:
        bridge.deleteLater()

    if bridge._error is not None:
        raise bridge._error
    return bridge._result


# ============== 便捷封装 ==============

def run(
    fn: Callable,
    *args,
    on_result: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[Any], None]] = None,
    on_log: Optional[Callable[[str], None]] = None,
    on_status: Optional[Callable[[str], None]] = None,
    on_finished: Optional[Callable[[], None]] = None,
    **kwargs,
) -> Worker:
    """一行启动后台任务。"""
    w = Worker(
        fn, *args,
        on_result=on_result, on_error=on_error,
        on_progress=on_progress, on_log=on_log,
        on_status=on_status, on_finished=on_finished,
        **kwargs,
    )
    get_pool().start(w)
    return w


# ============== 装饰器（让普通函数一行变成后台任务） ==============

def background(
    on_result: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[Any], None]] = None,
    on_log: Optional[Callable[[str], None]] = None,
    on_status: Optional[Callable[[str], None]] = None,
    on_finished: Optional[Callable[[], None]] = None,
):
    """装饰器：把同步函数变成可异步启动。

    用法：
        @background(on_result=lambda r: show(r), on_error=lambda e: alert(e))
        def fetch_url(url):
            return requests.get(url).text

        fetch_url.async_start("https://...")
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def async_start(*args, **kwargs):
            return run(
                fn, *args,
                on_result=on_result, on_error=on_error,
                on_progress=on_progress, on_log=on_log,
                on_status=on_status, on_finished=on_finished,
                **kwargs,
            )
        async_start.sync = fn
        return async_start
    return decorator
