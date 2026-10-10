#!/usr/bin/env python3
"""
启动期环境自检 —— GUI 启动前必须调用。

设计目标：
  1. 缺关键依赖时**早失败** + 给可执行修复指令，绝不让用户看到裸 ModuleNotFoundError
  2. 让引擎（TTS provider）按当前 Python 的真实可用性显示，不让用户选了一个跑不起来的引擎
  3. 单环境冲突问题（如 ctk 和 mlx_audio 分属两个 venv）能在启动期就被检测出来

调用方式（gui/main_window.py 启动期）：
    from gui.env_check import run_startup_check
    report = run_startup_check()
    if not report.ok:
        # 弹窗显示 report.render() 并 exit
        ...
    # 把 report.engines 喂给 GUI 让用户在选引擎时看到真实状态
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import platform
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


# ============== 报告数据结构 ==============

@dataclass
class CheckResult:
    """单个依赖项的检查结果。"""
    name: str         # 用户友好的名字，如 "mlx-audio (VoxCPM2 本地引擎)"
    available: bool   # 当前 Python 是否能 import
    required: bool    # 是否必需（True 缺失则 GUI 不能启动）
    fix_hint: str = ""  # 不可用时的修复指引


@dataclass
class EngineStatus:
    """单个 TTS 引擎在当前 Python 的可用性。"""
    key: str          # "voxcpm" | "minimax" | "doubao"
    label: str        # "VoxCPM2（本地）"
    available: bool
    reason: str = ""  # 不可用时说明原因


@dataclass
class EnvReport:
    python: str                          # 当前 python 解释器路径
    python_version: str                  # "3.11.5"
    platform: str                        # "macOS-27.0-arm64"
    checks: List[CheckResult] = field(default_factory=list)
    engines: List[EngineStatus] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """必需依赖全部可用才算 ok。"""
        return all(c.available for c in self.checks if c.required)

    @property
    def blockers(self) -> List[CheckResult]:
        return [c for c in self.checks if c.required and not c.available]

    @property
    def warnings(self) -> List[CheckResult]:
        return [c for c in self.checks if not c.required and not c.available]

    def render(self) -> str:
        """渲染成可贴进 UI 的多行字符串。"""
        lines: List[str] = []
        lines.append(f"Python: {self.python}")
        lines.append(f"        {self.python_version} on {self.platform}")
        lines.append("")
        lines.append("依赖检查：")
        for c in self.checks:
            mark = "✅" if c.available else ("❌" if c.required else "⚠️ ")
            tag = "必需" if c.required else "可选"
            lines.append(f"  {mark} [{tag}] {c.name}")
            if not c.available and c.fix_hint:
                for hint_line in c.fix_hint.splitlines():
                    lines.append(f"        {hint_line}")
        lines.append("")
        lines.append("TTS 引擎可用性：")
        for e in self.engines:
            mark = "✅" if e.available else "❌"
            lines.append(f"  {mark} {e.key}: {e.label}")
            if not e.available and e.reason:
                for r in e.reason.splitlines():
                    lines.append(f"        {r}")
        if self.ok:
            lines.append("")
            lines.append("✅ 启动检查通过，可以进入 GUI。")
        else:
            lines.append("")
            lines.append("❌ 必需依赖缺失，请按上方指引修复后重新启动。")
        return "\n".join(lines)


# ============== 检查项定义 ==============

# 每个 check 是一个 (name, required, probe_fn, fix_hint_fn) 元组
# probe_fn 返回 bool，True = 可用；fix_hint_fn 返回不可用时的修复指引
CheckSpec = Dict[str, object]


def _check_pyside6() -> bool:
    """PySide6（Qt6 官方 Python 绑定）—— 2026-Q1 起替代 customtkinter。

    旧 customtkinter 在 macOS 27 + Tk 9.0 上有解析 bug，已废弃。
    """
    return importlib.util.find_spec("PySide6") is not None


def _check_mlx_audio() -> bool:
    return importlib.util.find_spec("mlx_audio") is not None


def _check_voxcpm2_module() -> bool:
    """VoxCPM2 模型实现必须从 editable install 的 venv 走。

    0.2.x PyPI 版本只带 ``voxcpm/``（v1），没有 ``voxcpm2/``；在那上面跑 voxcpm
    引擎会报 "Model type voxcpm2 not supported"。这个 check 是硬卡口——
    缺 voxcpm2 模块时 voxcpm 引擎直接判不可用，并提示切到正确 venv。

    See also: ``bin/podcast-gui.sh`` 的 pre-flight（同一契约，两边都强制）。
    """
    return importlib.util.find_spec("mlx_audio.tts.models.voxcpm2") is not None


def _check_soundfile() -> bool:
    return importlib.util.find_spec("soundfile") is not None


def _check_requests() -> bool:
    return importlib.util.find_spec("requests") is not None


def _check_ffmpeg() -> bool:
    import shutil
    return shutil.which("ffmpeg") is not None


_CHECKS: List[CheckSpec] = [
    {
        "name": "PySide6（Qt6 GUI 框架，2026-Q1 起替代 customtkinter）",
        "required": True,
        "probe": _check_pyside6,
        "fix": (
            "PySide6 是 GUI 必需依赖（LGPL，可商用）。\n"
            "  pip install PySide6\n"
            "（如果你在用项目 venv：~/.podcast-automation/.venv/bin/pip install PySide6）\n"
            "旧 customtkinter / Tk 9.0 在 macOS 27 已知会崩，不要回退。"
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
        "required": False,  # 可选；GUI 启动不依赖它，但选了 voxcpm 引擎就会失败
        "probe": _check_mlx_audio,
        "fix": (
            "mlx-audio 是 VoxCPM2 本地克隆引擎的依赖。\n"
            "  仅 Apple Silicon Mac + Python 3.10+ 可装。\n"
            "  pip install mlx-audio\n"
            "（如果用项目 venv 启动：~/.podcast-automation/.venv/bin/pip install mlx-audio）"
        ),
    },
    {
        "name": "mlx-audio VoxCPM2 模块（v2 引擎代码，必须在 editable install 的 venv）",
        "required": False,  # GUI 启动不卡，但 voxcpm 引擎要求它
        "probe": _check_voxcpm2_module,
        "fix": (
            "当前 venv 是 PyPI 装的 mlx-audio（只有 v1，没有 voxcpm2 实现）。\n"
            "\n"
            "  ➜ 必须用 editable install 的 venv：\n"
            "      ~/.podcast-automation/mlx-audio/.venv/\n"
            "\n"
            "  切换方法（任选其一）：\n"
            "    1. bin/podcast-gui.sh                 # 默认就指向正确 venv\n"
            "    2. PODCAST_VENV=~/.podcast-automation/mlx-audio/.venv bin/podcast-gui.sh\n"
            "\n"
            "  如果正确 venv 还没建：\n"
            "    cd ~/.podcast-automation/mlx-audio\n"
            "    uv venv .venv --python 3.11\n"
            "    .venv/bin/pip install -e .\n"
            "\n"
            "  Memory: see voxcpm2-mlx-zero-shot + two-venvs-voxcpm2"
        ),
    },
    {
        "name": "ffmpeg（音频转码）",
        "required": False,  # 可选；只有要导出 mp3 才需要
        "probe": _check_ffmpeg,
        "fix": (
            "ffmpeg 未在 PATH 中。导出 mp3 / 变速 / loudnorm 都需要它。\n"
            "  macOS:  brew install ffmpeg\n"
            "  Linux:  sudo apt install ffmpeg"
        ),
    },
]


# ============== 引擎可用性映射 ==============

def _engine_statuses(checks: List[CheckResult]) -> List[EngineStatus]:
    by_name = {c.name.split("（")[0]: c for c in checks}
    mlx_ok = by_name.get("mlx-audio", CheckResult(name="", available=False, required=False)).available
    voxcpm2_ok = by_name.get("mlx-audio VoxCPM2 模块", CheckResult(name="", available=False, required=False)).available
    sf_ok = by_name.get("soundfile", CheckResult(name="", available=False, required=False)).available
    req_ok = by_name.get("requests", CheckResult(name="", available=False, required=False)).available
    ff_ok = by_name.get("ffmpeg", CheckResult(name="", available=False, required=False)).available

    # voxcpm 引擎可用性：必须 mlx-audio + voxcpm2 模块 + soundfile
    # 缺 voxcpm2 = 当前在错的 venv（PyPI 0.2.x 只有 v1）
    voxcpm_reasons = []
    if not mlx_ok:
        voxcpm_reasons.append("mlx-audio 未装")
    if not voxcpm2_ok:
        voxcpm_reasons.append("当前 venv 缺 voxcpm2 实现（用了 PyPI 0.2.x？切到 editable venv）")
    if not sf_ok:
        voxcpm_reasons.append("soundfile 未装")

    return [
        EngineStatus(
            key="minimax",
            label="MiniMax TTS（云端，需 API Key）",
            available=req_ok,
            reason="" if req_ok else "requests 未装；云端 TTS 走不通",
        ),
        EngineStatus(
            key="voxcpm",
            label="VoxCPM2（本地克隆，需 editable mlx-audio venv）",
            available=mlx_ok and voxcpm2_ok and sf_ok,
            reason="；".join(voxcpm_reasons),
        ),
        EngineStatus(
            key="doubao",
            label="豆包 TTS（火山引擎，需 appid/token）",
            available=req_ok,
            reason="" if req_ok else "requests 未装",
        ),
        EngineStatus(
            key="ffmpeg-required",
            label="导出 MP3（仅在导出 mp3 时需要 ffmpeg）",
            available=ff_ok,
            reason="" if ff_ok else "ffmpeg 未在 PATH；导 mp3 会失败（导出 wav 不需要）",
        ),
    ]


# ============== 主入口 ==============

def run_startup_check() -> EnvReport:
    """运行所有自检并返回结构化报告。

    GUI 启动时必须调用。如果 ok=False，应该弹窗显示 render() 然后 exit。
    """
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
        except Exception as e:  # 探测本身炸了也算不可用
            available = False
            fix_hint = (
                f"{str(spec.get('fix', ''))}\n"
                f"探测时抛异常：{type(e).__name__}: {e}"
            )
            report.checks.append(CheckResult(
                name=name, available=False, required=required, fix_hint=fix_hint,
            ))
            continue

        fix_hint = "" if available else str(spec.get("fix", ""))
        report.checks.append(CheckResult(
            name=name, available=available, required=required, fix_hint=fix_hint,
        ))

    report.engines = _engine_statuses(report.checks)
    return report


def quick_assert_ok_or_die(parent=None) -> EnvReport:
    """便捷封装：检查 + 如果不 ok 直接 sys.exit。

    返回的报告保证 ok=True。
    """
    report = run_startup_check()
    if not report.ok:
        msg = report.render()
        # 主窗口还没建出来，parent 可能为 None；用 stderr + 弹窗两层兜底
        print(msg, file=sys.stderr)
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Podcast Generator · 启动检查失败", msg)
            root.destroy()
        except Exception:
            pass  # 连 tkinter 都没有就让上层捕获 exit code
        sys.exit(2)
    return report


if __name__ == "__main__":
    # CLI 模式：打印报告并按 ok 退出
    r = run_startup_check()
    print(r.render())
    sys.exit(0 if r.ok else 2)
