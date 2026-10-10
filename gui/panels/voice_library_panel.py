#!/usr/bin/env python3
"""
音色库面板：克隆 UI + 库列表 + VoicePickerModal

Spec requirements addressed here:
  - Countdown UI during recording (big numeric + progress bar)
  - Required post-record preview (no skip)
  - Configurable recording duration (10-60s, default 30s)
  - Separate ASCII-validated voice_id field (not derived from Chinese display_name)
  - Overwrite race fix: clone API first, only delete old voice on success
  - messagebox imported (otherwise AttributeError on error paths)
"""
import os
import re
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import simpledialog, messagebox
from pathlib import Path

import customtkinter as ctk

from gui.theme import GHIBLI

import voice_library as vl
import voice_recorder as vr
import voice_cloner as vc
import audio_cleanup


SAMPLE_TEXTS = {
    "通用": (
        "今天我们来聊一聊健康与生活的话题。保持规律的作息非常重要,每天保证七到八小时的充足睡眠。"
        "饮食方面要注重营养均衡,多吃新鲜的蔬菜水果,适量摄入优质蛋白质。"
        "另外,坚持每天散步三十分钟,对身体会有很大的改善。"
    ),
    "医疗": (
        "高血压患者需要定期监测血压,建议每天早晚各测量一次并记录数据。"
        "饮食上应该控制盐分的摄入,每天不超过五克,少吃过咸的腌制食品。"
        "保持适度运动,比如每天散步三十分钟,按时服药,对控制血压很有帮助。"
    ),
    "慢速清晰": (
        "欢迎使用本系统进行声音克隆。请——慢——速——清——晰——地——朗——读——每——个——字。"
        "注——意——控——制——节——奏——,避——免——吞——字——现——象。"
        "保——持——均——匀——呼——吸,声——音——清——晰——稳——定。"
    ),
}

DEFAULT_DURATION = 30
DURATION_OPTIONS = [15, 20, 30, 45, 60]
VOICE_ID_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{7,255}$")

LIBRARY_GRAYOUT_TOOLTIP = "豆包引擎不支持音色克隆，请切换到 MiniMax 或本地 VoxCPM"


class VoiceLibraryPanel(ctk.CTkFrame):
    def __init__(self, parent, get_engine, get_api_key, **kwargs):
        super().__init__(parent, **kwargs)
        self.get_engine = get_engine
        self.get_api_key = get_api_key
        self._selected_voice_id = None
        self._voice_rows = []  # 缓存 row 引用，便于更新高亮
        self._build_ui()
        self._refresh_list()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self, text="📚 音色库",
            font=ctk.CTkFont("Helvetica Neue", 13, "bold"),
            text_color=GHIBLI["text"],
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 4))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 4))
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)
        btn_frame.grid_columnconfigure(2, weight=1)
        btn_frame.grid_columnconfigure(3, weight=1)
        btn_frame.grid_columnconfigure(4, weight=0)

        # MiniMax 专用：上传音频到云端克隆 + 用文字设计音色
        self.clone_btn = ctk.CTkButton(
            btn_frame, text="🎙️ 克隆",
            fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
            text_color=GHIBLI["white"], corner_radius=6,
            command=self._open_clone_dialog,
        )
        self.clone_btn.grid(row=0, column=0, sticky="ew", padx=(0, 2))

        self.design_btn = ctk.CTkButton(
            btn_frame, text="🎨 设计",
            fg_color=GHIBLI["totoro"], hover_color=GHIBLI["totoro_dark"],
            text_color=GHIBLI["white"], corner_radius=6,
            command=self._open_design_dialog,
        )
        self.design_btn.grid(row=0, column=1, sticky="ew", padx=(2, 2))

        # 本地 VoxCPM 专用：本地录制 + 导入音频文件
        self.local_record_btn = ctk.CTkButton(
            btn_frame, text="🎙️ 本地录制",
            fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
            text_color=GHIBLI["white"], corner_radius=6,
            command=self._open_local_record_dialog,
        )
        self.local_record_btn.grid(row=0, column=2, sticky="ew", padx=(2, 2))

        self.import_btn = ctk.CTkButton(
            btn_frame, text="📁 导入音频",
            fg_color=GHIBLI["totoro"], hover_color=GHIBLI["totoro_dark"],
            text_color=GHIBLI["white"], corner_radius=6,
            command=self._open_import_dialog,
        )
        self.import_btn.grid(row=0, column=3, sticky="ew", padx=(2, 2))

        self.refresh_btn = ctk.CTkButton(
            btn_frame, text="🔄", width=40,
            fg_color=GHIBLI["cream"], hover_color=GHIBLI["dust"],
            text_color=GHIBLI["text"], corner_radius=6,
            command=self._refresh_list,
        )
        self.refresh_btn.grid(row=0, column=4, sticky="e", padx=(4, 0))

        # 初始状态：按当前引擎显隐按钮
        self._update_button_visibility()

        self.list_frame = ctk.CTkScrollableFrame(
            self, fg_color=GHIBLI["cream"], corner_radius=6, height=140,
        )
        self.list_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 8))
        self.grid_rowconfigure(2, weight=1)
        self.list_frame.grid_columnconfigure(0, weight=1)

    def _refresh_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._voice_rows = []
        voices = vl.list_all()
        if not voices:
            ctk.CTkLabel(
                self.list_frame, text="（还没有音色，点击「克隆新音色」开始）",
                text_color=GHIBLI["text_light"],
                font=ctk.CTkFont("Helvetica Neue", 11),
            ).grid(row=0, column=0, padx=10, pady=10)
            return
        for i, v in enumerate(voices):
            row = _VoiceRow(
                self.list_frame, v,
                on_play=self._play_voice,
                on_edit=self._edit_voice,
                on_delete=self._delete_voice,
                on_pick=self._select_voice,
                on_clean=self._clean_voice,
            )
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=2)
            self._voice_rows.append(row)
            # 高亮之前选中的（库刷新后保留用户选择）
            if v["voice_id"] == self._selected_voice_id:
                row.set_selected(True)

    def _select_voice(self, voice_id):
        """用户点击「选用」：标记选中并刷新高亮。"""
        self._selected_voice_id = voice_id
        for r in self._voice_rows:
            r.set_selected(r._voice["voice_id"] == voice_id)

    def get_selected_voice_id(self):
        """当前用户在音色库里选中的 voice_id（output.py 合成时调用）。"""
        return self._selected_voice_id

    def _open_clone_dialog(self):
        if self.get_engine() != "minimax":
            messagebox.showinfo(
                "提示",
                "「克隆」是把音频上传到 MiniMax 云端，需要 MiniMax 引擎。\n"
                "当前本地 VoxCPM 引擎请用「本地录制」或「导入音频」。",
            )
            return
        api_key = self.get_api_key()
        if not api_key:
            messagebox.showerror("错误", "请先在凭证面板填写 MiniMax API Key")
            return
        CloneDialog(self, api_key=api_key, on_success=self._refresh_list)

    def _open_design_dialog(self):
        """Open the Voice Design dialog (text-to-voice without recording)."""
        if self.get_engine() != "minimax":
            messagebox.showinfo(
                "提示",
                "「设计」是 MiniMax 的文生音色能力，需要 MiniMax 引擎。\n"
                "本地 VoxCPM 不支持文字设计音色，请用「本地录制」或「导入音频」。",
            )
            return
        api_key = self.get_api_key()
        if not api_key:
            messagebox.showerror("错误", "请先在凭证面板填写 MiniMax API Key")
            return
        DesignVoiceDialog(self, api_key=api_key, on_success=self._refresh_list)

    def _open_local_record_dialog(self):
        """本地 VoxCPM 录制新音色：录音 → 试听 → 保存到 voice_library（无需 API）。"""
        if self.get_engine() != "voxcpm":
            messagebox.showinfo(
                "提示",
                "「本地录制」是给本地 VoxCPM 引擎用的。\n"
                "MiniMax 引擎请用「克隆」上传到云端。",
            )
            return
        LocalRecordDialog(self, on_success=self._refresh_list)

    def _open_import_dialog(self):
        """本地 VoxCPM 导入已有音频文件作为参考音色。"""
        if self.get_engine() != "voxcpm":
            messagebox.showinfo(
                "提示",
                "「导入音频」是给本地 VoxCPM 引擎用的。\n"
                "MiniMax 引擎请用「克隆」上传到云端。",
            )
            return
        ImportAudioDialog(self, on_success=self._refresh_list)

    def _update_button_visibility(self):
        """按当前引擎显隐不同按钮组。"""
        engine = self.get_engine()
        # MiniMax 专用按钮：仅在 minimax 下显示
        if engine == "minimax":
            self.clone_btn.grid()
            self.design_btn.grid()
            self.local_record_btn.grid_remove()
            self.import_btn.grid_remove()
        elif engine == "voxcpm":
            self.clone_btn.grid_remove()
            self.design_btn.grid_remove()
            self.local_record_btn.grid()
            self.import_btn.grid()
        else:
            # 豆包 / 其他：全部隐藏
            self.clone_btn.grid_remove()
            self.design_btn.grid_remove()
            self.local_record_btn.grid_remove()
            self.import_btn.grid_remove()

    def _play_voice(self, voice_id):
        text = simpledialog.askstring(
            "试听", "输入测试文本：", initialvalue="这是一个测试",
            parent=self.winfo_toplevel(),
        )
        if not text:
            return
        engine = self.get_engine()
        try:
            if engine == "voxcpm":
                mp3_bytes = vc.synthesize_test_voxcpm(voice_id, text)
            else:
                api_key = self.get_api_key()
                if not api_key:
                    messagebox.showerror("错误", "请先填写 MiniMax API Key")
                    return
                mp3_bytes = vc.synthesize_test(voice_id, text, api_key)
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.write(mp3_bytes)
            tmp.close()
            subprocess.Popen(["afplay", tmp.name])
        except Exception as e:
            messagebox.showerror("试听失败", str(e))

    def _delete_voice(self, voice_id):
        if not messagebox.askyesno("确认", f"删除音色 {voice_id}？"):
            return
        try:
            vl.delete(voice_id)
            self._refresh_list()
        except Exception as e:
            messagebox.showerror("删除失败", str(e))

    def _clean_voice(self, voice_id):
        """对单个 voice 重新执行标准化清洗（降噪 + 响度归一化）。

        用于：用户已用旧版 GUI 录入的 voice 没经过清洗，
        或采集环境变化后想重新清洗。后台线程跑，不阻塞 UI。
        """
        rec = vl.get(voice_id)
        if not rec:
            messagebox.showerror("错误", f"音色不存在: {voice_id}")
            return
        target_wav = rec.get("qwen3_wav_path") or rec.get("wav_path")
        if not target_wav or not os.path.exists(target_wav):
            messagebox.showerror(
                "无 wav_path",
                f"voice_id `{voice_id}` 没有可用的 wav 文件，无法清洗",
            )
            return

        cleaned_path = target_wav.replace(".wav", ".cleaned.wav")

        def _do():
            try:
                result = audio_cleanup.clean_wav_for_cloning(target_wav, cleaned_path)
                size_kb = os.path.getsize(cleaned_path) // 1024
                self.after(
                    0, lambda: messagebox.showinfo(
                        "✅ 清洗完成",
                        f"voice_id: {voice_id}\n"
                        f"输入 RMS: {result['input_rms']:.1f} dB\n"
                        f"输出 RMS: {result['output_rms']:.1f} dB\n"
                        f"实测底噪: {result['noise_floor_db']:.1f} dB\n"
                        f"输出文件: {size_kb} KB",
                    )
                )
            except Exception as e:
                self.after(
                    0, lambda: messagebox.showerror("❌ 清洗失败", str(e))
                )

        threading.Thread(target=_do, daemon=True).start()

    def _edit_voice(self, voice_id):
        """Open edit dialog for a library voice (rename display_name, edit notes)."""
        record = vl.get(voice_id)
        if not record:
            messagebox.showerror("错误", f"音色不存在: {voice_id}")
            return

        dialog = ctk.CTkToplevel(self.winfo_toplevel())
        dialog.title(f"编辑音色 — {voice_id}")
        dialog.geometry("420x320")
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="显示名称：", text_color=GHIBLI["text"]).pack(anchor="w", padx=12, pady=(12, 4))
        name_var = ctk.StringVar(value=record["display_name"])
        ctk.CTkEntry(
            dialog, textvariable=name_var,
            fg_color=GHIBLI["cream_dark"], text_color=GHIBLI["text"],
            border_color=GHIBLI["dust"], border_width=1,
            font=ctk.CTkFont("Helvetica Neue", 13),
        ).pack(fill="x", padx=12, pady=(0, 8))

        ctk.CTkLabel(dialog, text="备注：", text_color=GHIBLI["text"]).pack(anchor="w", padx=12, pady=(8, 4))
        notes_text = ctk.CTkTextbox(
            dialog, height=120,
            fg_color=GHIBLI["cream_dark"], text_color=GHIBLI["text"],
            border_color=GHIBLI["dust"], border_width=1,
            font=ctk.CTkFont("Helvetica Neue", 12),
        )
        notes_text.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        notes_text.insert("0.0", record.get("notes", ""))

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 12))
        btn_row.grid_columnconfigure(0, weight=1)
        btn_row.grid_columnconfigure(1, weight=1)

        def save():
            new_name = name_var.get().strip()
            new_notes = notes_text.get("0.0", "end").strip()
            if not new_name:
                messagebox.showerror("错误", "显示名称不能为空")
                return
            try:
                vl.update(voice_id, display_name=new_name, notes=new_notes)
            except Exception as e:
                messagebox.showerror("更新失败", str(e))
                return
            self._refresh_list()
            dialog.destroy()

        ctk.CTkButton(
            btn_row, text="保存",
            fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
            text_color=GHIBLI["white"], command=save,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))

        ctk.CTkButton(
            btn_row, text="取消",
            fg_color=GHIBLI["dust"], hover_color=GHIBLI["text_light"],
            text_color=GHIBLI["white"], command=dialog.destroy,
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

    def set_enabled(self, enabled):
        """Engine-aware gray-out. Spec: keep panel visible, disable buttons + tooltip."""
        state = "normal" if enabled else "disabled"

        def _apply(widget):
            try:
                if widget.winfo_class() in ("CTkButton", "CTkOptionMenu"):
                    widget.configure(state=state)
            except Exception:
                pass
            for child in widget.winfo_children():
                _apply(child)

        _apply(self.clone_btn)
        _apply(self.design_btn)
        _apply(self.local_record_btn)
        _apply(self.import_btn)
        _apply(self.refresh_btn)
        _apply(self.list_frame)

        # 按当前引擎切换按钮组
        self._update_button_visibility()

        if not enabled:
            self.clone_btn.bind("<Enter>", lambda e: self._show_tooltip(LIBRARY_GRAYOUT_TOOLTIP))
            self.clone_btn.bind("<Leave>", lambda e: self._hide_tooltip())
        else:
            self.clone_btn.unbind("<Enter>")
            self.clone_btn.unbind("<Leave>")
            self._hide_tooltip()

    def _show_tooltip(self, text):
        if hasattr(self, "_tooltip") and self._tooltip:
            return
        x = self.clone_btn.winfo_rootx() + 20
        y = self.clone_btn.winfo_rooty() + self.clone_btn.winfo_height() + 4
        self._tooltip = tk.Toplevel(self.winfo_toplevel())
        self._tooltip.wm_overrideredirect(True)
        self._tooltip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._tooltip, text=text, background="#FFFBE6",
            relief="solid", borderwidth=1, padx=6, pady=3,
            font=("Helvetica Neue", 10), foreground=GHIBLI["text"],
        ).pack()

    def _hide_tooltip(self):
        if hasattr(self, "_tooltip") and self._tooltip:
            self._tooltip.destroy()
            self._tooltip = None


class _VoiceRow(ctk.CTkFrame):
    def __init__(self, parent, voice, on_play, on_edit, on_delete, on_pick=None, on_clean=None):
        super().__init__(parent, fg_color=GHIBLI["white"], corner_radius=4)
        self._voice = voice
        self._on_play = on_play
        self._on_edit_cb = on_edit
        self._on_delete = on_delete
        self._on_pick = on_pick
        self._on_clean = on_clean
        self._selected = False
        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self, text=f"🎤 {voice['display_name']}",
            font=ctk.CTkFont("Helvetica Neue", 11, "bold"),
            text_color=GHIBLI["text"],
        ).grid(row=0, column=0, padx=(8, 4), pady=4)

        ctk.CTkLabel(
            self, text=f"({voice['voice_id']})",
            font=ctk.CTkFont("Helvetica Neue", 10),
            text_color=GHIBLI["text_light"],
        ).grid(row=0, column=1, sticky="w", padx=(0, 4), pady=4)

        self.pick_btn = ctk.CTkButton(
            self, text="✓ 选用", width=64, height=24,
            fg_color=GHIBLI["totoro"], hover_color=GHIBLI["totoro_dark"],
            text_color=GHIBLI["white"], corner_radius=4,
            command=lambda: self._on_pick_clicked(voice["voice_id"]),
        )
        self.pick_btn.grid(row=0, column=2, padx=2, pady=4)

        ctk.CTkButton(
            self, text="▶", width=32, height=24,
            fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
            text_color=GHIBLI["white"], corner_radius=4,
            command=lambda: self._on_play(voice["voice_id"]),
        ).grid(row=0, column=3, padx=2, pady=4)

        ctk.CTkButton(
            self, text="✏️", width=32, height=24,
            fg_color=GHIBLI["dust"], hover_color=GHIBLI["text_light"],
            text_color=GHIBLI["white"], corner_radius=4,
            command=lambda: self._on_edit_clicked(voice["voice_id"]),
        ).grid(row=0, column=4, padx=2, pady=4)

        # 🧹 重新清洗按钮（仅 VoxCPM 引擎下启用）
        self.clean_btn = ctk.CTkButton(
            self, text="🧹", width=32, height=24,
            fg_color=GHIBLI["cream_dark"], hover_color=GHIBLI["dust"],
            text_color=GHIBLI["text"], corner_radius=4,
            command=lambda: self._on_clean_clicked(voice["voice_id"]),
        )
        self.clean_btn.grid(row=0, column=5, padx=2, pady=4)

        ctk.CTkButton(
            self, text="🗑", width=32, height=24,
            fg_color=GHIBLI["dust"], hover_color=GHIBLI["text_light"],
            text_color=GHIBLI["white"], corner_radius=4,
            command=lambda: self._on_delete(voice["voice_id"]),
        ).grid(row=0, column=6, padx=(2, 8), pady=4)

    def _on_edit_clicked(self, voice_id):
        self._on_edit_cb(voice_id)

    def _on_clean_clicked(self, voice_id):
        if self._on_clean:
            self._on_clean(voice_id)

    def _on_pick_clicked(self, voice_id):
        if self._on_pick:
            self._on_pick(voice_id)

    def set_selected(self, selected):
        """视觉标记选中状态（高亮整行 + 切换按钮文字）。"""
        self._selected = selected
        if selected:
            self.configure(fg_color=GHIBLI["totoro"], border_width=2, border_color=GHIBLI["forest"])
            self.pick_btn.configure(text="✓ 已选", fg_color=GHIBLI["grass"])
        else:
            self.configure(fg_color=GHIBLI["white"], border_width=0)
            self.pick_btn.configure(text="✓ 选用", fg_color=GHIBLI["totoro"])


class CloneDialog(ctk.CTkToplevel):
    """Single-panel recording → preview → save flow with countdown."""

    def __init__(self, parent, api_key, on_success):
        super().__init__(parent.winfo_toplevel())
        self.api_key = api_key
        self.on_success = on_success
        self._recording = False
        self._wav_path = None
        self._countdown_job = None
        self._duration = DEFAULT_DURATION
        self.title("克隆新音色")
        self.geometry("560x620")
        self.grab_set()
        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text="🎙️ 录制一段声音（10-60 秒）",
            font=ctk.CTkFont("Helvetica Neue", 14, "bold"),
            text_color=GHIBLI["text"],
        ).grid(row=0, column=0, padx=12, pady=(12, 4), sticky="w")

        # Duration selector
        dur_frame = ctk.CTkFrame(self, fg_color="transparent")
        dur_frame.grid(row=1, column=0, padx=12, pady=(0, 4), sticky="ew")
        ctk.CTkLabel(dur_frame, text="时长：", text_color=GHIBLI["text"]).pack(side="left")
        self.duration_var = ctk.IntVar(value=DEFAULT_DURATION)
        ctk.CTkOptionMenu(
            dur_frame, variable=self.duration_var, values=[str(d) for d in DURATION_OPTIONS],
            fg_color=GHIBLI["cream_dark"], button_color=GHIBLI["grass"],
            text_color=GHIBLI["text"], width=80,
        ).pack(side="left", padx=(4, 0))

        # Sample text selector
        ctk.CTkLabel(self, text="示例文本：", text_color=GHIBLI["text"]).grid(
            row=2, column=0, padx=12, pady=(8, 2), sticky="w")
        self.sample_var = ctk.StringVar(value="通用")
        ctk.CTkOptionMenu(
            self, variable=self.sample_var, values=list(SAMPLE_TEXTS.keys()),
            fg_color=GHIBLI["cream_dark"], button_color=GHIBLI["grass"],
            text_color=GHIBLI["text"],
        ).grid(row=3, column=0, padx=12, pady=(0, 4), sticky="ew")

        self.sample_text = ctk.CTkTextbox(
            self, height=110,
            fg_color=GHIBLI["cream_dark"],
            text_color=GHIBLI["text"],
            font=ctk.CTkFont("Helvetica Neue", 13),
            border_color=GHIBLI["dust"], border_width=1,
            wrap="word",
        )
        self.sample_text.insert("0.0", SAMPLE_TEXTS["通用"])
        self.sample_text.grid(row=4, column=0, padx=12, pady=(0, 8), sticky="ew")
        self.sample_var.trace_add("write", lambda *_: self._on_sample_change())

        # Countdown display + progress bar
        self.countdown_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            self, textvariable=self.countdown_var,
            font=ctk.CTkFont("Helvetica Neue", 28, "bold"),
            text_color=GHIBLI["totoro"],
        ).grid(row=5, column=0, padx=12, pady=(4, 2))

        self.progress = ctk.CTkProgressBar(self, fg_color=GHIBLI["cream"], progress_color=GHIBLI["grass"])
        self.progress.set(0)
        self.progress.grid(row=6, column=0, padx=12, pady=(0, 8), sticky="ew")

        # Record / preview buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=7, column=0, padx=12, pady=(0, 4), sticky="ew")
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)

        self.rec_btn = ctk.CTkButton(
            btn_frame, text="⏺ 开始录制",
            fg_color=GHIBLI["totoro"], hover_color=GHIBLI["totoro_dark"],
            text_color=GHIBLI["white"], command=self._start_record,
        )
        self.rec_btn.grid(row=0, column=0, sticky="ew", padx=(0, 2))

        self.preview_btn = ctk.CTkButton(
            btn_frame, text="▶ 试听录制",
            fg_color=GHIBLI["cream"], hover_color=GHIBLI["dust"],
            text_color=GHIBLI["text"], command=self._preview_record,
            state="disabled",
        )
        self.preview_btn.grid(row=0, column=1, sticky="ew", padx=(2, 0))

        # Naming (two fields: display_name + voice_id, both editable)
        ctk.CTkLabel(self, text="显示名称（中文）：", text_color=GHIBLI["text"]).grid(
            row=8, column=0, padx=12, pady=(8, 2), sticky="w")
        self.name_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            self, textvariable=self.name_var,
            placeholder_text="例如：张医生的声音",
            fg_color=GHIBLI["cream_dark"],
            text_color=GHIBLI["text"],
            border_color=GHIBLI["dust"], border_width=1,
            font=ctk.CTkFont("Helvetica Neue", 13),
        ).grid(row=9, column=0, padx=12, pady=(0, 4), sticky="ew")

        ctk.CTkLabel(self, text="voice_id（ASCII，作为 API 标识）：", text_color=GHIBLI["text"]).grid(
            row=10, column=0, padx=12, pady=(4, 2), sticky="w")
        self.voice_id_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            self, textvariable=self.voice_id_var,
            placeholder_text="例如：dr_zhang_v1",
            fg_color=GHIBLI["cream_dark"],
            text_color=GHIBLI["text"],
            border_color=GHIBLI["dust"], border_width=1,
            font=ctk.CTkFont("Helvetica Neue", 13),
        ).grid(row=11, column=0, padx=12, pady=(0, 4), sticky="ew")

        # Save button
        self.save_btn = ctk.CTkButton(
            self, text="💾 保存并上传",
            fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
            text_color=GHIBLI["white"], command=self._save,
        )
        self.save_btn.grid(row=12, column=0, padx=12, pady=(8, 4), sticky="ew")

        # Status label
        self.status_var = ctk.StringVar(value="准备就绪")
        ctk.CTkLabel(
            self, textvariable=self.status_var,
            text_color=GHIBLI["text_light"],
            font=ctk.CTkFont("Helvetica Neue", 11),
        ).grid(row=13, column=0, padx=12, pady=(0, 12), sticky="w")

    def _on_sample_change(self):
        key = self.sample_var.get()
        self.sample_text.delete("0.0", "end")
        self.sample_text.insert("0.0", SAMPLE_TEXTS.get(key, ""))

    def _start_record(self):
        if self._recording:
            return
        self._duration = self.duration_var.get()
        self._wav_path = None
        self.preview_btn.configure(state="disabled")
        self.rec_btn.configure(state="disabled", text="录制中...")
        self._recording = True
        self.progress.set(0)
        self._start_countdown()
        threading.Thread(target=self._do_record, daemon=True).start()

    def _start_countdown(self):
        """Tick once per second; updates big numeric + progress bar."""
        def tick():
            if not self._recording:
                return
            # Use elapsed-from-start approximation; recorded file's actual duration is decided in worker
            elapsed = getattr(self, "_elapsed", 0)
            remaining = max(0, self._duration - elapsed)
            self.countdown_var.set(f"{remaining}s")
            self.progress.set(elapsed / self._duration)
            self._elapsed = elapsed + 1
            self._countdown_job = self.after(1000, tick)

        self._elapsed = 0
        tick()

    def _stop_countdown(self):
        if self._countdown_job:
            self.after_cancel(self._countdown_job)
            self._countdown_job = None
        self.countdown_var.set("")
        self.progress.set(1.0)

    def _do_record(self):
        try:
            wav_path = vr.record(seconds=self._duration)
            self._wav_path = wav_path
            self.after(0, lambda: self.status_var.set("✅ 录制完成，请先试听再保存"))
            self.after(0, lambda: self.rec_btn.configure(state="normal", text="⏺ 重新录制"))
            self.after(0, lambda: self.preview_btn.configure(state="normal"))
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self.status_var.set(f"❌ 录音失败: {err}"))
            self.after(0, lambda: self.rec_btn.configure(state="normal", text="⏺ 重试"))
        finally:
            self._recording = False
            self.after(0, self._stop_countdown)

    def _preview_record(self):
        """Spec: preview is REQUIRED before save."""
        if not self._wav_path:
            messagebox.showerror("未录制", "请先录音")
            return
        try:
            subprocess.Popen(["afplay", self._wav_path])
        except Exception as e:
            messagebox.showerror("试听失败", str(e))

    def _save(self):
        if not self._wav_path:
            messagebox.showerror("未录制", "请先录音")
            return
        name = self.name_var.get().strip()
        voice_id = self.voice_id_var.get().strip()
        if not name:
            messagebox.showerror("未命名", "请填写显示名称")
            return
        if not VOICE_ID_PATTERN.match(voice_id):
            messagebox.showerror(
                "voice_id 不合法",
                "voice_id 必须 8-256 字符，以字母开头，仅含 a-z A-Z 0-9 _ -",
            )
            return
        # Spec: preview required before save
        if not messagebox.askyesno("确认", "已经试听过了吗？没试听的话先点「▶ 试听录制」。继续保存？"):
            return
        existing = vl.exists(voice_id)
        if existing:
            choice = messagebox.askyesnocancel(
                "voice_id 已存在",
                f"voice_id `{voice_id}` 已在库中。\n"
                f"是=先克隆新音色，成功后再覆盖旧的（安全）\n"
                f"否=返回并改名\n"
                f"取消=放弃",
            )
            if not choice:  # No or Cancel
                return
            # Yes: continue with safe-overwrite flow (clone first, then swap)

        self.save_btn.configure(state="disabled", text="上传中...")
        self.status_var.set("⏫ 上传到 MiniMax...")
        threading.Thread(
            target=self._do_save,
            args=(voice_id, name, existing),
            daemon=True,
        ).start()

    def _do_save(self, voice_id, name, existing_was_there):
        """Safe-overwrite: clone first, only delete old on success."""
        try:
            vc.clone(self._wav_path, voice_id, self.api_key)
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self.status_var.set(f"❌ 克隆失败: {err}"))
            self.after(0, lambda: self.save_btn.configure(state="normal", text="💾 重试"))
            return
        try:
            if existing_was_there:
                vl.delete(voice_id)
            vl.add(voice_id, name, self._wav_path)
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self.status_var.set(f"❌ 写库失败: {err}"))
            self.after(0, lambda: self.save_btn.configure(state="normal", text="💾 重试"))
            return
        self.after(0, lambda: self.status_var.set("✅ 克隆成功"))
        self.after(0, lambda: self.on_success())
        self.after(500, self.destroy)


class DesignVoiceDialog(ctk.CTkToplevel):
    """Voice Design dialog — generate a custom voice from a text description.

    User flow:
      1. Type a description (e.g., "40岁男医生，语速慢，沉稳专业")
      2. Pick gender / language
      3. Provide voice_id (ASCII) + display name (Chinese OK)
      4. Click 生成 — calls MiniMax /v1/voice_design, auto-adds to library
    """

    PLACEHOLDER_PROMPT = "例如：40岁男医生，语速慢，沉稳专业"

    def __init__(self, parent, api_key, on_success):
        super().__init__(parent.winfo_toplevel())
        self.api_key = api_key
        self.on_success = on_success
        self.title("设计新音色")
        self.geometry("520x540")
        self.grab_set()
        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text="🎨 用文字描述生成音色",
            font=ctk.CTkFont("Helvetica Neue", 14, "bold"),
            text_color=GHIBLI["text"],
        ).grid(row=0, column=0, padx=12, pady=(12, 4), sticky="w")

        # Prompt textarea
        ctk.CTkLabel(
            self, text="音色描述（必填）：", text_color=GHIBLI["text"],
        ).grid(row=1, column=0, padx=12, pady=(8, 2), sticky="w")
        self.prompt_text = ctk.CTkTextbox(
            self, height=80,
            fg_color=GHIBLI["cream_dark"], text_color=GHIBLI["text"],
            border_color=GHIBLI["dust"], border_width=1,
            font=ctk.CTkFont("Helvetica Neue", 12),
        )
        self.prompt_text.grid(row=2, column=0, padx=12, pady=(0, 4), sticky="ew")
        self.prompt_text.insert("0.0", self.PLACEHOLDER_PROMPT)

        # Gender + language in one row
        opts_frame = ctk.CTkFrame(self, fg_color="transparent")
        opts_frame.grid(row=3, column=0, padx=12, pady=(4, 4), sticky="ew")
        opts_frame.grid_columnconfigure(1, weight=1)
        opts_frame.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(opts_frame, text="性别：", text_color=GHIBLI["text"]).grid(
            row=0, column=0, padx=(0, 4), sticky="w")
        self.gender_var = ctk.StringVar(value="male")
        ctk.CTkOptionMenu(
            opts_frame, variable=self.gender_var,
            values=["male", "female", "neutral"],
            fg_color=GHIBLI["cream_dark"], button_color=GHIBLI["grass"],
            text_color=GHIBLI["text"], width=120,
        ).grid(row=0, column=1, sticky="w", padx=(0, 12))

        ctk.CTkLabel(opts_frame, text="语言：", text_color=GHIBLI["text"]).grid(
            row=0, column=2, padx=(0, 4), sticky="w")
        self.lang_var = ctk.StringVar(value="Chinese")
        ctk.CTkOptionMenu(
            opts_frame, variable=self.lang_var,
            values=["Chinese", "Chinese,Yue", "English", "Japanese", "Korean"],
            fg_color=GHIBLI["cream_dark"], button_color=GHIBLI["grass"],
            text_color=GHIBLI["text"], width=120,
        ).grid(row=0, column=3, sticky="w")

        # Preview text (synthesized in the preview audio)
        ctk.CTkLabel(self, text="预览文本：", text_color=GHIBLI["text"]).grid(
            row=4, column=0, padx=12, pady=(8, 2), sticky="w")
        self.preview_text = ctk.CTkEntry(
            self, placeholder_text="例如：你好，我是张医生。",
            fg_color=GHIBLI["cream_dark"], text_color=GHIBLI["text"],
            border_color=GHIBLI["dust"], border_width=1,
            font=ctk.CTkFont("Helvetica Neue", 12),
        )
        self.preview_text.grid(row=5, column=0, padx=12, pady=(0, 4), sticky="ew")
        self.preview_text.insert(0, "你好，这是音色预览。")

        # voice_id (ASCII)
        ctk.CTkLabel(self, text="voice_id（必填，ASCII）：", text_color=GHIBLI["text"]).grid(
            row=6, column=0, padx=12, pady=(8, 2), sticky="w")
        self.vid_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            self, textvariable=self.vid_var,
            placeholder_text="例如：dr_zhang_v1",
            fg_color=GHIBLI["cream_dark"], text_color=GHIBLI["text"],
            border_color=GHIBLI["dust"], border_width=1,
            font=ctk.CTkFont("Helvetica Neue", 12),
        ).grid(row=7, column=0, padx=12, pady=(0, 4), sticky="ew")

        # Display name (Chinese OK)
        ctk.CTkLabel(self, text="显示名称（必填）：", text_color=GHIBLI["text"]).grid(
            row=8, column=0, padx=12, pady=(4, 2), sticky="w")
        self.name_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            self, textvariable=self.name_var,
            placeholder_text="例如：医生的稳重声",
            fg_color=GHIBLI["cream_dark"], text_color=GHIBLI["text"],
            border_color=GHIBLI["dust"], border_width=1,
            font=ctk.CTkFont("Helvetica Neue", 12),
        ).grid(row=9, column=0, padx=12, pady=(0, 8), sticky="ew")

        # Status
        self.status_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            self, textvariable=self.status_var,
            text_color=GHIBLI["text_light"],
            font=ctk.CTkFont("Helvetica Neue", 11),
        ).grid(row=10, column=0, padx=12, pady=(0, 4), sticky="w")

        # Buttons
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=11, column=0, padx=12, pady=(4, 12), sticky="ew")
        btn_row.grid_columnconfigure(0, weight=1)
        btn_row.grid_columnconfigure(1, weight=1)

        self.submit_btn = ctk.CTkButton(
            btn_row, text="🎨 生成",
            fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
            text_color=GHIBLI["white"], command=self._do_design,
        )
        self.submit_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        ctk.CTkButton(
            btn_row, text="取消",
            fg_color=GHIBLI["dust"], hover_color=GHIBLI["text_light"],
            text_color=GHIBLI["white"], command=self.destroy,
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

    def _do_design(self):
        prompt = self.prompt_text.get("0.0", "end").strip()
        if prompt == self.PLACEHOLDER_PROMPT:
            prompt = ""
        voice_id = self.vid_var.get().strip()
        display_name = self.name_var.get().strip()
        preview_text = self.preview_text.get().strip() or "你好"

        if not prompt:
            messagebox.showerror("错误", "请填写音色描述")
            return
        if not VOICE_ID_PATTERN.match(voice_id):
            messagebox.showerror(
                "voice_id 不合法",
                "voice_id 必须 8-256 字符，以字母开头，仅含 a-z A-Z 0-9 _ -",
            )
            return
        if not display_name:
            messagebox.showerror("错误", "请填写显示名称")
            return
        if vl.exists(voice_id):
            messagebox.showerror("错误", f"voice_id 已存在: {voice_id}")
            return

        self.submit_btn.configure(state="disabled", text="生成中...")
        self.status_var.set("🎨 正在生成音色...")
        threading.Thread(
            target=self._do_design_thread,
            args=(prompt, voice_id, display_name, preview_text),
            daemon=True,
        ).start()

    def _do_design_thread(self, prompt, voice_id, display_name, preview_text):
        try:
            import doubao_voice_design as dvd
            result = dvd.design_voice(
                prompt=prompt,
                api_key=self.api_key,
                gender=self.gender_var.get(),
                language=self.lang_var.get(),
                preview_text=preview_text,
                voice_id=voice_id,
            )
            notes = f"Voice Design: {prompt[:40]}"
            vl.add_designed(
                voice_id=voice_id,
                display_name=display_name,
                preview_audio=result["preview_audio"],
                notes=notes,
            )
            self.after(0, lambda: self.status_var.set(f"✅ 设计成功: {voice_id}"))
            self.after(0, self.on_success)
            self.after(700, self.destroy)
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self.status_var.set(f"❌ 失败: {err}"))
            self.after(0, lambda: self.submit_btn.configure(state="normal", text="🎨 重试"))


class LocalRecordDialog(ctk.CTkToplevel):
    """本地 VoxCPM2 录制新音色：录音 → 试听 → 保存到 voice_library。

    与 MiniMax CloneDialog 的关键区别：
      - 不调用任何云端 API（无 api_key 依赖）
      - 保存流程：直接把录制文件交给 voice_library.add()（会拷贝到 AUDIO_DIR）
      - ref_text 可选留空（VoxCPM2 不需要 ref_text，只用 ref_audio）
    """

    def __init__(self, parent, on_success):
        super().__init__(parent.winfo_toplevel())
        self.on_success = on_success
        self._recording = False
        self._wav_path = None
        self._countdown_job = None
        self._duration = DEFAULT_DURATION
        self.title("本地录制音色（VoxCPM）")
        self.geometry("560x620")
        self.grab_set()
        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text="🎙️ 本地录制音色（10-60 秒）",
            font=ctk.CTkFont("Helvetica Neue", 14, "bold"),
            text_color=GHIBLI["text"],
        ).grid(row=0, column=0, padx=12, pady=(12, 4), sticky="w")

        ctk.CTkLabel(
            self, text="💡 VoxCPM2 是零样本克隆，10-30 秒单人独白效果最好。",
            font=ctk.CTkFont("Helvetica Neue", 11),
            text_color=GHIBLI["text_light"],
        ).grid(row=1, column=0, padx=12, pady=(0, 4), sticky="w")

        # Duration selector
        dur_frame = ctk.CTkFrame(self, fg_color="transparent")
        dur_frame.grid(row=2, column=0, padx=12, pady=(0, 4), sticky="ew")
        ctk.CTkLabel(dur_frame, text="时长：", text_color=GHIBLI["text"]).pack(side="left")
        self.duration_var = ctk.IntVar(value=DEFAULT_DURATION)
        ctk.CTkOptionMenu(
            dur_frame, variable=self.duration_var, values=[str(d) for d in DURATION_OPTIONS],
            fg_color=GHIBLI["cream_dark"], button_color=GHIBLI["grass"],
            text_color=GHIBLI["text"], width=80,
        ).pack(side="left", padx=(4, 0))

        # Sample text selector + textarea
        ctk.CTkLabel(self, text="示例文本：", text_color=GHIBLI["text"]).grid(
            row=3, column=0, padx=12, pady=(8, 2), sticky="w")
        self.sample_var = ctk.StringVar(value="通用")
        ctk.CTkOptionMenu(
            self, variable=self.sample_var, values=list(SAMPLE_TEXTS.keys()),
            fg_color=GHIBLI["cream_dark"], button_color=GHIBLI["grass"],
            text_color=GHIBLI["text"],
        ).grid(row=4, column=0, padx=12, pady=(0, 4), sticky="ew")

        self.sample_text = ctk.CTkTextbox(
            self, height=110,
            fg_color=GHIBLI["cream_dark"], text_color=GHIBLI["text"],
            font=ctk.CTkFont("Helvetica Neue", 13),
            border_color=GHIBLI["dust"], border_width=1,
            wrap="word",
        )
        self.sample_text.insert("0.0", SAMPLE_TEXTS["通用"])
        self.sample_text.grid(row=5, column=0, padx=12, pady=(0, 8), sticky="ew")
        self.sample_var.trace_add("write", lambda *_: self._on_sample_change())

        # Countdown + progress
        self.countdown_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            self, textvariable=self.countdown_var,
            font=ctk.CTkFont("Helvetica Neue", 28, "bold"),
            text_color=GHIBLI["totoro"],
        ).grid(row=6, column=0, padx=12, pady=(4, 2))

        self.progress = ctk.CTkProgressBar(self, fg_color=GHIBLI["cream"], progress_color=GHIBLI["grass"])
        self.progress.set(0)
        self.progress.grid(row=7, column=0, padx=12, pady=(0, 8), sticky="ew")

        # Record / preview
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=8, column=0, padx=12, pady=(0, 4), sticky="ew")
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)

        self.rec_btn = ctk.CTkButton(
            btn_frame, text="⏺ 开始录制",
            fg_color=GHIBLI["totoro"], hover_color=GHIBLI["totoro_dark"],
            text_color=GHIBLI["white"], command=self._start_record,
        )
        self.rec_btn.grid(row=0, column=0, sticky="ew", padx=(0, 2))

        self.preview_btn = ctk.CTkButton(
            btn_frame, text="▶ 试听录制",
            fg_color=GHIBLI["cream"], hover_color=GHIBLI["dust"],
            text_color=GHIBLI["text"], command=self._preview_record,
            state="disabled",
        )
        self.preview_btn.grid(row=0, column=1, sticky="ew", padx=(2, 0))

        # Naming
        ctk.CTkLabel(self, text="显示名称（中文）：", text_color=GHIBLI["text"]).grid(
            row=9, column=0, padx=12, pady=(8, 2), sticky="w")
        self.name_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            self, textvariable=self.name_var,
            placeholder_text="例如：我的备用音色",
            fg_color=GHIBLI["cream_dark"], text_color=GHIBLI["text"],
            border_color=GHIBLI["dust"], border_width=1,
            font=ctk.CTkFont("Helvetica Neue", 13),
        ).grid(row=10, column=0, padx=12, pady=(0, 4), sticky="ew")

        ctk.CTkLabel(self, text="voice_id（ASCII，作为文件标识）：", text_color=GHIBLI["text"]).grid(
            row=11, column=0, padx=12, pady=(4, 2), sticky="w")
        self.voice_id_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            self, textvariable=self.voice_id_var,
            placeholder_text="例如：my_voice_local_v1",
            fg_color=GHIBLI["cream_dark"], text_color=GHIBLI["text"],
            border_color=GHIBLI["dust"], border_width=1,
            font=ctk.CTkFont("Helvetica Neue", 13),
        ).grid(row=12, column=0, padx=12, pady=(0, 4), sticky="ew")

        # Save button
        self.save_btn = ctk.CTkButton(
            self, text="💾 保存到本地音色库",
            fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
            text_color=GHIBLI["white"], command=self._save,
        )
        self.save_btn.grid(row=13, column=0, padx=12, pady=(8, 4), sticky="ew")

        self.status_var = ctk.StringVar(value="准备就绪")
        ctk.CTkLabel(
            self, textvariable=self.status_var,
            text_color=GHIBLI["text_light"],
            font=ctk.CTkFont("Helvetica Neue", 11),
        ).grid(row=14, column=0, padx=12, pady=(0, 12), sticky="w")

    def _on_sample_change(self):
        key = self.sample_var.get()
        self.sample_text.delete("0.0", "end")
        self.sample_text.insert("0.0", SAMPLE_TEXTS.get(key, ""))

    def _start_record(self):
        if self._recording:
            return
        self._duration = self.duration_var.get()
        self._wav_path = None
        self.preview_btn.configure(state="disabled")
        self.rec_btn.configure(state="disabled", text="录制中...")
        self._recording = True
        self.progress.set(0)
        self._start_countdown()
        threading.Thread(target=self._do_record, daemon=True).start()

    def _start_countdown(self):
        def tick():
            if not self._recording:
                return
            elapsed = getattr(self, "_elapsed", 0)
            remaining = max(0, self._duration - elapsed)
            self.countdown_var.set(f"{remaining}s")
            self.progress.set(elapsed / self._duration)
            self._elapsed = elapsed + 1
            self._countdown_job = self.after(1000, tick)

        self._elapsed = 0
        tick()

    def _stop_countdown(self):
        if self._countdown_job:
            self.after_cancel(self._countdown_job)
            self._countdown_job = None
        self.countdown_var.set("")
        self.progress.set(1.0)

    def _do_record(self):
        try:
            wav_path = vr.record(seconds=self._duration)
            self._wav_path = wav_path
            self.after(0, lambda: self.status_var.set("✅ 录制完成，请先试听再保存"))
            self.after(0, lambda: self.rec_btn.configure(state="normal", text="⏺ 重新录制"))
            self.after(0, lambda: self.preview_btn.configure(state="normal"))
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self.status_var.set(f"❌ 录音失败: {err}"))
            self.after(0, lambda: self.rec_btn.configure(state="normal", text="⏺ 重试"))
        finally:
            self._recording = False
            self.after(0, self._stop_countdown)

    def _preview_record(self):
        if not self._wav_path:
            messagebox.showerror("未录制", "请先录音")
            return
        try:
            subprocess.Popen(["afplay", self._wav_path])
        except Exception as e:
            messagebox.showerror("试听失败", str(e))

    def _save(self):
        if not self._wav_path:
            messagebox.showerror("未录制", "请先录音")
            return
        name = self.name_var.get().strip()
        voice_id = self.voice_id_var.get().strip()
        if not name:
            messagebox.showerror("未命名", "请填写显示名称")
            return
        if not VOICE_ID_PATTERN.match(voice_id):
            messagebox.showerror(
                "voice_id 不合法",
                "voice_id 必须 8-256 字符，以字母开头，仅含 a-z A-Z 0-9 _ -",
            )
            return
        if not messagebox.askyesno("确认", "已经试听过了吗？没试听的话先点「▶ 试听录制」。继续保存？"):
            return

        self.save_btn.configure(state="disabled", text="保存中...")
        self.status_var.set("💾 写入本地音色库...")
        threading.Thread(
            target=self._do_save,
            args=(voice_id, name),
            daemon=True,
        ).start()

    def _do_save(self, voice_id, name):
        try:
            # voice_library.add() 会自动把 wav 拷到 AUDIO_DIR
            vl.add(
                voice_id=voice_id,
                display_name=name,
                wav_path=self._wav_path,
                notes=f"Local record ({self._duration}s) for VoxCPM2",
            )
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self.status_var.set(f"❌ 保存失败: {err}"))
            self.after(0, lambda: self.save_btn.configure(state="normal", text="💾 重试"))
            return

        # 自动标准化清洗：降噪 + 响度归一化，生成 *.cleaned.wav
        # 后续 VoxCPM 合成会优先用 cleaned.wav，多 voice 组合时不会出现
        # 「底噪/混响/电平不一致」问题（_resolve_wav 默认优先 cleaned）
        self.after(0, lambda: self.status_var.set("🧹 标准化清洗中..."))
        try:
            rec = vl.get(voice_id)
            target_wav = rec.get("qwen3_wav_path") or rec.get("wav_path")
            if target_wav and os.path.exists(target_wav):
                cleaned_path = target_wav.replace(".wav", ".cleaned.wav")
                audio_cleanup.clean_wav_for_cloning(target_wav, cleaned_path)
        except Exception as e:
            # 清洗失败不阻塞保存（用户仍可手动重新清洗）
            err = str(e)
            self.after(0, lambda: self.status_var.set(f"⚠️ 已保存: {voice_id}，清洗失败: {err[:60]}"))
            self.after(0, self.on_success)
            self.after(500, self.destroy)
            return

        self.after(0, lambda: self.status_var.set(f"✅ 已保存并清洗: {voice_id}"))
        self.after(0, self.on_success)
        self.after(500, self.destroy)


class ImportAudioDialog(ctk.CTkToplevel):
    """本地 VoxCPM2 导入已有音频文件作为参考音色。

    用户从磁盘选 wav / mp3 / m4a / flac 等音频文件；拷贝到 AUDIO_DIR 后
    写入 voice_library.json。
    """

    def __init__(self, parent, on_success):
        super().__init__(parent.winfo_toplevel())
        self.on_success = on_success
        self._source_path = None
        self.title("导入音频文件（VoxCPM）")
        self.geometry("520x380")
        self.grab_set()
        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text="📁 导入本地音频文件",
            font=ctk.CTkFont("Helvetica Neue", 14, "bold"),
            text_color=GHIBLI["text"],
        ).grid(row=0, column=0, padx=12, pady=(12, 4), sticky="w")

        ctk.CTkLabel(
            self, text="💡 VoxCPM2 推荐 10-30 秒单人独白片段（wav/mp3/m4a/flac）。",
            font=ctk.CTkFont("Helvetica Neue", 11),
            text_color=GHIBLI["text_light"],
        ).grid(row=1, column=0, padx=12, pady=(0, 8), sticky="w")

        # File picker
        self.file_var = ctk.StringVar(value="未选择文件")
        ctk.CTkLabel(
            self, textvariable=self.file_var,
            font=ctk.CTkFont("Helvetica Neue", 12),
            text_color=GHIBLI["text_light"], anchor="w",
        ).grid(row=2, column=0, padx=12, pady=(4, 4), sticky="ew")

        ctk.CTkButton(
            self, text="📂 选择音频文件",
            fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
            text_color=GHIBLI["white"], corner_radius=6, height=32,
            command=self._pick_file,
        ).grid(row=3, column=0, padx=12, pady=(0, 12), sticky="ew")

        # Naming
        ctk.CTkLabel(self, text="显示名称（中文）：", text_color=GHIBLI["text"]).grid(
            row=4, column=0, padx=12, pady=(4, 2), sticky="w")
        self.name_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            self, textvariable=self.name_var,
            placeholder_text="例如：导入-男声样本",
            fg_color=GHIBLI["cream_dark"], text_color=GHIBLI["text"],
            border_color=GHIBLI["dust"], border_width=1,
            font=ctk.CTkFont("Helvetica Neue", 13),
        ).grid(row=5, column=0, padx=12, pady=(0, 4), sticky="ew")

        ctk.CTkLabel(self, text="voice_id（ASCII）：", text_color=GHIBLI["text"]).grid(
            row=6, column=0, padx=12, pady=(4, 2), sticky="w")
        self.voice_id_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            self, textvariable=self.voice_id_var,
            placeholder_text="例如：imported_voice_v1",
            fg_color=GHIBLI["cream_dark"], text_color=GHIBLI["text"],
            border_color=GHIBLI["dust"], border_width=1,
            font=ctk.CTkFont("Helvetica Neue", 13),
        ).grid(row=7, column=0, padx=12, pady=(0, 4), sticky="ew")

        # Save
        self.save_btn = ctk.CTkButton(
            self, text="💾 导入并保存",
            fg_color=GHIBLI["totoro"], hover_color=GHIBLI["totoro_dark"],
            text_color=GHIBLI["white"], corner_radius=6, height=36,
            command=self._save,
        )
        self.save_btn.grid(row=8, column=0, padx=12, pady=(12, 4), sticky="ew")

        self.status_var = ctk.StringVar(value="准备就绪")
        ctk.CTkLabel(
            self, textvariable=self.status_var,
            text_color=GHIBLI["text_light"],
            font=ctk.CTkFont("Helvetica Neue", 11),
        ).grid(row=9, column=0, padx=12, pady=(0, 12), sticky="w")

    def _pick_file(self):
        import tkinter.filedialog as fd
        path = fd.askopenfilename(
            title="选择参考音频",
            filetypes=[
                ("音频文件", "*.wav *.mp3 *.m4a *.flac *.ogg *.opus"),
                ("WAV", "*.wav"),
                ("MP3", "*.mp3"),
                ("所有文件", "*"),
            ],
        )
        if not path:
            return
        self._source_path = path
        self.file_var.set(f"✅ {os.path.basename(path)}")
        # 自动建议 voice_id（基于文件名去后缀）
        if not self.voice_id_var.get().strip():
            stem = Path(path).stem
            ascii_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", stem)[:30].strip("_")
            if len(ascii_stem) < 8:
                ascii_stem = f"imp_{ascii_stem}"[:30]
            if not ascii_stem or not re.match(r"^[a-zA-Z]", ascii_stem):
                ascii_stem = "imported_voice_v1"
            self.voice_id_var.set(ascii_stem)
        if not self.name_var.get().strip():
            self.name_var.set(f"导入-{Path(path).stem}")

    def _save(self):
        if not self._source_path:
            messagebox.showerror("未选择文件", "请先选一个音频文件")
            return
        name = self.name_var.get().strip()
        voice_id = self.voice_id_var.get().strip()
        if not name:
            messagebox.showerror("未命名", "请填写显示名称")
            return
        if not VOICE_ID_PATTERN.match(voice_id):
            messagebox.showerror(
                "voice_id 不合法",
                "voice_id 必须 8-256 字符，以字母开头，仅含 a-z A-Z 0-9 _ -",
            )
            return

        self.save_btn.configure(state="disabled", text="导入中...")
        self.status_var.set("💾 复制到音色库...")
        threading.Thread(
            target=self._do_save,
            args=(voice_id, name),
            daemon=True,
        ).start()

    def _do_save(self, voice_id, name):
        try:
            vl.add(
                voice_id=voice_id,
                display_name=name,
                wav_path=self._source_path,
                notes=f"Imported from {os.path.basename(self._source_path)}",
            )
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self.status_var.set(f"❌ 导入失败: {err}"))
            self.after(0, lambda: self.save_btn.configure(state="normal", text="💾 重试"))
            return

        # 自动标准化清洗（与本地录制保持一致）：降噪 + 响度归一化
        self.after(0, lambda: self.status_var.set("🧹 标准化清洗中..."))
        try:
            rec = vl.get(voice_id)
            target_wav = rec.get("qwen3_wav_path") or rec.get("wav_path")
            if target_wav and os.path.exists(target_wav):
                cleaned_path = target_wav.replace(".wav", ".cleaned.wav")
                audio_cleanup.clean_wav_for_cloning(target_wav, cleaned_path)
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self.status_var.set(f"⚠️ 已导入: {voice_id}，清洗失败: {err[:60]}"))
            self.after(0, self.on_success)
            self.after(500, self.destroy)
            return

        self.after(0, lambda: self.status_var.set(f"✅ 已导入并清洗: {voice_id}"))
        self.after(0, self.on_success)
        self.after(500, self.destroy)


class VoicePickerModal(ctk.CTkToplevel):
    """Modal opened from VoiceConfigRow when user picks '从音色库选'."""

    def __init__(self, parent, on_pick):
        super().__init__(parent.winfo_toplevel())
        self._on_pick = on_pick
        self._result = None
        self.title("从音色库选择")
        self.geometry("420x360")
        self.grab_set()
        self._build_ui()

    def _build_ui(self):
        ctk.CTkLabel(
            self, text="📚 选择音色",
            font=ctk.CTkFont("Helvetica Neue", 14, "bold"),
            text_color=GHIBLI["text"],
        ).pack(padx=12, pady=(12, 4), anchor="w")

        list_frame = ctk.CTkScrollableFrame(self, fg_color=GHIBLI["cream"])
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        voices = vl.list_all()
        if not voices:
            ctk.CTkLabel(
                list_frame, text="（音色库为空）",
                text_color=GHIBLI["text_light"],
            ).pack(padx=10, pady=10)
            return
        for v in voices:
            row = ctk.CTkFrame(list_frame, fg_color=GHIBLI["white"], corner_radius=4)
            row.pack(fill="x", padx=4, pady=2)
            ctk.CTkLabel(
                row, text=f"🎤 {v['display_name']}  ({v['voice_id']})",
                font=ctk.CTkFont("Helvetica Neue", 11),
                text_color=GHIBLI["text"],
            ).pack(side="left", padx=8, pady=6)
            ctk.CTkButton(
                row, text="选用", width=60,
                fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
                text_color=GHIBLI["white"],
                command=lambda vid=v["voice_id"]: self._pick(vid),
            ).pack(side="right", padx=8, pady=6)

    def _pick(self, voice_id):
        self._result = voice_id
        self._on_pick(voice_id)
        self.destroy()

    def get_result(self):
        return self._result