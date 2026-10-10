#!/usr/bin/env python3
"""
输入面板 - 包含凭证、音色选择、片头片尾和内容输入区域

输入流程与豆包原版一致：
  URL 链接 / 输入文本 / 导入文件 → 点右上「🎵 生成台词」→ 脚本自动灌进
  右半边 OutputPanel.script_editor，切到「✏️ 台词编辑」tab，日志同时写入「📋 日志」tab。
"""

import asyncio
import os
import threading
from pathlib import Path

import customtkinter as ctk

from gui.theme import GHIBLI
from gui.panels.credentials import CredentialRow
from gui.panels.speakers import SpeakerRow, MiniMaxVoiceList, VoxCPMVoiceList
from gui.panels.voice_library_panel import VoiceLibraryPanel
from gui.utils_script import is_script_format
from gui.utils import run_async
from gui.panels.intro_outro import IntroOutroPanel


class InputPanel(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.grid_rowconfigure(6, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 标题
        ctk.CTkLabel(
            self, text="📄  输入内容",
            font=ctk.CTkFont("Helvetica Neue", 16, "bold"),
            text_color=GHIBLI["forest_dark"], anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 4))

        # 凭证行
        cred = CredentialRow(self, fg_color=GHIBLI["cream_dark"])
        cred.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        self.cred = cred
        cred.engine_var.trace_add("write", lambda *_: self._on_engine_change())

        # 音色库面板：始终可见，豆包引擎下灰显 + tooltip
        self.voice_library_panel = VoiceLibraryPanel(
            self,
            get_engine=self.cred.get_engine,
            get_api_key=self.cred.get_minimax_key,
            fg_color=GHIBLI["cream_dark"],
        )
        self.voice_library_panel.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))

        # 切换视图
        self.tabs = ctk.CTkTabview(
            self, fg_color=GHIBLI["cream"],
            height=380,
        )
        self.tabs.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 8))
        self.tabs.add("🌐 URL 链接")
        self.tabs.add("📝 输入文本")
        self.tabs.add("📁 导入文件")

        # 音色选择
        self.speaker_row = SpeakerRow(self, fg_color=GHIBLI["cream_dark"])
        self.speaker_row.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 8))

        # MiniMax 音色选择（默认隐藏）- 动态列表，可增删
        self.minimax_voice_list = MiniMaxVoiceList(self)
        self.minimax_voice_list.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 8))
        self.minimax_voice_list.grid_remove()  # 默认隐藏

        # VoxCPM 音色选择（默认隐藏）- 动态列表，从音色库选
        self.voxcpm_voice_list = VoxCPMVoiceList(self)
        self.voxcpm_voice_list.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 8))
        self.voxcpm_voice_list.grid_remove()  # 默认隐藏

        # 片头片尾设置（始终显示，AI生成BGM始终可用）
        self.intro_outro_row = IntroOutroPanel(self, fg_color=GHIBLI["cream_dark"])
        self.intro_outro_row.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 8))

        # 提示：一律先生成台词，审核后再生成音频
        self.hint_label = ctk.CTkLabel(
            self, text="📝 输入内容后点击右上角「🎵 生成台词」→ 审核台词 → 点击「用台词生成音频」",
            font=ctk.CTkFont("Helvetica Neue", 11),
            text_color=GHIBLI["text_light"],
        )
        self.hint_label.grid(row=6, column=0, sticky="w", padx=12, pady=(0, 8))
        url_tab = self.tabs.tab("🌐 URL 链接")
        url_tab.grid_rowconfigure(0, weight=1)
        url_tab.grid_columnconfigure(0, weight=1)
        self.url_box = ctk.CTkTextbox(
            url_tab, fg_color=GHIBLI["cream"],
            border_color=GHIBLI["grass"], border_width=1.5,
            font=ctk.CTkFont("Helvetica Neue", 13),
            text_color=GHIBLI["text"], corner_radius=8,
        )
        self.url_box.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.url_box.insert("0.0", "粘贴文档或公众号文章 URL...\n例如: https://mp.weixin.qq.com/s/xxxxx")
        self.url_box.bind("<FocusIn>", lambda e: self._on_focus_in())
        self.url_box.bind("<FocusOut>", lambda e: self._on_focus_out())
        self._url_has_placeholder = True

        # 文本面板
        txt_tab = self.tabs.tab("📝 输入文本")
        txt_tab.grid_rowconfigure(0, weight=1)
        txt_tab.grid_columnconfigure(0, weight=1)
        self.txt_box = ctk.CTkTextbox(
            txt_tab, fg_color=GHIBLI["cream"],
            border_color=GHIBLI["grass"], border_width=1.5,
            font=ctk.CTkFont("Helvetica Neue", 13),
            text_color=GHIBLI["text"], corner_radius=8,
        )
        self.txt_box.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

        # 文件面板
        file_tab = self.tabs.tab("📁 导入文件")
        file_tab.grid_rowconfigure(1, weight=1)
        file_tab.grid_columnconfigure(0, weight=1)
        self.file_var = ctk.StringVar(value="未选择文件")
        self.file_label = ctk.CTkLabel(
            file_tab, textvariable=self.file_var,
            font=ctk.CTkFont("Helvetica Neue", 12),
            text_color=GHIBLI["text_light"], anchor="w",
        )
        self.file_label.grid(row=0, column=0, sticky="w", padx=12, pady=(12, 4))
        self.file_preview = ctk.CTkTextbox(
            file_tab, fg_color=GHIBLI["cream"],
            border_color=GHIBLI["grass"], border_width=1.5,
            font=ctk.CTkFont("Helvetica Neue", 11),
            text_color=GHIBLI["text"], corner_radius=8,
        )
        self.file_preview.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.file_preview.configure(state="disabled")
        ctk.CTkButton(
            file_tab, text="📂  选择文件（PDF/Word/Markdown/TXT）",
            fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
            text_color=GHIBLI["white"], corner_radius=8, height=36,
            command=self._on_select_file,
        ).grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 4))
        # 检测到脚本格式时显示的快捷按钮（默认隐藏）
        self.file_script_action_btn = ctk.CTkButton(
            file_tab, text="🎙️  检测到脚本格式，载入到台词编辑",
            fg_color=GHIBLI["totoro"], hover_color=GHIBLI["totoro_dark"],
            text_color=GHIBLI["white"], corner_radius=8, height=32,
            command=self._on_load_file_as_script,
        )
        self.file_script_action_btn.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.file_script_action_btn.grid_remove()
        self.selected_file_path = None
        self.selected_file_text = None  # 完整文本，供「识别为脚本」按钮使用

        # 应用初始引擎状态（豆包→灰显 MiniMax→激活）— 必须在所有 widget 创建后调用
        self._on_engine_change()

    def _on_engine_change(self):
        """切换引擎：更新音色面板显隐 + 库面板灰显。

        三个引擎的音色选择 UI：
          - 豆包: speaker_row（豆包内置音色下拉）
          - MiniMax: minimax_voice_list（MiniMax 音色列表）
          - VoxCPM: voxcpm_voice_list（本地音色库动态列表 + 语速）
        """
        engine = self.cred.get_engine()
        # 默认全部隐藏
        self.speaker_row.grid_remove()
        self.minimax_voice_list.grid_remove()
        self.voxcpm_voice_list.grid_remove()
        if engine == "minimax":
            self.minimax_voice_list.grid()
        elif engine == "doubao":
            self.speaker_row.grid()
        elif engine == "voxcpm":
            self.voxcpm_voice_list.grid()
        # 音色库面板：始终可见，按引擎灰显（minimax + voxcpm 都启用库选择）
        self.voice_library_panel.set_enabled(engine in ("minimax", "voxcpm"))

    def _on_select_file(self):
        import tkinter.filedialog as fd
        path = fd.askopenfilename(
            title="选择文档",
            filetypes=[
                ("文档文件", "*.md *.markdown *.pdf *.docx *.txt"),
                ("Markdown", "*.md *.markdown"),
                ("PDF", "*.pdf"),
                ("Word", "*.docx"),
                ("纯文本", "*.txt"),
            ],
        )
        if not path:
            return
        self.selected_file_path = path
        self.file_var.set(os.path.basename(path))
        # 提取文本
        text = self._extract_text(path)
        if not text:
            return
        self.selected_file_text = text
        # 预览
        self.file_preview.configure(state="normal")
        self.file_preview.delete("0.0", "end")
        preview = text[:500] + "..." if len(text) > 500 else text
        self.file_preview.insert("0.0", preview)
        self.file_preview.configure(state="disabled")
        # 检测脚本格式：≥2 轮【角色】+正文 → 显示快捷按钮
        if is_script_format(text):
            self.file_script_action_btn.grid()
        else:
            self.file_script_action_btn.grid_remove()

    def _on_load_file_as_script(self):
        """用户已确认：把选中的文件作为脚本载入（不走豆包/LLM，直接填到 script_editor）。"""
        if not getattr(self, "selected_file_text", None):
            return
        # 解析 + 推到 OutputPanel
        from minimax_script_gen import parse_script as parse_pasted_script
        try:
            segments = parse_pasted_script(self.selected_file_text)
        except Exception as e:
            # fallback：脚本解析失败，直接当作纯文本填进 editor（让用户手动整理）
            segments = []
        self.master.right.load_segments_into_editor(segments, fallback_text=self.selected_file_text)
        # 切到 OutputPanel 的「✏️ 台词编辑」tab
        self.master.right.tabs.set("✏️ 台词编辑")

    def _extract_text(self, path):
        p = Path(path)
        try:
            if p.suffix.lower() in [".md", ".markdown"]:
                return p.read_text(encoding="utf-8")
            elif p.suffix.lower() == ".pdf":
                try:
                    import fitz
                except ImportError:
                    return "[需要安装 PyMuPDF: pip install pymupdf]"
                doc = fitz.open(path)
                return "\n".join(page.get_text() for page in doc)
            elif p.suffix.lower() == ".docx":
                try:
                    import docx
                except ImportError:
                    return "[需要安装 python-docx: pip install python-docx]"
                doc = docx.Document(path)
                return "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            return f"[读取失败: {e}]"
        return "[不支持的文件格式]"

    def _on_focus_in(self):
        if self._url_has_placeholder:
            self.url_box.delete("0.0", "end")
            self._url_has_placeholder = False

    def _on_focus_out(self):
        if not self.url_box.get("0.0", "end").strip():
            self.url_box.insert("0.0", "粘贴文档或公众号文章 URL...\n例如: https://mp.weixin.qq.com/s/xxxxx")
            self._url_has_placeholder = True
