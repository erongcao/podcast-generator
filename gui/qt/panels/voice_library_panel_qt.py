#!/usr/bin/env python3
"""
音色库面板 —— PySide6 完整版

替代旧 customtkinter VoiceLibraryPanel（1449 行），覆盖：
  - 列表 / 刷新 / 删除 / 选 voice
  - 4 个录入入口（按引擎显隐）：
      🎙️ 克隆   → CloneDialogQt     （MiniMax 上传到云端）
      🎨 设计   → DesignDialogQt    （MiniMax 文生音色）
      🎙️ 本地录制 → LocalRecordDialogQt （VoxCPM 麦克风）
      📁 导入音频 → ImportAudioDialogQt （VoxCPM 已有 wav）

数据来源：voice_library.py（与 Tk 版共用，原子 JSON 存储）
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from gui.qt import qasync
from gui.qt.theme_qt import GHIBLI


# ============== pick_voice_qt（mini 选择器，speakers.py 用）==============

def pick_voice_qt(parent: QWidget | None = None) -> Optional[str]:
    """弹模态选 voice，返回选中的 voice_id；用户取消返回 None。"""
    try:
        import voice_library as vl
        voices = vl.list_all()
    except Exception as e:
        QMessageBox.warning(parent, "音色库", f"读取音色库失败：{e}")
        return None

    if not voices:
        QMessageBox.information(
            parent, "音色库",
            "音色库还是空的。\n请到「音色库」面板录一条或克隆一条音色。",
        )
        return None

    dlg = _VoicePickerDialog(voices, parent)
    if dlg.exec() == QDialog.Accepted:
        return dlg.selected_voice_id()
    return None


class _VoicePickerDialog(QDialog):
    """简易选择器：列表 + 选一项确定。"""

    def __init__(self, voices: list[dict], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("从音色库选")
        self.setMinimumSize(420, 360)
        self.setStyleSheet(f"background-color: {GHIBLI['cream']};")

        self._voices = voices
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        title = QLabel(f"📚  音色库（{len(voices)} 个）")
        title.setStyleSheet(f"color: {GHIBLI['forest_dark']}; font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        self.list_widget = QListWidget()
        for v in voices:
            text = f"🎤  {v.get('display_name', v.get('voice_id'))}"
            notes = v.get("notes", "").strip()
            if notes:
                text += f"  — {notes[:30]}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, v.get("voice_id"))
            self.list_widget.addItem(item)
        self.list_widget.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.list_widget, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("选择")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_double_click(self, _item):
        self.accept()

    def selected_voice_id(self) -> Optional[str]:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)


# ============== 共用 voice_id 校验 ==============

_VOICE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{2,40}$")


def _validate_voice_id(voice_id: str) -> Optional[str]:
    """合法返回 None，不合法返回错误说明。"""
    if not voice_id:
        return "voice_id 不能为空"
    if not _VOICE_ID_RE.match(voice_id):
        return f"voice_id 格式错（仅字母/数字/_-，2-40 字符）：{voice_id!r}"
    return None


# ============== 主面板 ==============

class VoiceLibraryPanel(QFrame):
    """音色库主面板。set_engine() 控制按钮显隐。"""

    voice_changed = Signal(str)  # 选用时发，payload=voice_id

    def __init__(self, parent=None, get_engine=None, get_api_key=None):
        super().__init__(parent)
        self.get_engine = get_engine or (lambda: "doubao")
        self.get_api_key = get_api_key or (lambda: "")
        self._selected_voice_id: Optional[str] = None
        self._row_widgets: list[QFrame] = []

        self.setStyleSheet(
            f"background-color: {GHIBLI['white']}; "
            f"border: 2px solid {GHIBLI['totoro']}; border-radius: 12px;"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # 标题
        title = QLabel("📚  音色库")
        title.setStyleSheet(
            f"color: {GHIBLI['forest_dark']}; font-size: 16px; font-weight: bold;"
        )
        root.addWidget(title)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self.clone_btn = QPushButton("🎙️ 克隆")
        self.clone_btn.setStyleSheet(self._btn_style(GHIBLI["grass"]))
        self.clone_btn.clicked.connect(self._open_clone)
        btn_row.addWidget(self.clone_btn)

        self.design_btn = QPushButton("🎨 设计")
        self.design_btn.setStyleSheet(self._btn_style(GHIBLI["totoro"]))
        self.design_btn.clicked.connect(self._open_design)
        btn_row.addWidget(self.design_btn)

        self.local_record_btn = QPushButton("🎙️ 本地录制")
        self.local_record_btn.setStyleSheet(self._btn_style(GHIBLI["grass"]))
        self.local_record_btn.clicked.connect(self._open_local_record)
        btn_row.addWidget(self.local_record_btn)

        self.import_btn = QPushButton("📁 导入音频")
        self.import_btn.setStyleSheet(self._btn_style(GHIBLI["totoro"]))
        self.import_btn.clicked.connect(self._open_import)
        btn_row.addWidget(self.import_btn)

        self.refresh_btn = QPushButton("🔄")
        self.refresh_btn.setFixedWidth(40)
        self.refresh_btn.setStyleSheet(self._btn_style(GHIBLI["cream"]))
        self.refresh_btn.clicked.connect(self._refresh_list)
        btn_row.addWidget(self.refresh_btn)

        root.addLayout(btn_row)

        # 状态行（用于反馈）
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color: {GHIBLI['text_light']}; font-size: 11px;")
        root.addWidget(self.status_lbl)

        # 列表（用 QScrollArea + 动态 row）
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {GHIBLI['cream']}; "
            f"border: 1px solid {GHIBLI['dust']}; border-radius: 8px; }}"
        )
        self._scroll.setMinimumHeight(280)

        self._rows_widget = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(6, 6, 6, 6)
        self._rows_layout.setSpacing(4)
        self._rows_layout.addStretch(1)
        self._scroll.setWidget(self._rows_widget)
        root.addWidget(self._scroll, 1)

        self._update_button_visibility()
        self._refresh_list()

    def set_engine(self, engine: str):
        """主窗口可调；同步显隐按钮。"""
        self.get_engine = lambda e=engine: e
        self._update_button_visibility()

    def set_api_key_provider(self, fn):
        """主窗口可调；同步 API key 提供函数。"""
        self.get_api_key = fn

    def get_selected_voice_id(self) -> Optional[str]:
        return self._selected_voice_id

    def _btn_style(self, bg: str) -> str:
        return (
            f"background-color: {bg}; color: {GHIBLI['white']}; "
            f"border: none; border-radius: 6px; padding: 6px; font-weight: bold;"
        )

    def _update_button_visibility(self):
        engine = self.get_engine()
        if engine == "minimax":
            self.clone_btn.setVisible(True)
            self.design_btn.setVisible(True)
            self.local_record_btn.setVisible(False)
            self.import_btn.setVisible(False)
        elif engine == "voxcpm":
            self.clone_btn.setVisible(False)
            self.design_btn.setVisible(False)
            self.local_record_btn.setVisible(True)
            self.import_btn.setVisible(True)
        else:
            for b in (self.clone_btn, self.design_btn, self.local_record_btn, self.import_btn):
                b.setVisible(False)

    def _set_status(self, msg: str):
        self.status_lbl.setText(msg)

    # ----- 列表 -----

    def _refresh_list(self):
        # 清空旧行（保留 stretch）
        for w in self._row_widgets:
            self._rows_layout.removeWidget(w)
            w.setParent(None)
            w.deleteLater()
        self._row_widgets.clear()

        try:
            import voice_library as vl
            voices = vl.list_all()
        except Exception as e:
            self._set_status(f"❌ 读取音色库失败: {e}")
            return

        if not voices:
            empty = QLabel("（还没有音色，点上面的按钮开始录入）")
            empty.setStyleSheet(f"color: {GHIBLI['text_light']}; font-size: 12px; padding: 12px;")
            self._rows_layout.insertWidget(0, empty)
            self._row_widgets.append(empty)  # 跟踪避免被 GC
            return

        for i, v in enumerate(voices):
            row = _VoiceRow(v, on_pick=self._select_voice,
                            on_edit=self._edit_voice,
                            on_delete=self._delete_voice,
                            on_play=self._play_voice)
            self._rows_layout.insertWidget(i, row)
            self._row_widgets.append(row)
            if v["voice_id"] == self._selected_voice_id:
                row.set_selected(True)

        self._set_status(f"已加载 {len(voices)} 个音色")

    def _select_voice(self, voice_id: str):
        self._selected_voice_id = voice_id
        for r in self._row_widgets:
            if isinstance(r, _VoiceRow):
                r.set_selected(r._voice["voice_id"] == voice_id)
        self.voice_changed.emit(voice_id)
        self._set_status(f"✅ 已选：{voice_id}")

    def _edit_voice(self, voice_id: str):
        try:
            import voice_library as vl
            rec = vl.get(voice_id)
        except Exception as e:
            QMessageBox.warning(self, "编辑失败", str(e))
            return
        if not rec:
            return
        dlg = _EditDialog(rec, parent=self)
        if dlg.exec() == QDialog.Accepted:
            try:
                vl.update(voice_id,
                          notes=dlg.notes(),
                          tags=dlg.tags_list())
                self._set_status(f"✅ 已更新 {voice_id}")
                self._refresh_list()
            except Exception as e:
                QMessageBox.warning(self, "保存失败", str(e))

    def _delete_voice(self, voice_id: str):
        ans = QMessageBox.question(
            self, "删除音色",
            f"确定删除音色 `{voice_id}`？\n（关联的 WAV 也会一起删）",
        )
        if ans != QMessageBox.Yes:
            return
        try:
            import voice_library as vl
            vl.delete(voice_id)
            if self._selected_voice_id == voice_id:
                self._selected_voice_id = None
            self._refresh_list()
            self._set_status(f"🗑 已删除 {voice_id}")
        except Exception as e:
            QMessageBox.warning(self, "删除失败", str(e))

    def _play_voice(self, voice_id: str):
        """拉一个新进程调 voice_cloner.synthesize_test 试听。"""
        engine = self.get_engine()
        if engine == "minimax":
            api_key = self.get_api_key()
            if not api_key:
                QMessageBox.warning(self, "试听", "需要 MiniMax API Key")
                return
            text, ok = _ask_text(self, "试听", "输入测试文本：", "这是一个测试")
            if not ok or not text:
                return
            self._set_status(f"🔊 合成试听中…")
            def work():
                from voice_cloner import synthesize_test
                return synthesize_test(voice_id, text, api_key)
            def on_ok(mp3_bytes):
                if not mp3_bytes:
                    self._set_status("⚠️ 试听合成返回空")
                    return
                tmp = Path(tempfile.gettempdir()) / f"voice_test_{voice_id}.mp3"
                tmp.write_bytes(mp3_bytes)
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(tmp)))
                self._set_status(f"🔊 已生成 {tmp.name}（用系统播放器打开）")
            def on_err(e):
                self._set_status(f"❌ 试听失败: {str(e)[:200]}")
            qasync.run(work, on_result=on_ok, on_error=on_err)
        else:
            # VoxCPM：直接打开本地 wav
            try:
                import voice_library as vl
                rec = vl.get(voice_id)
                wav = rec.get("wav_path") if rec else None
                if wav and Path(wav).exists():
                    QDesktopServices.openUrl(QUrl.fromLocalFile(wav))
                else:
                    QMessageBox.information(self, "试听", "该音色没有本地音频文件")
            except Exception as e:
                QMessageBox.warning(self, "试听失败", str(e))

    # ----- 弹窗入口 -----

    def _open_clone(self):
        if self.get_engine() != "minimax":
            QMessageBox.information(self, "提示", "「克隆」需要 MiniMax 引擎")
            return
        api_key = self.get_api_key()
        if not api_key:
            QMessageBox.warning(self, "克隆", "请先填 MiniMax API Key")
            return
        dlg = CloneDialog(self, api_key=api_key, on_success=self._refresh_list)
        dlg.exec()

    def _open_design(self):
        if self.get_engine() != "minimax":
            QMessageBox.information(self, "提示", "「设计」需要 MiniMax 引擎")
            return
        api_key = self.get_api_key()
        if not api_key:
            QMessageBox.warning(self, "设计", "请先填 MiniMax API Key")
            return
        dlg = DesignDialog(self, api_key=api_key, on_success=self._refresh_list)
        dlg.exec()

    def _open_local_record(self):
        if self.get_engine() != "voxcpm":
            QMessageBox.information(self, "提示", "「本地录制」需要 VoxCPM 引擎")
            return
        dlg = LocalRecordDialog(self, on_success=self._refresh_list)
        dlg.exec()

    def _open_import(self):
        if self.get_engine() != "voxcpm":
            QMessageBox.information(self, "提示", "「导入音频」需要 VoxCPM 引擎")
            return
        dlg = ImportAudioDialog(self, on_success=self._refresh_list)
        dlg.exec()


# ============== 单行 row ==============

class _VoiceRow(QFrame):
    def __init__(self, voice: dict, on_pick, on_edit, on_delete, on_play):
        super().__init__()
        self._voice = voice
        self._cb_pick = on_pick
        self._cb_edit = on_edit
        self._cb_delete = on_delete
        self._cb_play = on_play
        self._selected = False

        self.setStyleSheet(
            f"background-color: {GHIBLI['cream_dark']}; border-radius: 8px;"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        # 名称
        name = QLabel(voice.get("display_name") or voice.get("voice_id"))
        name.setStyleSheet(f"color: {GHIBLI['text']}; font-weight: bold;")
        name.setFixedWidth(120)
        layout.addWidget(name)

        # tags
        tags = voice.get("tags") or []
        if tags:
            tag_lbl = QLabel(" ".join(f"#{t}" for t in tags))
            tag_lbl.setStyleSheet(f"color: {GHIBLI['forest_dark']}; font-size: 11px;")
            layout.addWidget(tag_lbl)

        # notes
        notes = voice.get("notes", "")
        if notes:
            notes_lbl = QLabel(notes[:40])
            notes_lbl.setStyleSheet(f"color: {GHIBLI['text_light']}; font-size: 11px;")
            notes_lbl.setToolTip(notes)
            layout.addWidget(notes_lbl, 1)

        # source 标签
        src = voice.get("source", "user")
        if src != "user":
            src_lbl = QLabel(f"[{src}]")
            src_lbl.setStyleSheet(f"color: {GHIBLI['text_light']}; font-size: 10px;")
            layout.addWidget(src_lbl)

        layout.addStretch(1)

        # 按钮
        for label, cb, color in (
            ("▶", lambda: on_play(voice["voice_id"]), GHIBLI["cream"]),
            ("✏", lambda: on_edit(voice["voice_id"]), GHIBLI["cream"]),
            ("🗑", lambda: on_delete(voice["voice_id"]), GHIBLI["cream"]),
        ):
            btn = QPushButton(label)
            btn.setFixedWidth(32)
            btn.setStyleSheet(
                f"background-color: {color}; color: {GHIBLI['text']}; "
                f"border: 1px solid {GHIBLI['dust']}; border-radius: 4px;"
            )
            btn.clicked.connect(cb)
            layout.addWidget(btn)

        # 选用按钮
        self.pick_btn = QPushButton("选用")
        self.pick_btn.setStyleSheet(
            f"background-color: {GHIBLI['totoro']}; color: {GHIBLI['white']}; "
            f"border: none; border-radius: 4px; padding: 4px 10px; font-weight: bold;"
        )
        self.pick_btn.clicked.connect(lambda: on_pick(voice["voice_id"]))
        layout.addWidget(self.pick_btn)

    def set_selected(self, sel: bool):
        self._selected = sel
        bg = GHIBLI["sky_dark"] if sel else GHIBLI["cream_dark"]
        self.setStyleSheet(f"background-color: {bg}; border-radius: 8px;")
        self.pick_btn.setText("已选 ✓" if sel else "选用")


# ============== 通用 ask-text helper ==============

def _ask_text(parent, title: str, label: str, default: str = "") -> tuple[str, bool]:
    from PySide6.QtWidgets import QInputDialog
    text, ok = QInputDialog.getText(parent, title, label, text=default)
    return (text.strip(), ok and bool(text))


# ============== 编辑 dialog ==============

class _EditDialog(QDialog):
    def __init__(self, rec: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"编辑音色 · {rec.get('display_name', '')}")
        self.setMinimumSize(420, 280)
        self.setStyleSheet(f"background-color: {GHIBLI['cream']};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel(f"voice_id: <code>{rec.get('voice_id')}</code>"))

        layout.addWidget(QLabel("备注 (notes)："))
        self.notes_edit = QPlainTextEdit(rec.get("notes", ""))
        self.notes_edit.setMaximumHeight(80)
        layout.addWidget(self.notes_edit)

        layout.addWidget(QLabel("标签 (逗号分隔)："))
        self.tags_edit = QLineEdit(", ".join(rec.get("tags") or []))
        layout.addWidget(self.tags_edit)

        # 标签快捷
        hint = QLabel("常用：female / male / foreign / english / no_enhance / voice_design")
        hint.setStyleSheet(f"color: {GHIBLI['text_light']}; font-size: 11px;")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def notes(self) -> str:
        return self.notes_edit.toPlainText().strip()

    def tags_list(self) -> list[str]:
        raw = self.tags_edit.text().strip()
        if not raw:
            return []
        return [t.strip() for t in raw.split(",") if t.strip()]


# ============== 本地录制 dialog（VoxCPM）==============

class LocalRecordDialog(QDialog):
    """VoxCPM 麦克风录制 → 保存到 voice_library。"""

    def __init__(self, parent=None, on_success=None):
        super().__init__(parent)
        self.on_success = on_success
        self._wav_path: Optional[str] = None
        self.setWindowTitle("本地录制新音色")
        self.setMinimumSize(480, 420)
        self.setStyleSheet(f"background-color: {GHIBLI['cream']};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel("🎙️  用麦克风录制一段参考音频（10-60 秒）"))

        # 设备选择
        layout.addWidget(QLabel("输入设备："))
        self.device_combo = QComboBox()
        try:
            from voice_recorder import list_input_devices
            devices = list_input_devices()
        except Exception as e:
            devices = []
            layout.addWidget(QLabel(f"⚠️ 无法列出输入设备: {e}"))
        self.device_combo.addItems(devices if devices else ["（无可用设备）"])
        layout.addWidget(self.device_combo)

        # 时长
        layout.addWidget(QLabel("时长（秒）："))
        self.duration_combo = QComboBox()
        self.duration_combo.addItems(["15", "20", "30", "45", "60"])
        self.duration_combo.setCurrentText("30")
        layout.addWidget(self.duration_combo)

        # 录制按钮
        self.record_btn = QPushButton("🔴 开始录音")
        self.record_btn.setStyleSheet(
            f"background-color: {GHIBLI['grass']}; color: {GHIBLI['white']}; "
            f"border: none; border-radius: 6px; padding: 8px; font-weight: bold;"
        )
        self.record_btn.clicked.connect(self._on_record)
        layout.addWidget(self.record_btn)

        # 进度
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        # 试听 wav_path
        self.wav_lbl = QLabel("（还没录）")
        self.wav_lbl.setStyleSheet(f"color: {GHIBLI['text_light']}; font-size: 11px;")
        layout.addWidget(self.wav_lbl)

        # 试听按钮
        self.play_btn = QPushButton("▶ 试听")
        self.play_btn.setStyleSheet(self._btn_style(GHIBLI["cream"]))
        self.play_btn.clicked.connect(self._on_play)
        self.play_btn.setEnabled(False)
        layout.addWidget(self.play_btn)

        # 元数据
        layout.addWidget(QLabel("voice_id（必填，字母/数字/_/-）："))
        self.voice_id_edit = QLineEdit()
        self.voice_id_edit.setPlaceholderText("my-voice-001")
        layout.addWidget(self.voice_id_edit)

        layout.addWidget(QLabel("显示名（必填）："))
        self.display_name_edit = QLineEdit()
        self.display_name_edit.setPlaceholderText("小明的男声")
        layout.addWidget(self.display_name_edit)

        layout.addWidget(QLabel("参考文本（可选，提升克隆质量）："))
        self.ref_text_edit = QPlainTextEdit()
        self.ref_text_edit.setMaximumHeight(60)
        layout.addWidget(self.ref_text_edit)

        # 底部按钮
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存到音色库")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _btn_style(self, bg: str) -> str:
        return (
            f"background-color: {bg}; color: {GHIBLI['text']}; "
            f"border: 1px solid {GHIBLI['dust']}; border-radius: 6px; padding: 6px;"
        )

    def _on_record(self):
        seconds = int(self.duration_combo.currentText())
        self.record_btn.setEnabled(False)
        self.progress.setValue(0)
        self._set_status("🎙️ 录音中…")
        def work():
            from voice_recorder import record
            # 用 QTimer 在主线程推进 progress bar
            return record(seconds=seconds)
        def on_ok(wav):
            self._wav_path = wav
            self.wav_lbl.setText(f"已录制：{wav}")
            self.play_btn.setEnabled(True)
            self.progress.setValue(100)
            self._set_status("✅ 录音完成")
            self.record_btn.setEnabled(True)
        def on_err(e):
            self._set_status(f"❌ 录音失败: {str(e)[:200]}")
            self.record_btn.setEnabled(True)
        qasync.run(work, on_result=on_ok, on_error=on_err)

    def _on_play(self):
        if self._wav_path and Path(self._wav_path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._wav_path))

    def _on_save(self):
        if not self._wav_path or not Path(self._wav_path).exists():
            QMessageBox.warning(self, "保存", "请先录音")
            return
        vid = self.voice_id_edit.text().strip()
        err = _validate_voice_id(vid)
        if err:
            QMessageBox.warning(self, "voice_id", err)
            return
        dname = self.display_name_edit.text().strip()
        if not dname:
            QMessageBox.warning(self, "显示名", "请填显示名")
            return
        ref_text = self.ref_text_edit.toPlainText().strip()
        try:
            import voice_library as vl
            vl.add(vid, dname, self._wav_path, notes="本地录制", ref_text=ref_text)
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
        if self.on_success:
            self.on_success()
        self.accept()

    def _set_status(self, msg: str):
        # 状态写到 wav_lbl 上，节省一个组件
        self.wav_lbl.setText(msg)
        self.wav_lbl.setStyleSheet(f"color: {GHIBLI['text']}; font-size: 11px;")


# ============== 导入音频 dialog（VoxCPM）==============

class ImportAudioDialog(QDialog):
    """选本地 wav → 试听 → 填 voice_id/display_name → 保存。"""

    def __init__(self, parent=None, on_success=None):
        super().__init__(parent)
        self.on_success = on_success
        self._wav_path: Optional[str] = None
        self.setWindowTitle("导入音频作为音色")
        self.setMinimumSize(480, 360)
        self.setStyleSheet(f"background-color: {GHIBLI['cream']};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel("📁  选一段 10-60 秒的 WAV（48kHz mono 推荐）"))

        pick_row = QHBoxLayout()
        self.path_lbl = QLabel("（未选）")
        self.path_lbl.setStyleSheet(f"color: {GHIBLI['text_light']};")
        pick_row.addWidget(self.path_lbl, 1)
        pick_btn = QPushButton("📂 选择文件…")
        pick_btn.setStyleSheet(
            f"background-color: {GHIBLI['grass']}; color: {GHIBLI['white']}; "
            f"border: none; border-radius: 6px; padding: 6px; font-weight: bold;"
        )
        pick_btn.clicked.connect(self._on_pick)
        pick_row.addWidget(pick_btn)
        layout.addLayout(pick_row)

        play_btn = QPushButton("▶ 试听")
        play_btn.setStyleSheet(
            f"background-color: {GHIBLI['cream']}; color: {GHIBLI['text']}; "
            f"border: 1px solid {GHIBLI['dust']}; border-radius: 6px; padding: 6px;"
        )
        play_btn.clicked.connect(self._on_play)
        layout.addWidget(play_btn)

        layout.addWidget(QLabel("voice_id："))
        self.voice_id_edit = QLineEdit()
        layout.addWidget(self.voice_id_edit)

        layout.addWidget(QLabel("显示名："))
        self.display_name_edit = QLineEdit()
        layout.addWidget(self.display_name_edit)

        layout.addWidget(QLabel("参考文本（可选）："))
        self.ref_text_edit = QPlainTextEdit()
        self.ref_text_edit.setMaximumHeight(60)
        layout.addWidget(self.ref_text_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_pick(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选 WAV", "",
            "音频文件 (*.wav *.mp3 *.m4a *.flac);;全部 (*)"
        )
        if not path:
            return
        self._wav_path = path
        self.path_lbl.setText(Path(path).name)

    def _on_play(self):
        if self._wav_path and Path(self._wav_path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._wav_path))

    def _on_save(self):
        if not self._wav_path:
            QMessageBox.warning(self, "保存", "请先选文件")
            return
        vid = self.voice_id_edit.text().strip()
        err = _validate_voice_id(vid)
        if err:
            QMessageBox.warning(self, "voice_id", err)
            return
        dname = self.display_name_edit.text().strip()
        if not dname:
            QMessageBox.warning(self, "显示名", "请填显示名")
            return
        ref_text = self.ref_text_edit.toPlainText().strip()
        try:
            import voice_library as vl
            vl.add(vid, dname, self._wav_path, notes="导入音频", ref_text=ref_text)
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
        if self.on_success:
            self.on_success()
        self.accept()


# ============== MiniMax 克隆 dialog ==============

class CloneDialog(QDialog):
    """MiniMax 上传 wav 到云端 + 拿 voice_id。"""

    def __init__(self, parent=None, api_key: str = "", on_success=None):
        super().__init__(parent)
        self.api_key = api_key
        self.on_success = on_success
        self._wav_path: Optional[str] = None
        self.setWindowTitle("克隆新音色 · MiniMax")
        self.setMinimumSize(480, 380)
        self.setStyleSheet(f"background-color: {GHIBLI['cream']};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel("🎙️  选 10-60 秒的参考音频上传到 MiniMax 云端"))

        pick_row = QHBoxLayout()
        self.path_lbl = QLabel("（未选）")
        self.path_lbl.setStyleSheet(f"color: {GHIBLI['text_light']};")
        pick_row.addWidget(self.path_lbl, 1)
        pick_btn = QPushButton("📂 选择文件…")
        pick_btn.setStyleSheet(
            f"background-color: {GHIBLI['grass']}; color: {GHIBLI['white']}; "
            f"border: none; border-radius: 6px; padding: 6px;"
        )
        pick_btn.clicked.connect(self._on_pick)
        pick_row.addWidget(pick_btn)
        layout.addLayout(pick_row)

        layout.addWidget(QLabel("voice_id："))
        self.voice_id_edit = QLineEdit()
        self.voice_id_edit.setPlaceholderText("my-voice-001")
        layout.addWidget(self.voice_id_edit)

        layout.addWidget(QLabel("显示名："))
        self.display_name_edit = QLineEdit()
        layout.addWidget(self.display_name_edit)

        layout.addWidget(QLabel("备注："))
        self.notes_edit = QLineEdit()
        layout.addWidget(self.notes_edit)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color: {GHIBLI['text_light']}; font-size: 11px;")
        layout.addWidget(self.status_lbl)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("上传并保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_pick(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选 WAV", "",
            "音频文件 (*.wav *.mp3 *.m4a);;全部 (*)"
        )
        if path:
            self._wav_path = path
            self.path_lbl.setText(Path(path).name)

    def _on_save(self):
        if not self._wav_path:
            QMessageBox.warning(self, "克隆", "请先选文件")
            return
        vid = self.voice_id_edit.text().strip()
        err = _validate_voice_id(vid)
        if err:
            QMessageBox.warning(self, "voice_id", err)
            return
        dname = self.display_name_edit.text().strip() or vid
        notes = self.notes_edit.text().strip() or "MiniMax 云端克隆"

        self.status_lbl.setText("☁️ 上传中…")
        def work():
            from voice_cloner import clone
            return clone(self._wav_path, vid, self.api_key)
        def on_ok(server_voice_id):
            # 上传成功：server 返回的是 voice_id 字符串（同名 = 用本地 vid）
            try:
                import voice_library as vl
                vl.add(vid, dname, self._wav_path, notes=notes, tags=["voice_clone"])
            except Exception as e:
                raise RuntimeError(f"上传成功但保存到库失败: {e}")
            return vid
        def on_err(e):
            self.status_lbl.setText(f"❌ 上传失败: {str(e)[:200]}")
        qasync.run(work, on_result=lambda _: (
            self.status_lbl.setText("✅ 上传并保存成功"),
            self.on_success() if self.on_success else None,
            self.accept()
        ), on_error=on_err)


# ============== MiniMax 文字设计 dialog ==============

class DesignDialog(QDialog):
    """用文字描述生成 MiniMax 音色。"""

    def __init__(self, parent=None, api_key: str = "", on_success=None):
        super().__init__(parent)
        self.api_key = api_key
        self.on_success = on_success
        self.setWindowTitle("设计新音色 · MiniMax")
        self.setMinimumSize(480, 360)
        self.setStyleSheet(f"background-color: {GHIBLI['cream']};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel("🎨  用文字描述生成 MiniMax 音色"))

        layout.addWidget(QLabel("音色描述："))
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("40岁男医生，语速慢，沉稳专业")
        self.prompt_edit.setMaximumHeight(80)
        layout.addWidget(self.prompt_edit)

        row = QHBoxLayout()
        row.addWidget(QLabel("性别："))
        self.gender_combo = QComboBox()
        self.gender_combo.addItems(["male", "female", "neutral"])
        row.addWidget(self.gender_combo)
        row.addSpacing(20)
        row.addWidget(QLabel("语言："))
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["Chinese", "English", "Japanese"])
        row.addWidget(self.lang_combo)
        row.addStretch(1)
        layout.addLayout(row)

        layout.addWidget(QLabel("voice_id："))
        self.voice_id_edit = QLineEdit()
        layout.addWidget(self.voice_id_edit)

        layout.addWidget(QLabel("显示名："))
        self.display_name_edit = QLineEdit()
        layout.addWidget(self.display_name_edit)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color: {GHIBLI['text_light']}; font-size: 11px;")
        layout.addWidget(self.status_lbl)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("生成并保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_save(self):
        prompt = self.prompt_edit.toPlainText().strip()
        if not prompt:
            QMessageBox.warning(self, "设计", "请填音色描述")
            return
        vid = self.voice_id_edit.text().strip()
        err = _validate_voice_id(vid)
        if err:
            QMessageBox.warning(self, "voice_id", err)
            return
        dname = self.display_name_edit.text().strip() or vid

        self.status_lbl.setText("🪄 生成中…")
        gender = self.gender_combo.currentText()
        lang = self.lang_combo.currentText()
        def work():
            from doubao_voice_design import design_voice
            return design_voice(prompt=prompt, api_key=self.api_key,
                                gender=gender, language=lang)
        def on_ok(preview_mp3):
            try:
                import voice_library as vl
                vl.add_designed(vid, dname, preview_mp3,
                                notes=prompt, tags=["voice_design"])
            except Exception as e:
                raise RuntimeError(f"生成成功但保存失败: {e}")
            return vid
        def on_err(e):
            self.status_lbl.setText(f"❌ 生成失败: {str(e)[:200]}")
        qasync.run(work, on_result=lambda _: (
            self.status_lbl.setText("✅ 生成并保存成功"),
            self.on_success() if self.on_success else None,
            self.accept()
        ), on_error=on_err)
