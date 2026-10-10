#!/usr/bin/env python3
"""
片头片尾设置面板 —— PySide6 版

对外 API（与旧 customtkinter 版一致）：
  - get_intro_outro() -> (intro_mode, intro_prompt, outro_mode, outro_prompt)
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
)

from gui.qt.theme_qt import GHIBLI


class IntroOutroPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.setStyleSheet(
            f"background-color: {GHIBLI['cream_dark']}; "
            f"border-radius: 8px;"
        )

        layout = QGridLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(4)

        intro_lbl = QLabel("🎶 片头音乐")
        intro_lbl.setStyleSheet(f"color: {GHIBLI['text']}; font-weight: bold;")
        layout.addWidget(intro_lbl, 0, 0)

        outro_lbl = QLabel("🎶 片尾音乐")
        outro_lbl.setStyleSheet(f"color: {GHIBLI['text']}; font-weight: bold;")
        layout.addWidget(outro_lbl, 0, 1)

        # intro mode
        self.intro_mode = QComboBox()
        self.intro_mode.addItems(["无", "纯音乐", "AI生成"])
        self.intro_mode.currentTextChanged.connect(self._on_intro_mode_change)
        layout.addWidget(self.intro_mode, 1, 0)

        # outro mode
        self.outro_mode = QComboBox()
        self.outro_mode.addItems(["无", "纯音乐", "AI生成"])
        self.outro_mode.currentTextChanged.connect(self._on_outro_mode_change)
        layout.addWidget(self.outro_mode, 1, 1)

        # AI prompts
        self.intro_prompt = QLineEdit()
        self.intro_prompt.setPlaceholderText("AI音乐描述，如：calm piano")
        self.intro_prompt.setVisible(False)
        layout.addWidget(self.intro_prompt, 2, 0)

        self.outro_prompt = QLineEdit()
        self.outro_prompt.setPlaceholderText("AI音乐描述，如：peaceful ending")
        self.outro_prompt.setVisible(False)
        layout.addWidget(self.outro_prompt, 2, 1)

        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)

    def _on_intro_mode_change(self, value: str):
        self.intro_prompt.setVisible(value == "AI生成")

    def _on_outro_mode_change(self, value: str):
        self.outro_prompt.setVisible(value == "AI生成")

    def get_intro_outro(self) -> tuple[str, str, str, str]:
        return (
            self.intro_mode.currentText(),
            self.intro_prompt.text().strip(),
            self.outro_mode.currentText(),
            self.outro_prompt.text().strip(),
        )
