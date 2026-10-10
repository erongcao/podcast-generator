#!/usr/bin/env python3
"""
输入面板 —— PySide6 版

包含：
  - 凭证（credentials）
  - 输入源（URL / 文本 / 文件，QTabWidget）
  - 引擎相关音色选择（豆包 / MiniMax / VoxCPM）
  - 片头片尾
  - 「🎵 生成台词」按钮（emit script_requested signal 给主窗口）

对外 API：
  - engine -> str                    引擎 key（通过 credentials.get_engine()）
  - minimax_key -> str               API Key
  - script_engine -> str             台词生成引擎
  - get_voice_map() / get_speed_map() / get_intro_outro()
  - 三个信号：script_requested, script_loaded(segments, fallback_text)

与旧 customtkinter 版接口尽量保持一致。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gui.qt.panels.credentials import CredentialPanel
from gui.qt.panels.intro_outro import IntroOutroPanel
from gui.qt.panels.speakers import (
    SpeakerRow,
    MiniMaxVoiceList,
    VoxCPMVoiceList,
)
from gui.qt.theme_qt import GHIBLI


# 复用旧 utils_script 的脚本格式检测
from gui.utils_script import is_script_format


class InputPanel(QFrame):
    # 用户点「🎵 生成台词」时发；payload=(engine, url, text, only_nlp_text)
    script_requested = Signal(str, object, object, bool)
    # 文件被识别为脚本时发；payload=(segments, fallback_text)
    script_loaded = Signal(list, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            f"background-color: {GHIBLI['cream']}; "
            f"border: 2px solid {GHIBLI['grass']}; "
            f"border-radius: 12px;"
        )

        self.selected_file_path: Optional[str] = None
        self.selected_file_text: Optional[str] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 标题 ----
        title = QLabel("📄  输入内容")
        title.setStyleSheet(
            f"color: {GHIBLI['forest_dark']}; font-size: 16px; font-weight: bold; "
            f"background: transparent; padding: 12px 16px 4px 16px;"
        )
        root.addWidget(title)

        # ---- 凭证 ----
        self.cred = CredentialPanel(self)
        root.addWidget(self.cred)
        self.cred.engine_changed.connect(self._on_engine_change)

        # ---- 音色选择（三选一，按引擎切换） ----
        # 三个都加到 stacked widget 里，根据 engine 切换
        self._voice_stack = QStackedWidget()
        self._speaker_row = SpeakerRow()
        self._minimax_list = MiniMaxVoiceList()
        self._voxcpm_list = VoxCPMVoiceList()
        self._voice_stack.addWidget(self._speaker_row)   # 0
        self._voice_stack.addWidget(self._minimax_list)   # 1
        self._voice_stack.addWidget(self._voxcpm_list)    # 2
        root.addWidget(self._voice_stack)

        # ---- 输入源 Tabs ----
        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.North)
        self.tabs.setMinimumHeight(280)

        # --- URL ---
        url_tab = QWidget()
        url_layout = QVBoxLayout(url_tab)
        url_layout.setContentsMargins(6, 6, 6, 6)
        self.url_box = QPlainTextEdit()
        self.url_box.setPlaceholderText(
            "粘贴文档或公众号文章 URL...\n例如: https://mp.weixin.qq.com/s/xxxxx"
        )
        self.url_box.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {GHIBLI['cream']}; "
            f"border: 1.5px solid {GHIBLI['grass']}; border-radius: 8px; "
            f"padding: 8px; font-size: 13px; }}"
        )
        url_layout.addWidget(self.url_box)
        self.tabs.addTab(url_tab, "🌐 URL 链接")

        # --- 文本 ---
        txt_tab = QWidget()
        txt_layout = QVBoxLayout(txt_tab)
        txt_layout.setContentsMargins(6, 6, 6, 6)
        self.txt_box = QPlainTextEdit()
        self.txt_box.setPlaceholderText("直接粘贴文章全文…")
        self.txt_box.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {GHIBLI['cream']}; "
            f"border: 1.5px solid {GHIBLI['grass']}; border-radius: 8px; "
            f"padding: 8px; font-size: 13px; }}"
        )
        txt_layout.addWidget(self.txt_box)
        self.tabs.addTab(txt_tab, "📝 输入文本")

        # --- 文件 ---
        file_tab = QWidget()
        file_layout = QVBoxLayout(file_tab)
        file_layout.setContentsMargins(6, 6, 6, 6)
        file_layout.setSpacing(4)

        self.file_label = QLabel("未选择文件")
        self.file_label.setStyleSheet(f"color: {GHIBLI['text_light']};")
        file_layout.addWidget(self.file_label)

        self.file_preview = QPlainTextEdit()
        self.file_preview.setReadOnly(True)
        self.file_preview.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {GHIBLI['cream_dark']}; "
            f"border: 1.5px solid {GHIBLI['grass']}; border-radius: 8px; "
            f"padding: 8px; font-size: 11px; }}"
        )
        file_layout.addWidget(self.file_preview, 1)

        self.file_pick_btn = QPushButton("📂  选择文件（PDF/Word/Markdown/TXT）")
        self.file_pick_btn.setStyleSheet(
            f"background-color: {GHIBLI['grass']}; color: {GHIBLI['white']}; "
            f"border: none; border-radius: 8px; padding: 8px; font-weight: bold;"
        )
        self.file_pick_btn.clicked.connect(self._on_select_file)
        file_layout.addWidget(self.file_pick_btn)

        self.file_script_btn = QPushButton("🎙️  检测到脚本格式，载入到台词编辑")
        self.file_script_btn.setStyleSheet(
            f"background-color: {GHIBLI['totoro']}; color: {GHIBLI['white']}; "
            f"border: none; border-radius: 8px; padding: 6px; font-weight: bold;"
        )
        self.file_script_btn.clicked.connect(self._on_load_file_as_script)
        self.file_script_btn.setVisible(False)
        file_layout.addWidget(self.file_script_btn)

        self.tabs.addTab(file_tab, "📁 导入文件")

        root.addWidget(self.tabs)

        # ---- 片头片尾 ----
        self.intro_outro = IntroOutroPanel(self)
        root.addWidget(self.intro_outro)

        # ---- 底部：操作行 ----
        bottom = QHBoxLayout()
        bottom.setContentsMargins(12, 4, 12, 12)
        bottom.setSpacing(8)

        self.hint = QLabel("💡 输入内容后点「🎵 生成台词」→ 审核 → 再点生成音频")
        self.hint.setStyleSheet(f"color: {GHIBLI['text_light']}; font-size: 11px;")
        bottom.addWidget(self.hint, 1)

        self.gen_btn = QPushButton("🎵 生成台词")
        self.gen_btn.setStyleSheet(
            f"background-color: {GHIBLI['totoro']}; color: {GHIBLI['white']}; "
            f"border: none; border-radius: 8px; padding: 8px 18px; "
            f"font-weight: bold; font-size: 13px;"
        )
        self.gen_btn.clicked.connect(self._on_generate_script)
        bottom.addWidget(self.gen_btn)

        root.addLayout(bottom)

        # 初始引擎
        self._on_engine_change()

    # ============== 槽 ==============

    def _on_engine_change(self, *_):
        engine = self.cred.get_engine()
        if engine == "doubao":
            self._voice_stack.setCurrentIndex(0)
        elif engine == "minimax":
            self._voice_stack.setCurrentIndex(1)
        elif engine == "voxcpm":
            self._voice_stack.setCurrentIndex(2)

    def _on_select_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择文档",
            "",
            "文档文件 (*.md *.markdown *.pdf *.docx *.txt);;全部文件 (*)",
        )
        if not path:
            return
        self.selected_file_path = path
        self.file_label.setText(os.path.basename(path))
        text = self._extract_text(path)
        if not text:
            return
        self.selected_file_text = text
        # 预览
        self.file_preview.setPlainText(text[:500] + "..." if len(text) > 500 else text)
        # 检测脚本格式
        if is_script_format(text):
            self.file_script_btn.setVisible(True)
        else:
            self.file_script_btn.setVisible(False)

    def _on_load_file_as_script(self):
        if not self.selected_file_text:
            return
        from minimax_script_gen import parse_script as parse_pasted_script
        try:
            segments = parse_pasted_script(self.selected_file_text)
        except Exception:
            segments = []
        self.script_loaded.emit(segments, self.selected_file_text)

    def _on_generate_script(self):
        engine = self.cred.get_engine()
        script_engine = self.cred.get_script_engine()
        # 用户选了 MiniMax LLM 当台词生成器，但 TTS 引擎是豆包
        # → 走 only_nlp_text=True 让 output panel 知道这是 LLM 生成的台词
        only_nlp_text = (script_engine == "minimax_llm")

        # 按当前 tab 决定 url/text
        if self.tabs.currentIndex() == 0:  # URL
            url = self.url_box.toPlainText().strip()
            if url and not url.startswith(("http://", "https://")):
                url = ""  # ignore placeholder
            self.script_requested.emit(engine, url, "", only_nlp_text)
        elif self.tabs.currentIndex() == 1:  # 文本
            text = self.txt_box.toPlainText().strip()
            self.script_requested.emit(engine, None, text, only_nlp_text)
        else:  # 文件 tab
            if not self.selected_file_text:
                return
            self.script_requested.emit(engine, None, self.selected_file_text, only_nlp_text)

    # ============== 文本提取 ==============

    def _extract_text(self, path: str) -> str:
        p = Path(path)
        try:
            if p.suffix.lower() in (".md", ".markdown"):
                return p.read_text(encoding="utf-8")
            if p.suffix.lower() == ".pdf":
                try:
                    import fitz
                except ImportError:
                    return "[需要安装 PyMuPDF: pip install pymupdf]"
                doc = fitz.open(path)
                return "\n".join(page.get_text() for page in doc)
            if p.suffix.lower() == ".docx":
                try:
                    import docx
                except ImportError:
                    return "[需要安装 python-docx: pip install python-docx]"
                doc = docx.Document(path)
                return "\n".join(pp.text for pp in doc.paragraphs)
        except Exception as e:
            return f"[读取失败: {e}]"
        return "[不支持的文件格式]"

    # ============== 对外 getter ==============

    def get_engine(self) -> str:
        return self.cred.get_engine()

    def get_minimax_key(self) -> str:
        return self.cred.get_minimax_key()

    def get_script_engine(self) -> str:
        return self.cred.get_script_engine()

    def get_credentials(self) -> dict:
        return self.cred.get_credentials()

    def get_voice_map(self) -> dict:
        engine = self.get_engine()
        if engine == "doubao":
            # 豆包返回 [speaker1, speaker2] 格式
            ids = self._speaker_row.get_speakers()
            return {"主播甲": ids[0], "主播乙": ids[1]} if len(ids) >= 2 else {}
        if engine == "minimax":
            return self._minimax_list.get_voice_map()
        if engine == "voxcpm":
            return self._voxcpm_list.get_voice_map()
        return {}

    def get_speed_map(self) -> dict:
        engine = self.get_engine()
        if engine == "minimax":
            return self._minimax_list.get_speed_map()
        if engine == "voxcpm":
            return self._voxcpm_list.get_speed_map()
        # 豆包走引擎默认速度
        return {"主播甲": 1.0, "主播乙": 1.0}

    def get_model(self) -> str:
        # 仅 MiniMax 引擎有意义
        return self._minimax_list.get_model()

    def get_intro_outro(self) -> tuple:
        return self.intro_outro.get_intro_outro()
