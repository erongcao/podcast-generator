#!/usr/bin/env python3
"""
音色选择面板 —— PySide6 版

三类组件：
  - SpeakerRow       豆包引擎专用：固定男声/女声两个下拉
  - MiniMaxVoiceList MiniMax 引擎专用：动态多行（每行：选 voice + 调速）
  - VoxCPMVoiceList  本地 VoxCPM 引擎专用：动态多行（强制走本地音色库）

对外接口（与旧 customtkinter 版完全一致）：
  - list.get_model()         -> str   （仅 MiniMax）
  - list.get_voice_map()     -> {label: voice_id}
  - list.get_speed_map()     -> {label: speed}
"""

from __future__ import annotations

import minimax_tts
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from gui.qt.theme_qt import GHIBLI


# ============== 共享数据 ==============

ROLE_LABELS = ["主播甲", "主播乙", "主播丙", "主播丁", "主播戊",
               "主播己", "主播庚", "主播辛", "主播壬", "主播癸"]
MAX_VOICES = len(ROLE_LABELS)
DOUBAO_MAX_VOICES = 2

MALE_SPEAKERS = [
    ("zh_male_dayixiansheng_v2_saturn_bigtts", "男声 · 大衣先生"),
]
FEMALE_SPEAKERS = [
    ("zh_female_mizaitongxue_v2_saturn_bigtts", "女声 · 米渣同学"),
]

MINIMAX_VOICES = [
    # 男声
    ("male-qn-qingse", "男声 · 青年清涩"),
    ("male-qn-jingying", "男声 · 精英沉稳"),
    ("male-qn-badao", "男声 · 霸道有力"),
    ("male-qn-daxuesheng", "男声 · 大学生青春"),
    ("Chinese (Mandarin)_Male_Announcer", "男声 · 播音员"),
    ("Chinese (Mandarin)_Warm_Bestie", "男声 · 暖心闺蜜"),
    # 女声
    ("female-shaonv", "女声 · 少女甜美"),
    ("female-yujie", "女声 · 御姐成熟"),
    ("female-chengshu", "女声 · 成熟知性"),
    ("female-tianmei", "女声 · 甜美"),
    ("Chinese (Mandarin)_Lyrical_Voice", "女声 · 抒情婉转"),
    ("Chinese (Mandarin)_Radio_Host", "女声 · 电台主播"),
    ("Chinese (Mandarin)_Sweet_Lady", "女声 · 甜蜜女生"),
    ("Chinese (Mandarin)_Kind-hearted_Antie", "女声 · 善良阿姨"),
    ("Japanese_KindLady", "女声 · 日系温柔"),
    # 复刻音色
    ("custom_voice_1", "🎤 复刻音色 1（输入ID）"),
    ("custom_voice_2", "🎤 复刻音色 2（输入ID）"),
]


def _lookup_minimax_voice_id(display_text: str) -> str | None:
    return next((k for k, v in MINIMAX_VOICES if v == display_text), None)


def _display_name_for(voice_id: str) -> str:
    rec = next((v for k, v in MINIMAX_VOICES if k == voice_id), None)
    if rec:
        return rec
    return voice_id


# ============== 豆包引擎：固定男/女声 ==============

class SpeakerRow(QFrame):
    """豆包引擎：固定男声/女声两个下拉（Doubao API 上限 2 个）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        layout = QGridLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(4)

        lbl_m = QLabel("🎭 男声主播")
        lbl_m.setStyleSheet(f"color: {GHIBLI['text']}; font-weight: bold;")
        lbl_f = QLabel("🎭 女声主播")
        lbl_f.setStyleSheet(f"color: {GHIBLI['text']}; font-weight: bold;")
        layout.addWidget(lbl_m, 0, 0)
        layout.addWidget(lbl_f, 0, 1)

        self.male_combo = QComboBox()
        self.male_combo.addItems([s[1] for s in MALE_SPEAKERS])
        self.male_combo.setCurrentIndex(0)
        self.female_combo = QComboBox()
        self.female_combo.addItems([s[1] for s in FEMALE_SPEAKERS])
        self.female_combo.setCurrentIndex(0)

        layout.addWidget(self.male_combo, 1, 0)
        layout.addWidget(self.female_combo, 1, 1)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)

    def get_speakers(self) -> list[str]:
        s1 = next((k for k, v in MALE_SPEAKERS if v == self.male_combo.currentText()),
                  MALE_SPEAKERS[0][0])
        s2 = next((k for k, v in FEMALE_SPEAKERS if v == self.female_combo.currentText()),
                  FEMALE_SPEAKERS[0][0])
        return [s1, s2]


# ============== 通用 base：单行音色配置 ==============

class _VoiceRowBase(QFrame):
    """MiniMax / VoxCPM 行的基类。"""

    def __init__(self, parent=None, on_remove=None):
        super().__init__(parent)
        self._index = 0
        self._voice_id: str | None = None
        self._on_remove = on_remove
        self.setStyleSheet(
            f"background-color: {GHIBLI['cream_dark']}; "
            f"border-radius: 8px;"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        self._build()

    def _build(self):
        layout = QGridLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(4)

        # 角色标签
        self._label = QLabel("🎭 主播甲")
        self._label.setStyleSheet(f"color: {GHIBLI['text']}; font-weight: bold;")
        self._label.setFixedWidth(72)
        layout.addWidget(self._label, 0, 0)

        # 主控件（子类具体实现 _make_picker_button）
        self.pick_btn = self._make_picker_button()
        layout.addWidget(self.pick_btn, 0, 1)

        # 删除按钮
        self._remove_btn = QPushButton("🗑")
        self._remove_btn.setFixedWidth(36)
        self._remove_btn.setProperty("role", "secondary")
        self._remove_btn.setEnabled(False)
        self._remove_btn.clicked.connect(self._handle_remove)
        layout.addWidget(self._remove_btn, 0, 2)

        # 语速滑块
        speed_lbl = QLabel("🐢 语速")
        speed_lbl.setStyleSheet(f"color: {GHIBLI['text']};")
        speed_lbl.setFixedWidth(72)
        layout.addWidget(speed_lbl, 1, 0)

        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setMinimum(self._speed_min())
        self.speed_slider.setMaximum(self._speed_max())
        self.speed_slider.setValue(self._speed_default())
        self.speed_slider.setTickPosition(QSlider.NoTicks)
        self.speed_slider.valueChanged.connect(self._on_speed_change)
        layout.addWidget(self.speed_slider, 1, 1)

        self._speed_value_lbl = QLabel("1.00x")
        self._speed_value_lbl.setStyleSheet(f"color: {GHIBLI['text']}; font-weight: bold;")
        self._speed_value_lbl.setFixedWidth(48)
        self._speed_value_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._speed_value_lbl, 1, 2)

    # ----- 子类重写 -----
    def _make_picker_button(self) -> QPushButton: ...
    def _speed_min(self) -> int: ...
    def _speed_max(self) -> int: ...
    def _speed_default(self) -> int: ...

    # ----- helpers -----
    def _on_speed_change(self, raw: int):
        # slider 存 0.05 步进的整数；显示成 1.00x
        val = raw / 100.0
        self._speed_value_lbl.setText(f"{val:.2f}x")

    def _handle_remove(self):
        if self._on_remove:
            self._on_remove(self._index)

    def set_index(self, new_index: int):
        self._index = new_index
        label = ROLE_LABELS[new_index] if new_index < len(ROLE_LABELS) else f"主播{new_index+1}"
        self._label.setText(f"🎭 {label}")
        # 前 2 行不能删（保证主播甲/乙必在）
        self._remove_btn.setEnabled(new_index >= 2)

    def get_label(self) -> str:
        return ROLE_LABELS[self._index] if self._index < len(ROLE_LABELS) else f"主播{self._index+1}"

    def get_voice_id(self) -> str:
        return self._voice_id or ""

    def get_speed(self) -> float:
        return round(self.speed_slider.value() / 100.0, 2)


# ============== MiniMax 行 ==============

class MiniMaxVoiceRow(_VoiceRowBase):
    """MiniMax 引擎：行内可从音色库选 / MiniMax 系统预设下拉 / 粘贴 ID。"""

    def __init__(self, parent=None, on_remove=None):
        # 0.5-2.0 × 100 = 50-200
        self._SPEED_MIN, self._SPEED_MAX, self._SPEED_DEFAULT = 50, 200, 100
        super().__init__(parent, on_remove)
        self._build_advanced()

    def _speed_min(self) -> int: return self._SPEED_MIN
    def _speed_max(self) -> int: return self._SPEED_MAX
    def _speed_default(self) -> int: return self._SPEED_DEFAULT

    def _make_picker_button(self) -> QPushButton:
        btn = QPushButton("📚 从音色库选…")
        btn.setStyleSheet(
            f"background-color: {GHIBLI['cream']}; color: {GHIBLI['text']}; "
            f"border: 1.5px solid {GHIBLI['dust']}; border-radius: 6px; "
            f"padding: 6px; min-height: 28px;"
        )
        btn.clicked.connect(self._open_picker)
        return btn

    def _build_advanced(self):
        # 在 picker 按钮下面插一行：高级（系统预设 + 粘贴 ID）
        self._advanced = QFrame()
        self._advanced.setStyleSheet(
            f"background-color: {GHIBLI['cream']}; border-radius: 6px;"
        )
        adv = QGridLayout(self._advanced)
        adv.setContentsMargins(6, 4, 6, 4)
        adv.setHorizontalSpacing(4)
        adv.setVerticalSpacing(2)

        preset_lbl = QLabel("系统预设")
        preset_lbl.setStyleSheet(f"color: {GHIBLI['text_light']};")
        preset_lbl.setFixedWidth(60)
        adv.addWidget(preset_lbl, 0, 0)

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("（不选）")
        for _, v in MINIMAX_VOICES:
            if not v.startswith(("📚", "🎤 复刻")):
                self.preset_combo.addItem(v)
        self.preset_combo.currentTextChanged.connect(self._on_preset_pick)
        adv.addWidget(self.preset_combo, 0, 1, 1, 2)

        custom_lbl = QLabel("粘贴 ID")
        custom_lbl.setStyleSheet(f"color: {GHIBLI['text_light']};")
        adv.addWidget(custom_lbl, 1, 0)

        from PySide6.QtWidgets import QLineEdit
        self.custom_edit = QLineEdit()
        self.custom_edit.setPlaceholderText("任意 MiniMax voice_id")
        self.custom_edit.editingFinished.connect(self._on_custom_id_change)
        adv.addWidget(self.custom_edit, 1, 1, 1, 2)

        # 嵌进主 grid（row 2，跨 3 列）
        self.layout().addWidget(self._advanced, 2, 0, 1, 3)

    def _open_picker(self):
        # 占位：先用 QMessageBox 提示；voice_library_panel 完成后会替换为真正的 modal
        from PySide6.QtWidgets import QMessageBox
        from gui.qt.panels.voice_library_panel_qt import pick_voice_qt
        voice_id = pick_voice_qt(self.window())
        if voice_id:
            self._set_voice(voice_id, source="library")

    def _on_preset_pick(self, display: str):
        if display == "（不选）":
            return
        voice_id = _lookup_minimax_voice_id(display)
        if voice_id:
            self.custom_edit.clear()
            self._set_voice(voice_id, source="preset")

    def _on_custom_id_change(self):
        custom = self.custom_edit.text().strip()
        if not custom:
            return
        self.preset_combo.setCurrentIndex(0)
        self._set_voice(custom, source="custom")

    def _set_voice(self, voice_id: str, source: str):
        self._voice_id = voice_id
        if source == "library":
            try:
                import voice_library as vl
                rec = vl.get(voice_id)
                display = rec["display_name"] if rec else voice_id
            except Exception:
                display = voice_id
        else:
            display = _display_name_for(voice_id)
        self.pick_btn.setText(f"🎤 {display}  ▾")


# ============== VoxCPM 行 ==============

class VoxCPMVoiceRow(_VoiceRowBase):
    """VoxCPM 引擎：行内只能从本地音色库选。"""

    def __init__(self, parent=None, on_remove=None):
        # VoxCPM 走 ffmpeg atempo 变速；范围 0.5-1.5 跟 MiniMax 对齐，
        # 给"模型忠实克隆 ref 节奏导致偏快"留出更慢的余量；默认 0.90x 兜底。
        self._SPEED_MIN, self._SPEED_MAX, self._SPEED_DEFAULT = 50, 150, 90
        super().__init__(parent, on_remove)

    def _speed_min(self) -> int: return self._SPEED_MIN
    def _speed_max(self) -> int: return self._SPEED_MAX
    def _speed_default(self) -> int: return self._SPEED_DEFAULT

    def _make_picker_button(self) -> QPushButton:
        btn = QPushButton("📚 从音色库选…")
        btn.setStyleSheet(
            f"background-color: {GHIBLI['cream']}; color: {GHIBLI['text']}; "
            f"border: 1.5px solid {GHIBLI['dust']}; border-radius: 6px; "
            f"padding: 6px; min-height: 28px;"
        )
        btn.clicked.connect(self._open_picker)
        return btn

    def _open_picker(self):
        from gui.qt.panels.voice_library_panel_qt import pick_voice_qt
        voice_id = pick_voice_qt(self.window())
        if voice_id:
            self._set_voice(voice_id)

    def _set_voice(self, voice_id: str):
        self._voice_id = voice_id
        try:
            import voice_library as vl
            rec = vl.get(voice_id)
            display = rec["display_name"] if rec else voice_id
        except Exception:
            display = voice_id
        self.pick_btn.setText(f"🎤 {display}  ▾")


# ============== MiniMax 动态列表 ==============

class MiniMaxVoiceList(QFrame):
    """MiniMax 引擎用的动态音色列表：模型选择器 + 滚动行 + ➕ 按钮。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 模型选择器
        model_row = QHBoxLayout()
        model_row.setSpacing(6)
        lbl = QLabel("模型：")
        lbl.setStyleSheet(f"color: {GHIBLI['text']};")
        model_row.addWidget(lbl)

        self.model_combo = QComboBox()
        self.model_combo.addItems(minimax_tts.get_supported_models())
        # 选当前默认
        default_model = minimax_tts.DEFAULT_MODEL
        idx = self.model_combo.findText(default_model)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        model_row.addWidget(self.model_combo, 1)
        layout.addLayout(model_row)

        # 滚动行容器
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {GHIBLI['cream_dark']}; "
            f"border: 1px solid {GHIBLI['dust']}; border-radius: 6px; }}"
        )
        self._scroll.setMinimumHeight(280)
        # 让 scroll area 在 voice_list 里能撑满（而不是被 240px 卡住看不到主播乙）
        from PySide6.QtWidgets import QSizePolicy
        self._scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._rows_widget = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(4, 4, 4, 4)
        self._rows_layout.setSpacing(4)
        self._rows_layout.addStretch(1)
        self._scroll.setWidget(self._rows_widget)
        layout.addWidget(self._scroll, 1)

        # ➕ 按钮
        self._add_btn = QPushButton("➕  添加音色")
        self._add_btn.setStyleSheet(
            f"background-color: {GHIBLI['grass']}; color: {GHIBLI['white']}; "
            f"border: none; border-radius: 6px; padding: 6px; font-weight: bold;"
        )
        self._add_btn.clicked.connect(self._add_row)
        layout.addWidget(self._add_btn)

        # 默认 2 行
        self.rows: list[MiniMaxVoiceRow] = []
        self._add_row()
        self._add_row()

    def _add_row(self):
        if len(self.rows) >= MAX_VOICES:
            return
        # insert before stretch
        row = MiniMaxVoiceRow(self._rows_widget, on_remove=self._remove_row)
        idx = len(self.rows)
        self._rows_layout.insertWidget(idx, row)
        row.set_index(idx)
        self.rows.append(row)

    def _remove_row(self, index: int):
        if index < 2 or index >= len(self.rows):
            return
        row = self.rows.pop(index)
        self._rows_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        # 重编号
        for i, r in enumerate(self.rows):
            r.set_index(i)

    def get_model(self) -> str:
        return self.model_combo.currentText()

    def get_voice_map(self) -> dict:
        return {r.get_label(): r.get_voice_id() for r in self.rows}

    def get_speed_map(self) -> dict:
        return {r.get_label(): r.get_speed() for r in self.rows}


# ============== VoxCPM 动态列表 ==============

class VoxCPMVoiceList(QFrame):
    """VoxCPM 本地引擎用的动态音色列表（无模型选择器）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {GHIBLI['cream_dark']}; "
            f"border: 1px solid {GHIBLI['dust']}; border-radius: 6px; }}"
        )
        self._scroll.setMinimumHeight(280)
        from PySide6.QtWidgets import QSizePolicy
        self._scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._rows_widget = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(4, 4, 4, 4)
        self._rows_layout.setSpacing(4)
        self._rows_layout.addStretch(1)
        self._scroll.setWidget(self._rows_widget)
        layout.addWidget(self._scroll, 1)

        self._add_btn = QPushButton("➕  添加音色")
        self._add_btn.setStyleSheet(
            f"background-color: {GHIBLI['grass']}; color: {GHIBLI['white']}; "
            f"border: none; border-radius: 6px; padding: 6px; font-weight: bold;"
        )
        self._add_btn.clicked.connect(self._add_row)
        layout.addWidget(self._add_btn)

        self.rows: list[VoxCPMVoiceRow] = []
        self._add_row()
        self._add_row()

    def _add_row(self):
        if len(self.rows) >= MAX_VOICES:
            return
        row = VoxCPMVoiceRow(self._rows_widget, on_remove=self._remove_row)
        idx = len(self.rows)
        self._rows_layout.insertWidget(idx, row)
        row.set_index(idx)
        self.rows.append(row)

    def _remove_row(self, index: int):
        if index < 2 or index >= len(self.rows):
            return
        row = self.rows.pop(index)
        self._rows_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        for i, r in enumerate(self.rows):
            r.set_index(i)

    def get_voice_map(self) -> dict:
        return {r.get_label(): r.get_voice_id() for r in self.rows if r.get_voice_id()}

    def get_speed_map(self) -> dict:
        return {r.get_label(): r.get_speed() for r in self.rows}
