#!/usr/bin/env python3
"""
凭证输入面板 —— PySide6 版

布局：
    +--- 引擎选择 ---+        +- 台词生成 -+
    | ○ 🎙️ 豆包       |        | ○ 豆包     |
    | ○ 🎵 MiniMax    |        | ○ M3 LLM   |
    | ○ 🖥️ 本地 VoxCPM|        +------------+
    +----------------+

    [ MiniMax API Key            ]  (始终显示)

    [ 豆包 APP ID ] [ Access Token ] [📁]   (仅 doubao 引擎下启用)

对外接口（保持与旧 customtkinter 版一致，main_window 不需要改）：
    - get_engine()           -> "doubao" | "minimax" | "voxcpm"
    - get_minimax_key()      -> str
    - get_script_engine()    -> "doubao" | "minimax_llm"
    - get_credentials()      -> {"appid": str, "token": str}

依赖 config.json 自动保存（防抖 500ms，避免每个 keystroke 都写盘）
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config import CONFIG
from gui.qt.theme_qt import GHIBLI


# 配置保存锁（防止 worker 线程同时写盘时撞车）
_config_lock = threading.Lock()


def _atomic_write_json(path: Path, data: dict):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, dir=path.parent) as f:
        json.dump(data, f, indent=2)
        temp_path = f.name
    os.replace(temp_path, path)


class CredentialPanel(QFrame):
    """PySide6 版凭证面板。"""

    engine_changed = Signal(str)         # 引擎切换时发，主窗口可以连这个信号
    credentials_saved = Signal()         # config 写盘后发（防抖后）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.setStyleSheet(
            f"background-color: {GHIBLI['white']}; "
            f"border: 2px solid {GHIBLI['forest']}; "
            f"border-radius: 10px;"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        # ---- 顶部：引擎 + 台词引擎（两列） ----
        top = QHBoxLayout()
        top.setSpacing(20)
        root.addLayout(top)

        # 左：TTS 引擎
        engine_box = QWidget()
        eb_layout = QVBoxLayout(engine_box)
        eb_layout.setContentsMargins(0, 0, 0, 0)
        eb_layout.setSpacing(2)

        eb_title = QLabel("引擎：")
        eb_title.setProperty("role", "muted")
        eb_layout.addWidget(eb_title)

        self._engine_group = QButtonGroup(self)
        for i, (key, label) in enumerate([
            ("doubao", "🎙️ 豆包"),
            ("minimax", "🎵 MiniMax"),
            ("voxcpm", "🖥️ 本地 VoxCPM"),
        ]):
            rb = QRadioButton(label)
            rb.setProperty("engine", key)
            self._engine_group.addButton(rb, i)
            eb_layout.addWidget(rb)
            if key == "doubao":
                rb.setChecked(True)

        top.addWidget(engine_box)

        # 右：台词生成引擎
        script_box = QWidget()
        sb_layout = QVBoxLayout(script_box)
        sb_layout.setContentsMargins(0, 0, 0, 0)
        sb_layout.setSpacing(2)

        sb_title = QLabel("台词生成：")
        sb_title.setProperty("role", "muted")
        sb_layout.addWidget(sb_title)

        self._script_group = QButtonGroup(self)
        for i, (key, label) in enumerate([
            ("doubao", "豆包"),
            ("minimax_llm", "MiniMax M3"),
        ]):
            rb = QRadioButton(label)
            rb.setProperty("script", key)
            self._script_group.addButton(rb, i)
            sb_layout.addWidget(rb)
            if key == "doubao":
                rb.setChecked(True)

        top.addWidget(script_box)
        top.addStretch(1)

        # ---- MiniMax API Key（始终显示） ----
        self.minimax_key_edit = QLineEdit(CONFIG.get_minimax("api_key", ""))
        self.minimax_key_edit.setPlaceholderText(
            "MiniMax API Key（用于 TTS / BGM / AI 生成台词）"
        )
        self.minimax_key_edit.setEchoMode(QLineEdit.Password)
        self.minimax_key_edit.setClearButtonEnabled(True)
        root.addWidget(self.minimax_key_edit)

        # ---- 豆包凭证行 ----
        doubao_row = QHBoxLayout()
        doubao_row.setSpacing(6)

        self.doubao_appid_edit = QLineEdit(CONFIG.get_volcengine("app_id", ""))
        self.doubao_appid_edit.setPlaceholderText("豆包 APP ID")

        self.doubao_token_edit = QLineEdit(CONFIG.get_volcengine("access_token", ""))
        self.doubao_token_edit.setPlaceholderText("豆包 Access Token")
        self.doubao_token_edit.setEchoMode(QLineEdit.Password)

        self.output_btn = QPushButton("📁")
        self.output_btn.setFixedWidth(36)
        self.output_btn.setToolTip("打开输出文件夹")
        self.output_btn.clicked.connect(self._open_output_dir)

        doubao_row.addWidget(self.doubao_appid_edit)
        doubao_row.addWidget(self.doubao_token_edit)
        doubao_row.addWidget(self.output_btn)
        root.addLayout(doubao_row)

        # ---- 信号连接 ----
        self._engine_group.buttonClicked.connect(self._on_engine_change)
        self.minimax_key_edit.textChanged.connect(self._on_field_changed)
        self.doubao_appid_edit.textChanged.connect(self._on_field_changed)
        self.doubao_token_edit.textChanged.connect(self._on_field_changed)

        # 防抖保存（500ms 内合并多次修改）
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_credentials)

        # 初始化可见性
        self._on_engine_change()

    # ============== 槽 ==============

    def _on_engine_change(self, *_):
        btn = self._engine_group.checkedButton()
        engine = btn.property("engine") if btn else "doubao"
        # 豆包凭证：doubao 引擎下启用
        doubao_enabled = engine == "doubao"
        self.doubao_appid_edit.setEnabled(doubao_enabled)
        self.doubao_token_edit.setEnabled(doubao_enabled)
        self.engine_changed.emit(engine)
        self._on_field_changed()  # engine 切了也保存一下

    def _on_field_changed(self):
        # 防抖：500ms 内重复触发会重置 timer，最终只写一次
        self._save_timer.start()

    def _save_credentials(self):
        config_path = Path(__file__).parent.parent.parent / "config.json"
        try:
            with _config_lock:
                if config_path.exists():
                    config_data = json.loads(config_path.read_text())
                else:
                    config_data = {}
                config_data.setdefault("volcengine", {})
                config_data.setdefault("minimax", {})
                config_data["volcengine"]["app_id"] = self.doubao_appid_edit.text().strip()
                config_data["volcengine"]["access_token"] = self.doubao_token_edit.text().strip()
                config_data["minimax"]["api_key"] = self.minimax_key_edit.text().strip()
                _atomic_write_json(config_path, config_data)
            self.credentials_saved.emit()
        except Exception as e:
            # 写盘失败不要弹窗打断用户（防抖写盘常见）；打到 stderr
            import sys
            print(f"[credentials] _save_credentials failed: {e}", file=sys.stderr)

    def _open_output_dir(self):
        out_dir = Path(__file__).parent.parent.parent / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        # macOS 用 Finder 打开；Linux/Windows fallback 到 xdg-open / explorer
        try:
            subprocess.Popen(["open", str(out_dir)])
        except FileNotFoundError:
            subprocess.Popen(["xdg-open", str(out_dir)])

    # ============== 对外 API（保持与旧 customtkinter 版一致） ==============

    def get_engine(self) -> str:
        btn = self._engine_group.checkedButton()
        return btn.property("engine") if btn else "doubao"

    def get_minimax_key(self) -> str:
        return self.minimax_key_edit.text().strip()

    def get_script_engine(self) -> str:
        btn = self._script_group.checkedButton()
        return btn.property("script") if btn else "doubao"

    def get_credentials(self) -> dict:
        return {
            "appid": self.doubao_appid_edit.text().strip(),
            "token": self.doubao_token_edit.text().strip(),
        }
