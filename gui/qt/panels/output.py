#!/usr/bin/env python3
"""
输出面板 —— PySide6 版

布局：
    +-- 状态 / 进度 --------------------------------------+
    | 状态：✅ 准备就绪                                    |
    | [██████████████░░░░░░░░] 50%                        |
    +----------------------------------------------------+
    +-- Tabs: 🎙️ 台词编辑 | 📋 日志 ---------------------+
    | [script editor / log]                              |
    +----------------------------------------------------+
    +-- 选项 + 操作 --------------------------------------+
    | [☑ loudnorm] [☑ 清理临时] 格式: [mp3 ▾]            |
    |                              [🎵 用台词生成音频]   |
    +----------------------------------------------------+

对外 API（保持与旧 customtkinter 版一致）：
  - get_output_options()  -> {normalize_audio, cleanup_temp_files, output_format}
  - set_status(msg)
  - _log(msg)             （其实叫 append_log 更准；这里保留旧名以兼容）
  - load_segments_into_editor(segments, source, fallback_text)
  - get_script_text()     新增：取当前 script editor 文本
  - generate(url, text, only_nlp_text)   主入口（新线程跑）
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui.qt import qasync
from gui.qt.theme_qt import GHIBLI


# 复用旧 minimax_script_gen 解析
try:
    from minimax_script_gen import parse_script as parse_pasted_script
except ImportError:
    def parse_pasted_script(text: str):
        return []


# ============== 台词解析（豆包/MiniMax 输出格式 → segments 列表） ==============

# 匹配 "[主播甲]: xxx" / "主播甲：xxx" / "甲: xxx" / "A: xxx" / "1: xxx" / "[旁白]: xxx"
# 故意比 minimax_script_gen 的 parse_script 宽松 —— GUI 端允许更随意的脚本格式，
# 避免用户写了"甲: 你好"结果整段被当主播甲独白合成（之前真实的 bug）。
_SPEAKER_PATTERN = re.compile(
    r"^\s*\[?("
    r"主播[甲乙丙丁戊己庚辛壬癸]"  # 主播甲..主播癸
    r"|主播[1-9]"                   # 主播1..主播9
    r"|[甲乙丙丁戊己庚辛壬癸]"        # 短标签：甲/乙/...
    r"|[A-E]"                        # 短标签：A/B/C/D/E
    r"|[1-9]"                        # 短标签：1/2/.../9
    r"|男声|女声"                     # 男声/女声
    r"|旁白"                          # 旁白
    r"|男|女"                         # 男/女
    r")\]?\s*[::：]\s*(.+)$"     # 冒号支持半角 + 全角
)


def parse_script_lines(text: str) -> List[dict]:
    """把 `[主播甲]: 你好` 风格的文本解析成 [{speaker, text}, ...] 列表。"""
    if not text:
        return []
    segments: List[dict] = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        m = _SPEAKER_PATTERN.match(line)
        if m:
            speaker = m.group(1)
            body = m.group(2).strip()
            if body:
                segments.append({"speaker": speaker, "text": body})
        # 没法解析的行忽略（用户自由编辑格式）
    return segments


# ============== OutputPanel ==============

class OutputPanel(QFrame):
    """PySide6 版输出面板。"""

    status_changed = Signal(str)
    script_loaded = Signal(int)  # 加载了几条 segment

    def __init__(self, parent=None, input_panel=None):
        super().__init__(parent)
        self.input_panel = input_panel
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            f"background-color: {GHIBLI['white']}; "
            f"border: 2px solid {GHIBLI['sky_dark']}; "
            f"border-radius: 12px;"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ---- 状态行 ----
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.status_lbl = QLabel("✅ 准备就绪")
        self.status_lbl.setStyleSheet(
            f"color: {GHIBLI['forest_dark']}; font-weight: bold; font-size: 14px;"
        )
        status_row.addWidget(self.status_lbl, 1)

        root.addLayout(status_row)

        # ---- 进度条 ----
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        root.addWidget(self.progress)

        # ---- 垂直 splitter: 台词编辑 (上) | 日志 (下) ----
        # 不再是 QTabWidget —— 用户说"上下可调"。现在两边同时可见，
        # 可以拖中间把手分配高度。台词编辑改完立刻能看生成日志，不用切 tab。
        self.tabs = QSplitter(Qt.Vertical, self)
        self.tabs.setStyleSheet(
            f"QSplitter::handle {{ background-color: {GHIBLI['dust']}; "
            f"  border-top: 1px solid {GHIBLI['cream_dark']}; "
            f"  border-bottom: 1px solid {GHIBLI['cream_dark']}; }}"
            f"QSplitter::handle:hover {{ background-color: {GHIBLI['sky_dark']}; }}"
            f"QSplitter::handle:pressed {{ background-color: {GHIBLI['totoro']}; }}"
        )
        self.tabs.setHandleWidth(6)
        self.tabs.setChildrenCollapsible(False)

        # --- 台词编辑 ---
        editor_tab = QWidget()
        e_layout = QVBoxLayout(editor_tab)
        e_layout.setContentsMargins(0, 0, 0, 0)

        editor_hint = QLabel("格式：[主播甲]: 你好    [主播乙]: 大家好")
        editor_hint.setStyleSheet(f"color: {GHIBLI['text_light']}; font-size: 11px;")
        e_layout.addWidget(editor_hint)

        self.script_editor = QPlainTextEdit()
        self.script_editor.setPlaceholderText(
            "在这里编辑或粘贴台词...\n\n"
            "格式示例：\n"
            "[主播甲]: 大家好，欢迎收听本期播客。\n"
            "[主播乙]: 今天我们聊一聊... \n\n"
            "也可以点上面「🎵 生成台词」让 AI 自动生成。"
        )
        self.script_editor.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {GHIBLI['cream']}; "
            f"border: 1.5px solid {GHIBLI['sky_dark']}; border-radius: 8px; "
            f"padding: 8px; font-size: 13px; }}"
        )
        e_layout.addWidget(self.script_editor, 1)

        self.tabs.addWidget(editor_tab)

        # --- 日志 ---
        log_tab = QWidget()
        l_layout = QVBoxLayout(log_tab)
        l_layout.setContentsMargins(0, 0, 0, 0)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Menlo", 10))
        self.log_view.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {GHIBLI['cream_dark']}; "
            f"border: 1.5px solid {GHIBLI['dust']}; border-radius: 8px; "
            f"padding: 8px; }}"
        )
        l_layout.addWidget(self.log_view, 1)
        self.tabs.addWidget(log_tab)

        # 默认比例 6:4（编辑:日志）；用户拖后由 QSplitter 自动记住
        self.tabs.setSizes([360, 240])
        self.tabs.setStretchFactor(0, 1)
        self.tabs.setStretchFactor(1, 1)

        root.addWidget(self.tabs, 1)

        # ---- 选项 + 操作 ----
        opts_row = QHBoxLayout()
        opts_row.setSpacing(8)

        self.normalize_cb = QCheckBox("🔊 音量标准化")
        self.normalize_cb.setChecked(True)
        opts_row.addWidget(self.normalize_cb)

        self.cleanup_cb = QCheckBox("🧹 清理临时文件")
        self.cleanup_cb.setChecked(True)
        opts_row.addWidget(self.cleanup_cb)

        opts_row.addWidget(QLabel("格式："))
        self.format_combo = QComboBox()
        self.format_combo.addItems(["mp3", "wav"])
        self.format_combo.currentTextChanged.connect(self._on_format_change)
        opts_row.addWidget(self.format_combo)
        opts_row.addStretch(1)

        self.gen_audio_btn = QPushButton("🎵 用台词生成音频")
        self.gen_audio_btn.setStyleSheet(
            f"background-color: {GHIBLI['totoro']}; color: {GHIBLI['white']}; "
            f"border: none; border-radius: 8px; padding: 8px 16px; "
            f"font-weight: bold; font-size: 13px;"
        )
        self.gen_audio_btn.clicked.connect(self._on_generate_audio)
        opts_row.addWidget(self.gen_audio_btn)

        root.addLayout(opts_row)

    # ============== 槽 ==============

    def _on_format_change(self, value: str):
        if value != "mp3" and self.normalize_cb.isChecked():
            self.normalize_cb.setChecked(False)
            self._log("[*] 切换为非 mp3 格式：自动禁用音量标准化")

    def _on_generate_audio(self):
        if not self.input_panel:
            return
        text = self.script_editor.toPlainText().strip()
        if not text:
            self.set_status("❌ 台词为空，请先在「✏️ 台词编辑」输入或生成台词")
            return
        # 两边始终可见，synthesize 日志实时滚到底（_log() 内部已处理）
        # 调主生成入口
        self.generate(text=text, only_nlp_text=False)

    # ============== 对外 API ==============

    def set_status(self, msg: str):
        self.status_lbl.setText(msg)
        self._log(f"[STATUS] {msg}")
        self.status_changed.emit(msg)

    def _log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{ts}] {msg}")
        # 自动滚到底
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def get_output_options(self) -> dict:
        return {
            "normalize_audio": self.normalize_cb.isChecked(),
            "cleanup_temp_files": self.cleanup_cb.isChecked(),
            "output_format": self.format_combo.currentText(),
        }

    def get_script_text(self) -> str:
        return self.script_editor.toPlainText().strip()

    def load_segments_into_editor(self, segments: list, source: str = None, fallback_text: str = None):
        """把 segments 灌进 script_editor。"""
        if segments:
            lines = [f"[{s['speaker']}]: {s['text']}" for s in segments if s.get("text")]
            self.script_editor.setPlainText("\n".join(lines))
            self.set_status(f"✅ 已加载 {len(segments)} 条台词（source={source or '?'}）")
            self.script_loaded.emit(len(segments))
            # 两边始终可见，无需切 tab
        elif fallback_text:
            self.script_editor.setPlainText(fallback_text)
            self.set_status("⚠️ 脚本解析失败，按纯文本载入，请手动整理")
            self.script_loaded.emit(0)
        else:
            self.set_status("⚠️ 没有可载入的台词")

    # ============== 主入口：生成 ==============

    def generate(self, url: str = None, text: str = None, only_nlp_text: bool = False):
        """主入口：调起对应的 TTS 引擎跑生成。

        全部走 qasync.run() 在 worker 线程里跑，UI 通过 on_log/on_status/on_progress
        回调回到主线程（Qt 框架保证线程安全）。
        """
        if not self.input_panel:
            self.set_status("❌ input_panel 未注入")
            return

        engine = self.input_panel.get_engine()
        script_engine = self.input_panel.get_script_engine()

        self.gen_audio_btn.setEnabled(False)
        self.progress.setValue(0)

        if engine == "minimax":
            if only_nlp_text or (script_engine == "minimax_llm" and text):
                self._generate_minimax_llm_script_then_audio(text or "")
            else:
                self._generate_minimax_audio(text or "")
        elif engine == "voxcpm":
            self._generate_voxcpm_audio(text or "")
        else:  # doubao
            if text:
                self._generate_doubao_audio(text)
            else:
                self.set_status("❌ 豆包引擎需要 URL 或文本输入")

    # ----- MiniMax LLM 台词生成 -----

    def _generate_minimax_llm_script_then_audio(self, text: str):
        from minimax_script_gen import MiniMaxScriptGenerator, DEFAULT_HOSTS_2
        api_key = self.input_panel.get_minimax_key()
        if not api_key:
            self.set_status("❌ 缺 MiniMax API Key")
            self.gen_audio_btn.setEnabled(True)
            return

        def work():
            from minimax_script_gen import MiniMaxScriptGenerator, DEFAULT_HOSTS_2
            gen = MiniMaxScriptGenerator(api_key=api_key)
            topic, context = ("", text) if len(text) > 600 else (text, "")
            return gen.generate(
                topic=topic, host_descriptions=DEFAULT_HOSTS_2,
                context=context, custom="",
            )

        def on_ok(segments):
            self.load_segments_into_editor(segments, source="minimax_llm")
            self.gen_audio_btn.setEnabled(True)

        def on_err(e):
            self.set_status(f"❌ MiniMax LLM 失败: {e[:200]}")
            self.gen_audio_btn.setEnabled(True)

        self.set_status("🤖 MiniMax M3 生成台词中…")
        qasync.run(work, on_result=on_ok, on_error=on_err)

    # ----- MiniMax TTS 音频生成 -----

    def _generate_minimax_audio(self, text: str):
        from minimax_tts import synthesize_segments
        api_key = self.input_panel.get_minimax_key()
        if not api_key:
            self.set_status("❌ 缺 MiniMax API Key")
            self.gen_audio_btn.setEnabled(True)
            return

        voice_map = self.input_panel.get_voice_map()
        speed_map = self.input_panel.get_speed_map()
        model = self.input_panel.get_model()
        options = self.get_output_options()

        # 把 script editor 里的文本解析成 segments
        segments = parse_script_lines(text)
        if not segments:
            self._log(
                "[!] 脚本格式无法识别，整段按主播甲处理（独白）。"
                "如需 2-host 对话，格式示例：\n"
                "  [主播甲]: 大家好\n"
                "  [主播乙]: 今天聊...\n"
                "也支持：甲:  / 乙:  / A: B:  / 1: 2:  / 主播甲：主播乙："
            )
            segments = [{"speaker": "主播甲", "text": text}]

        # voice1/voice2 是 speaker_map 未命中时的兜底；从前 2 个值里取
        voice_ids = [v for v in voice_map.values() if v]
        voice1_id = voice_ids[0] if len(voice_ids) >= 1 else ""
        voice2_id = voice_ids[1] if len(voice_ids) >= 2 else voice1_id

        def work():
            merged_path, order = synthesize_segments(
                segments=segments,
                voice1_id=voice1_id,
                voice2_id=voice2_id,
                api_key=api_key,
                model=model,
                speaker_map=voice_map,
                speed_map=speed_map,
                output_format=options["output_format"],
                progress_callback=lambda done, total: self._emit_progress(
                    done / max(total, 1), f"合成 {done}/{total}"
                ),
            )
            # 标准化（GUI 开关）
            if options["normalize_audio"]:
                try:
                    from doubao_audio import normalize_audio
                    return normalize_audio(merged_path, merged_path)
                except Exception as e:
                    self._log(f"[!] 音量标准化失败: {e}")
                    return merged_path
            return merged_path

        def on_ok(out_path):
            self.set_status(f"✅ 已生成：{out_path}")
            self.progress.setValue(100)
            self.gen_audio_btn.setEnabled(True)

        def on_err(e):
            self.set_status(f"❌ MiniMax TTS 失败: {e[:200]}")
            self.gen_audio_btn.setEnabled(True)

        self.set_status(f"🎙️ MiniMax ({model}) 合成中…")
        qasync.run(work, on_result=on_ok, on_error=on_err, on_log=self._log)

    def _emit_progress(self, p: float, msg: str):
        self.progress.setValue(int(p * 100))
        if msg:
            self._log(f"  {msg}")

    # ----- VoxCPM 本地 -----

    def _generate_voxcpm_audio(self, text: str):
        voice_map = self.input_panel.get_voice_map()
        speed_map = self.input_panel.get_speed_map()
        options = self.get_output_options()

        segments = parse_script_lines(text)
        if not segments:
            segments = [{"speaker": "主播甲", "text": text}]

        # 兜底 voice_id：取 voice_map 第一个非空
        fallback_voice = next((v for v in voice_map.values() if v), "")

        ts = time.strftime("%Y%m%d_%H%M%S")
        out_path = f"output/voxcpm_{ts}.mp3"

        segments = parse_script_lines(text)
        if not segments:
            self._log(
                "[!] 脚本格式无法识别，整段按主播甲处理（独白）。"
                "如需 2-host 对话，格式示例：\n"
                "  [主播甲]: 大家好\n"
                "  [主播乙]: 今天聊...\n"
                "也支持：甲:  / 乙:  / A: B:  / 1: 2:  / 主播甲：主播乙："
            )
            segments = [{"speaker": "主播甲", "text": text}]

        def work():
            from voxcpm_tts import synthesize_segments
            merged_path, order = synthesize_segments(
                segments=segments,
                voice_id=fallback_voice,
                output_path=out_path,
                speaker_map=voice_map,
                speed_map=speed_map,
                progress_callback=lambda done, total: self._emit_progress(
                    done / max(total, 1), f"VoxCPM 合成 {done}/{total}"
                ),
                on_log=self._log,  # 段级日志（"合成段 N/M..."），避免进度条卡 0% 让用户误以为死锁
            )
            if options["normalize_audio"]:
                try:
                    from doubao_audio import normalize_audio
                    return normalize_audio(merged_path, merged_path)
                except Exception as e:
                    self._log(f"[!] 音量标准化失败: {e}")
                    return merged_path
            return merged_path

        def on_ok(out_path):
            self.set_status(f"✅ 已生成：{out_path}")
            self.progress.setValue(100)
            self.gen_audio_btn.setEnabled(True)

        def on_err(e):
            self.set_status(f"❌ VoxCPM 失败: {e[:200]}")
            self.gen_audio_btn.setEnabled(True)

        self.set_status("🖥️ VoxCPM 本地合成中…")
        qasync.run(work, on_result=on_ok, on_error=on_err, on_log=self._log)

    # ----- 豆包 -----

    def _generate_doubao_audio(self, text: str):
        cred = self.input_panel.get_credentials()
        if not cred.get("appid") or not cred.get("token"):
            self.set_status("❌ 缺豆包凭证")
            self.gen_audio_btn.setEnabled(True)
            return

        voice_map = self.input_panel.get_voice_map()
        options = self.get_output_options()

        segments = parse_script_lines(text)
        if not segments:
            segments = [{"speaker": "主播甲", "text": text}]

        # speakers 是发音人 id 列表（豆包只支持 2 个）；取 voice_map 值的去重前 2 个
        seen = set()
        speakers = []
        for v in voice_map.values():
            if v and v not in seen:
                speakers.append(v)
                seen.add(v)
            if len(speakers) >= 2:
                break
        if not speakers:
            speakers = ["zh_male_dayixiansheng_v2_saturn_bigtts",
                        "zh_female_mizaitongxue_v2_saturn_bigtts"]

        ts = time.strftime("%Y%m%d_%H%M%S")
        out_path = f"output/doubao_{ts}.mp3"

        segments = parse_script_lines(text)
        if not segments:
            self._log(
                "[!] 脚本格式无法识别，整段按主播甲处理（独白）。"
                "如需 2-host 对话，格式示例：\n"
                "  [主播甲]: 大家好\n"
                "  [主播乙]: 今天聊...\n"
                "也支持：甲:  / 乙:  / A: B:  / 1: 2:  / 主播甲：主播乙："
            )
            segments = [{"speaker": "主播甲", "text": text}]

        def work():
            # 豆包是 async；worker 线程里跑 asyncio.run
            from doubao_podcast import generate_podcast
            result = asyncio.run(generate_podcast(
                nlp_texts=segments,
                appid=cred["appid"],
                token=cred["token"],
                speakers=speakers,
                output_path=out_path,
            ))
            final = result.get("output_path") or out_path
            if options["normalize_audio"] and final:
                try:
                    from doubao_audio import normalize_audio
                    return normalize_audio(final, final)
                except Exception as e:
                    self._log(f"[!] 音量标准化失败: {e}")
                    return final
            return final

        def on_ok(out_path):
            self.set_status(f"✅ 已生成：{out_path}")
            self.progress.setValue(100)
            self.gen_audio_btn.setEnabled(True)

        def on_err(e):
            self.set_status(f"❌ 豆包 TTS 失败: {e[:200]}")
            self.gen_audio_btn.setEnabled(True)

        self.set_status("🎙️ 豆包 TTS 合成中…")
        qasync.run(work, on_result=on_ok, on_error=on_err, on_log=self._log)
