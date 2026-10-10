#!/usr/bin/env python3
"""
主窗口 - PySide6 版

布局：
    +---------------------------------------------+
    |  EnvBanner（点击展开看修复指引）            |
    +---------------------------------------------+
    |  Header：豆包播客生成器 (Ghibli 配色)       |
    +---------------------------------------------+
    |  InputPanel (左)  |  [生成 | 音色库] (右)   |
    |                   |                         |
    +---------------------------------------------+
    |  Footer                                    |
    +---------------------------------------------+

启动流程：
    1. run_startup_check() —— 必需依赖（PySide6/requests/soundfile）缺失则
       弹窗并 exit(2)
    2. 创建 GhibliApp，传 env_report（让 banner 显示真实引擎可用性）
    3. app.exec() 进入事件循环
"""

from __future__ import annotations

import importlib.util
import os
import sys

# ---- Hard env guarantee: refuse to start if venv lacks voxcpm2 ----
# Mirror of bin/podcast-gui.sh's pre-flight. Both gates enforce the same
# contract: GUI must run from a venv that has the editable mlx-audio
# install (with voxcpm2/ source). Skipping requires explicit env var.
# Bypass: PODCAST_SKIP_VENV_CHECK=1 python -m gui.qt.main_window
if os.environ.get("PODCAST_SKIP_VENV_CHECK") != "1":
    if importlib.util.find_spec("mlx_audio.tts.models.voxcpm2") is None:
        sys.stderr.write(
            "\n"
            "❌ 当前 Python 环境缺 voxcpm2 实现：\n"
            f"   {sys.executable}\n"
            "\n"
            "   import mlx_audio.tts.models.voxcpm2 失败\n"
            "   （PyPI 装的 mlx-audio 0.2.x 只带 v1，没有 voxcpm2）\n"
            "\n"
            "   启动方法（任选其一）：\n"
            "     1. bin/podcast-gui.sh                       # 默认用正确 venv\n"
            "     2. PODCAST_VENV=~/.podcast-automation/mlx-audio/.venv bin/podcast-gui.sh\n"
            "\n"
            "   （诊断绕过：PODCAST_SKIP_VENV_CHECK=1 python -m gui.qt.main_window）\n"
            "\n"
        )
        sys.exit(2)

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gui.env_check import EnvReport
from gui.qt.env_check_qt import run_startup_check
from gui.qt.panels.input import InputPanel
from gui.qt.panels.output import OutputPanel
from gui.qt.panels.voice_library_panel_qt import VoiceLibraryPanel
from gui.qt.theme_qt import GHIBLI_QSS, GHIBLI


# ============== 标题栏 ==============

class HeaderFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(72)
        self.setStyleSheet(f"background-color: {GHIBLI['forest']};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(0)

        title = QLabel("  豆包播客生成器")
        title.setFont(QFont("Helvetica Neue", 24, QFont.Bold))
        title.setStyleSheet(f"color: {GHIBLI['white']}; background: transparent;")
        layout.addWidget(title)

        subtitle = QLabel("上传文档 · AI 生成双人播客音频")
        subtitle.setFont(QFont("Helvetica Neue", 12))
        subtitle.setStyleSheet(f"color: {GHIBLI['cream']}; background: transparent;")
        layout.addWidget(subtitle)

        # 顶部 6px 装饰条
        self._stripe = QFrame(self)
        self._stripe.setFixedHeight(6)
        self._stripe.setStyleSheet(f"background-color: {GHIBLI['forest_dark']};")
        self._stripe.move(0, 0)
        self._stripe.resize(self.width(), 6)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._stripe.resize(self.width(), 6)


# ============== 底部栏 ==============

class FooterFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setStyleSheet(f"background-color: {GHIBLI['cream_dark']};")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 0, 15, 0)
        layout.addStretch(1)

        label = QLabel("吉卜力风格 · 豆包播客生成器 · PySide6")
        label.setFont(QFont("Helvetica Neue", 10))
        label.setStyleSheet(f"color: {GHIBLI['text_light']}; background: transparent;")
        layout.addWidget(label)


# ============== 环境提示条 ==============

class EnvBanner(QFrame):
    """显示当前 Python 的 TTS 引擎可用性；点击展开修复提示。"""

    def __init__(self, parent=None, env_report: EnvReport | None = None):
        super().__init__(parent)
        self._report = env_report
        self.setFixedHeight(28)
        self.setStyleSheet(
            f"background-color: {GHIBLI['cream_dark']}; "
            f"border-bottom: 1px solid {GHIBLI['shadow']};"
        )
        self.setCursor(Qt.PointingHandCursor)

        self._label = QLabel(self._render_summary())
        self._label.setFont(QFont("Helvetica Neue", 11))
        self._label.setStyleSheet(f"color: {GHIBLI['text']}; background: transparent;")
        self._label.move(15, 4)
        self._label.resize(800, 20)

        self._detail: QLabel | None = None
        if env_report and (env_report.warnings or not env_report.ok):
            self._detail = QLabel(env_report.render(), self.parentWidget() if self.parentWidget() else self)
            self._detail.setFont(QFont("Menlo", 10))
            self._detail.setWordWrap(True)
            self._detail.setStyleSheet(
                f"color: {GHIBLI['text']}; background: {GHIBLI['cream']}; "
                f"padding: 8px; border: 1px solid {GHIBLI['shadow']};"
            )
            self._detail.setVisible(False)
            self._detail.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._visible = False

    def _render_summary(self) -> str:
        if not self._report:
            return "环境自检通过"
        parts = []
        for eng in self._report.engines:
            mark = "✅" if eng.available else "🚫"
            parts.append(f"{mark} {eng.key}")
        s = " · ".join(parts)
        if not self._report.warnings:
            return "引擎就绪 · " + s
        return s

    def mousePressEvent(self, event):
        if self._detail is None:
            return
        self._visible = not self._visible
        self._detail.setVisible(self._visible)
        if self._visible and self.parentWidget():
            self._detail.setFixedWidth(self.parentWidget().width())
            self._detail.move(0, self.height())
            self._detail.raise_()
        event.accept()


# ============== 主窗口 ==============

class GhibliApp(QMainWindow):
    def __init__(self, env_report: EnvReport):
        super().__init__()
        self.env_report = env_report
        self.setWindowTitle("豆包播客生成器 · Ghibli Style (Qt)")
        self.resize(1100, 800)
        self.setMinimumSize(800, 560)
        self.setStyleSheet(f"background-color: {GHIBLI['cream']};")

        # 中央 widget：垂直布局 [env_banner, header, content, footer]
        central = QWidget()
        central.setStyleSheet(f"background-color: {GHIBLI['cream']};")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 1. env banner
        self.env_banner = EnvBanner(central, env_report)
        root.addWidget(self.env_banner)

        # 2. header
        self.header = HeaderFrame(central)
        root.addWidget(self.header)

        # 3. content (splitter)
        # 横向：左 input | 右 tabs
        # handle 加宽到 6px，hover 时变深 + 显示 `` 抓握点（用户要求"左右可调"）
        self.splitter = QSplitter(Qt.Horizontal, central)
        self.splitter.setStyleSheet(
            f"QSplitter::handle {{ background-color: {GHIBLI['dust']}; "
            f"  border-left: 1px solid {GHIBLI['cream_dark']}; "
            f"  border-right: 1px solid {GHIBLI['cream_dark']}; }}"
            f"QSplitter::handle:hover {{ background-color: {GHIBLI['sky_dark']}; }}"
            f"QSplitter::handle:pressed {{ background-color: {GHIBLI['totoro']}; }}"
        )
        self.splitter.setHandleWidth(6)
        self.splitter.setChildrenCollapsible(False)

        # 真正接入 input/output panel
        self.input_panel = InputPanel()
        self.output_panel = OutputPanel(input_panel=self.input_panel)

        # 音色库面板（与生成面板并排作为右侧的 tab）
        self.voice_library = VoiceLibraryPanel(
            get_engine=self.input_panel.get_engine,
            get_api_key=self.input_panel.get_minimax_key,
        )
        # 引擎切换时同步按钮显隐
        self.input_panel.cred.engine_changed.connect(self.voice_library.set_engine)

        # 信号连接
        self.input_panel.script_loaded.connect(self._on_script_loaded)
        self.input_panel.script_requested.connect(self._on_script_requested)

        # 右侧：QTabWidget（生成 | 音色库）
        right_tabs = QTabWidget()
        right_tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: 0; }} "
            f"QTabBar::tab {{ background: {GHIBLI['cream_dark']}; "
            f"  color: {GHIBLI['text']}; padding: 8px 16px; "
            f"  border-top-left-radius: 6px; border-top-right-radius: 6px; }} "
            f"QTabBar::tab:selected {{ background: {GHIBLI['white']}; "
            f"  color: {GHIBLI['forest_dark']}; font-weight: bold; }}"
        )
        right_tabs.addTab(self.output_panel, "🎙️  生成音频")
        right_tabs.addTab(self.voice_library, "📚  音色库")

        self.splitter.addWidget(self.input_panel)
        self.splitter.addWidget(right_tabs)
        self.splitter.setSizes([520, 580])

        wrap = QWidget()
        wrap.setStyleSheet(f"background-color: {GHIBLI['cream']};")
        wrap_layout = QVBoxLayout(wrap)
        wrap_layout.setContentsMargins(16, 12, 16, 12)
        wrap_layout.addWidget(self.splitter)
        root.addWidget(wrap, 1)

        # 4. footer
        self.footer = FooterFrame(central)
        root.addWidget(self.footer)

    def _on_script_loaded(self, segments: list, fallback_text):
        """input panel 把文件识别为脚本时，转给 output panel。"""
        self.output_panel.load_segments_into_editor(segments, source="file", fallback_text=fallback_text)

    def _on_script_requested(self, engine: str, url, text: str, only_nlp_text: bool):
        """用户点「🎵 生成台词」。"""
        self.output_panel.generate(url=url, text=text, only_nlp_text=only_nlp_text)


# ============== 启动 ==============

def main() -> int:
    report = run_startup_check()
    if not report.ok:
        msg = report.render()
        print(msg, file=sys.stderr)
        app = QApplication.instance() or QApplication(sys.argv)
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "Podcast Generator · 启动检查失败", msg)
        return 2

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(GHIBLI_QSS)

    win = GhibliApp(report)
    win.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
