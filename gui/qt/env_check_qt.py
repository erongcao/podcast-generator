#!/usr/bin/env python3
"""
启动期环境自检 —— PySide6 版本。

与 gui/env_check.py 同样的 EnvReport 数据结构，但 probe 函数切到 PySide6：
  - 必需 GUI 框架从 customtkinter → PySide6
  - 其余检查（requests / soundfile / mlx-audio / ffmpeg）完全一致

调用方式（gui/qt/main_window.py 启动期）：
    from gui.qt.env_check_qt import run_startup_check, quick_assert_ok_or_die
    report = quick_assert_ok_or_die()
"""

from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List

# 复用旧 env_check 的 dataclass 定义（保持 EnvReport API 稳定，跨 Tk/Qt 一致）
from gui.env_check import CheckResult, EngineStatus, EnvReport


# ============== probe 函数（PySide6 版） ==============

def _check_pyside6() -> bool:
    return importlib.util.find_spec("PySide6") is not None


def _check_mlx_audio() -> bool:
    return importlib.util.find_spec("mlx_audio") is not None


def _check_soundfile() -> bool:
    return importlib.util.find_spec("soundfile") is not None


def _check_requests() -> bool:
    return importlib.util.find_spec("requests") is not None


def _check_sounddevice() -> bool:
    """录制本地音色需要。"""
    return importlib.util.find_spec("sounddevice") is not None


def _check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


_CHECKS: List[Dict[str, object]] = [
    {
        "name": "PySide6（Qt 6 GUI 框架）",
        "required": True,
        "probe": _check_pyside6,
        "fix": (
            "PySide6 是 GUI 必需依赖（替代 customtkinter，绕开 Tk 9.0 canvas 段错误）。\n"
            "  pip install \"PySide6>=6.6,<6.9\"\n"
            "（项目 venv：~/.podcast-automation/.venv/bin/pip install PySide6）"
        ),
    },
    {
        "name": "requests（HTTP 客户端）",
        "required": True,
        "probe": _check_requests,
        "fix": "pip install requests",
    },
    {
        "name": "soundfile（音频读写）",
        "required": True,
        "probe": _check_soundfile,
        "fix": "pip install soundfile",
    },
    {
        "name": "mlx-audio（VoxCPM2 本地引擎）",
        "required": False,
        "probe": _check_mlx_audio,
        "fix": (
            "mlx-audio 是 VoxCPM2 本地克隆引擎的依赖。\n"
            "  仅 Apple Silicon Mac + Python 3.10+ 可装。\n"
            "  pip install mlx-audio"
        ),
    },
    {
        "name": "sounddevice（麦克风录制）",
        "required": False,
        "probe": _check_sounddevice,
        "fix": (
            "sounddevice 用来录本地音色。\n"
            "  macOS: brew install portaudio && pip install sounddevice"
        ),
    },
    {
        "name": "ffmpeg（音频转码）",
        "required": False,
        "probe": _check_ffmpeg,
        "fix": "macOS: brew install ffmpeg\nLinux: sudo apt install ffmpeg",
    },
]


# ============== 引擎可用性映射 ==============

def _engine_statuses(checks: List[CheckResult]) -> List[EngineStatus]:
    def by(prefix: str) -> CheckResult:
        for c in checks:
            if c.name.startswith(prefix):
                return c
        return CheckResult(name="", available=False, required=False)

    mlx_ok = by("mlx-audio").available
    sf_ok = by("soundfile").available
    req_ok = by("requests").available
    ff_ok = by("ffmpeg").available

    return [
        EngineStatus(
            key="minimax",
            label="MiniMax TTS（云端，需 API Key）",
            available=req_ok,
            reason="" if req_ok else "requests 未装",
        ),
        EngineStatus(
            key="voxcpm",
            label="VoxCPM2（本地克隆，需 mlx-audio）",
            available=mlx_ok and sf_ok,
            reason=(
                "" if (mlx_ok and sf_ok)
                else "mlx-audio 未装（仅 Apple Silicon）" if not mlx_ok
                else "soundfile 未装"
            ),
        ),
        EngineStatus(
            key="doubao",
            label="豆包 TTS（火山引擎）",
            available=req_ok,
            reason="" if req_ok else "requests 未装",
        ),
        EngineStatus(
            key="ffmpeg-required",
            label="导出 MP3",
            available=ff_ok,
            reason="" if ff_ok else "ffmpeg 未在 PATH（仅导出 wav 不需要）",
        ),
    ]


# ============== 主入口 ==============

def run_startup_check() -> EnvReport:
    report = EnvReport(
        python=sys.executable,
        python_version=sys.version.split()[0],
        platform=platform.platform(),
    )

    for spec in _CHECKS:
        name = str(spec["name"])
        required = bool(spec["required"])
        probe = spec["probe"]
        assert isinstance(probe, Callable)
        try:
            available = bool(probe())
            fix_hint = "" if available else str(spec.get("fix", ""))
        except Exception as e:
            available = False
            fix_hint = f"{str(spec.get('fix', ''))}\n探测时抛异常：{type(e).__name__}: {e}"

        report.checks.append(CheckResult(
            name=name, available=available, required=required, fix_hint=fix_hint,
        ))

    report.engines = _engine_statuses(report.checks)
    return report


def quick_assert_ok_or_die(parent=None) -> EnvReport:
    """检查 + 不 ok 直接 sys.exit(2)。

    Args:
        parent: 可选 QWidget，失败时把弹窗挂到 parent 上居中显示。
                不传则用 stderr 兜底（CLI 模式）。
    """
    report = run_startup_check()
    if report.ok:
        return report

    msg = report.render()
    print(msg, file=sys.stderr)

    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance()
        if app is None:
            # 还没有 QApplication —— 临时起一个只为弹窗
            app = QApplication(sys.argv)
        QMessageBox.critical(parent, "Podcast Generator · 启动检查失败", msg)
    except Exception:
        # 连 PySide6 都炸了（极端情况），让上层捕获 exit code
        pass
    sys.exit(2)


if __name__ == "__main__":
    r = run_startup_check()
    print(r.render())
    sys.exit(0 if r.ok else 2)
