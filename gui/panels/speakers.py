#!/usr/bin/env python3
"""
音色选择面板
"""

import customtkinter as ctk
import minimax_tts
from gui.theme import GHIBLI

# ============ 跨引擎角色标签 ============
# 中文天干 10 个，与 MiniMax 动态音色行一一对应；豆包引擎只用到前 2 个。
ROLE_LABELS = ["主播甲", "主播乙", "主播丙", "主播丁", "主播戊",
               "主播己", "主播庚", "主播辛", "主播壬", "主播癸"]
MAX_VOICES = len(ROLE_LABELS)
# Doubao API 的 speakers 字段上限
DOUBAO_MAX_VOICES = 2

# ============ 豆包音色 ============
MALE_SPEAKERS = [
    ("zh_male_dayixiansheng_v2_saturn_bigtts", "男声 · 大衣先生"),
]
FEMALE_SPEAKERS = [
    ("zh_female_mizaitongxue_v2_saturn_bigtts", "女声 · 米渣同学"),
]

# ============ MiniMax 预设音色（可自由选择，不限性别） ============
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
    # 其他
    ("Chinese (Mandarin)_Kind-hearted_Antie", "女声 · 善良阿姨"),
    ("Japanese_KindLady", "女声 · 日系温柔"),
    # 复刻音色（用户可替换为自己的复刻音色ID）
    ("custom_voice_1", "🎤 复刻音色 1（输入ID）"),
    ("custom_voice_2", "🎤 复刻音色 2（输入ID）"),
    # 从用户音色库选择（最后一项；display 文本用于前缀匹配）
    ("__from_library__", "📚 从音色库选"),
]


class SpeakerRow(ctk.CTkFrame):
    """豆包引擎：固定男声/女声两个下拉。豆包 API 的 speakers 字段最多 2 个。"""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, height=50, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self, text="🎭 男声主播",
            font=ctk.CTkFont("Helvetica Neue", 12, "bold"),
            text_color=GHIBLI["text"],
        ).grid(row=0, column=0, sticky="w", padx=(10, 5), pady=(8, 4))

        ctk.CTkLabel(
            self, text="🎭 女声主播",
            font=ctk.CTkFont("Helvetica Neue", 12, "bold"),
            text_color=GHIBLI["text"],
        ).grid(row=0, column=1, sticky="w", padx=(5, 10), pady=(8, 4))

        self.speaker1_var = ctk.StringVar(value=MALE_SPEAKERS[0][1])
        self.speaker1_menu = ctk.CTkOptionMenu(
            self, variable=self.speaker1_var,
            values=[s[1] for s in MALE_SPEAKERS],
            fg_color=GHIBLI["cream"], button_color=GHIBLI["grass"],
            text_color=GHIBLI["text"], dropdown_fg_color=GHIBLI["white"],
            font=ctk.CTkFont("Helvetica Neue", 11),
            corner_radius=6, height=28,
        )
        self.speaker1_menu.grid(row=1, column=0, sticky="ew", padx=(10, 5), pady=(0, 8))

        self.speaker2_var = ctk.StringVar(value=FEMALE_SPEAKERS[0][1])
        self.speaker2_menu = ctk.CTkOptionMenu(
            self, variable=self.speaker2_var,
            values=[s[1] for s in FEMALE_SPEAKERS],
            fg_color=GHIBLI["cream"], button_color=GHIBLI["grass"],
            text_color=GHIBLI["text"], dropdown_fg_color=GHIBLI["white"],
            font=ctk.CTkFont("Helvetica Neue", 11),
            corner_radius=6, height=28,
        )
        self.speaker2_menu.grid(row=1, column=1, sticky="ew", padx=(5, 10), pady=(0, 8))

    def get_speakers(self):
        s1_display = self.speaker1_var.get()
        s2_display = self.speaker2_var.get()
        s1 = next((k for k, v in MALE_SPEAKERS if v == s1_display), MALE_SPEAKERS[0][0])
        s2 = next((k for k, v in FEMALE_SPEAKERS if v == s2_display), FEMALE_SPEAKERS[0][0])
        return [s1, s2]


class VoiceConfigRow(ctk.CTkFrame):
    """单个 MiniMax 音色配置行，对应一个角色（主播甲/乙/...）。

    主控件：「🎤 <显示名> ▾」按钮，点开弹音色库选择器（仿 VoxCPM 风格）。
    高级入口（折叠）：MiniMax 系统预设下拉 + 任意 ID 粘贴输入框。
    """

    def __init__(self, parent, on_remove=None, **kwargs):
        super().__init__(parent, fg_color=GHIBLI["cream_dark"], corner_radius=8, **kwargs)
        self._index = 0
        self.on_remove = on_remove
        self._voice_id: str | None = None  # 主控件当前选的 voice_id
        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0)

        # 角色标签
        self._label_widget = ctk.CTkLabel(
            self, text="",
            font=ctk.CTkFont("Helvetica Neue", 12, "bold"),
            text_color=GHIBLI["text"], width=72, anchor="w",
        )
        self._label_widget.grid(row=0, column=0, sticky="w", padx=(10, 5), pady=(8, 0))

        # 主控件：仿 VoxCPM 风格的「点击选音色」按钮
        self.pick_btn = ctk.CTkButton(
            self, text="📚 从音色库选…",
            fg_color=GHIBLI["cream"], hover_color=GHIBLI["dust"],
            text_color=GHIBLI["text"],
            font=ctk.CTkFont("Helvetica Neue", 12),
            corner_radius=6, height=30,
            command=self._open_picker,
        )
        self.pick_btn.grid(row=0, column=1, sticky="ew", padx=(5, 5), pady=(8, 0))

        # 删除按钮
        self._remove_btn = ctk.CTkButton(
            self, text="🗑", width=32, height=30,
            fg_color=GHIBLI["dust"], hover_color=GHIBLI["text_light"],
            text_color=GHIBLI["white"], corner_radius=6,
            state="disabled",
        )
        self._remove_btn.grid(row=0, column=2, sticky="e", padx=(5, 10), pady=(8, 0))

        # 语速滑块（0.5x - 2.0x，步进 0.05，默认 1.0）
        self._speed_label = ctk.CTkLabel(
            self, text="🐢 语速",
            font=ctk.CTkFont("Helvetica Neue", 11),
            text_color=GHIBLI["text"], width=72, anchor="w",
        )
        self._speed_label.grid(row=1, column=0, sticky="w", padx=(10, 5), pady=(4, 8))

        self.speed_var = ctk.DoubleVar(value=1.0)
        self.speed_slider = ctk.CTkSlider(
            self, from_=0.5, to=2.0, number_of_steps=30,
            variable=self.speed_var,
            fg_color=GHIBLI["cream"], progress_color=GHIBLI["grass"],
            button_color=GHIBLI["totoro"], button_hover_color=GHIBLI["totoro_dark"],
            height=14,
            command=self._on_speed_change,
        )
        self.speed_slider.grid(row=1, column=1, sticky="ew", padx=(5, 5), pady=(4, 8))

        self._speed_value_var = ctk.StringVar(value="1.00x")
        self._speed_value_label = ctk.CTkLabel(
            self, textvariable=self._speed_value_var, width=44,
            font=ctk.CTkFont("Helvetica Neue", 11, "bold"),
            text_color=GHIBLI["text"],
        )
        self._speed_value_label.grid(row=1, column=2, sticky="e", padx=(5, 10), pady=(4, 8))

        # 高级选项（折叠）：MiniMax 系统预设 + 粘贴任意 ID
        self._advanced_visible = False
        self._advanced_btn = ctk.CTkButton(
            self, text="⚙ 高级（系统预设 / 粘贴 ID）",
            fg_color="transparent", hover_color=GHIBLI["cream"],
            text_color=GHIBLI["text_light"],
            font=ctk.CTkFont("Helvetica Neue", 10),
            corner_radius=6, height=22,
            command=self._toggle_advanced,
        )
        self._advanced_btn.grid(row=2, column=0, columnspan=3, sticky="ew",
                                  padx=(10, 10), pady=(2, 2))

        # 高级面板（默认隐藏）
        self._advanced_frame = ctk.CTkFrame(self, fg_color=GHIBLI["cream"], corner_radius=6)
        # row=3 占位但先 grid_remove，toggle 时再 grid
        self._advanced_frame.grid(row=3, column=0, columnspan=3, sticky="ew",
                                    padx=(10, 10), pady=(0, 8))
        self._advanced_frame.grid_remove()
        self._build_advanced()

    def _build_advanced(self):
        """高级面板：MiniMax 系统预设 dropdown + 任意 ID 粘贴 entry。"""
        self._advanced_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self._advanced_frame, text="系统预设",
            font=ctk.CTkFont("Helvetica Neue", 10),
            text_color=GHIBLI["text_light"], width=72, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(8, 5), pady=(6, 4))
        self._preset_var = ctk.StringVar(value="（不选）")
        preset_values = ["（不选）"] + [v[1] for v in MINIMAX_VOICES
                                        if not v[1].startswith(("📚", "🎤 复刻"))]
        self._preset_menu = ctk.CTkOptionMenu(
            self._advanced_frame, variable=self._preset_var,
            values=preset_values,
            fg_color=GHIBLI["white"], button_color=GHIBLI["grass"],
            text_color=GHIBLI["text"], dropdown_fg_color=GHIBLI["white"],
            font=ctk.CTkFont("Helvetica Neue", 10),
            corner_radius=6, height=24,
            command=self._on_preset_pick,
        )
        self._preset_menu.grid(row=0, column=1, sticky="ew", padx=(5, 8), pady=(6, 4))

        ctk.CTkLabel(
            self._advanced_frame, text="粘贴 ID",
            font=ctk.CTkFont("Helvetica Neue", 10),
            text_color=GHIBLI["text_light"], width=72, anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=(8, 5), pady=(2, 6))
        self._custom_var = ctk.StringVar()
        self._custom_entry = ctk.CTkEntry(
            self._advanced_frame, textvariable=self._custom_var,
            placeholder_text="任意 MiniMax voice_id",
            fg_color=GHIBLI["cream_dark"],
            text_color=GHIBLI["text"],
            border_color=GHIBLI["dust"], border_width=1,
            font=ctk.CTkFont("Helvetica Neue", 11),
            corner_radius=6, height=24,
        )
        self._custom_entry.grid(row=1, column=1, sticky="ew", padx=(5, 8), pady=(2, 6))
        self._custom_entry.bind("<FocusOut>", lambda e: self._on_custom_id_change())
        self._custom_entry.bind("<Return>", lambda e: self._on_custom_id_change())

    def _toggle_advanced(self):
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self._advanced_frame.grid()
            self._advanced_btn.configure(text="⚙ 收起高级选项")
        else:
            self._advanced_frame.grid_remove()
            self._advanced_btn.configure(text="⚙ 高级（系统预设 / 粘贴 ID）")

    def _open_picker(self):
        from gui.panels.voice_library_panel import VoicePickerModal
        modal = VoicePickerModal(self.winfo_toplevel(), on_pick=self._on_library_pick)
        self.wait_window(modal)

    def _on_library_pick(self, voice_id):
        """用户从音色库选了 voice。"""
        import voice_library as vl
        rec = vl.get(voice_id)
        display = rec["display_name"] if rec else voice_id
        self._voice_id = voice_id
        # 同步重置高级面板（避免显示陈旧内容）
        self._preset_var.set("（不选）")
        self._custom_var.set("")
        self.pick_btn.configure(text=f"🎤 {display}  ▾")

    def _on_preset_pick(self, display: str):
        """用户从高级面板选了 MiniMax 系统预设。"""
        if display == "（不选）":
            return
        voice_id = next((k for k, v in MINIMAX_VOICES if v == display), None)
        if not voice_id:
            return
        self._voice_id = voice_id
        self._custom_var.set("")
        self.pick_btn.configure(text=f"🎤 {display}  ▾")

    def _on_custom_id_change(self):
        """用户在高级面板粘贴了任意 voice_id（回车或失焦触发）。"""
        custom = self._custom_var.get().strip()
        if not custom:
            return
        self._voice_id = custom
        self._preset_var.set("（不选）")
        self.pick_btn.configure(text=f"🎤 {custom}  ▾")

    def _on_speed_change(self, value: float):
        self._speed_value_var.set(f"{value:.2f}x")

    def set_index(self, new_index):
        self._index = new_index
        label = ROLE_LABELS[new_index] if new_index < len(ROLE_LABELS) else f"主播{new_index+1}"
        self._label_widget.configure(text=f"🎭 {label}")
        if new_index < 2:
            self._remove_btn.configure(state="disabled")
        else:
            self._remove_btn.configure(
                state="normal",
                command=lambda: self.on_remove(self._index) if self.on_remove else None,
            )

    def get_voice_id(self):
        """返回当前选中 voice 的 ID。
        优先级：高级面板自定义 ID > 系统预设 > 主控件（音色库选择）。"""
        custom = self._custom_var.get().strip()
        if custom:
            return custom
        preset_display = self._preset_var.get()
        if preset_display and preset_display != "（不选）":
            voice_id = next((k for k, v in MINIMAX_VOICES if v == preset_display), None)
            if voice_id:
                return voice_id
        return self._voice_id or ""

    def get_speed(self) -> float:
        return round(self.speed_var.get(), 2)

    def get_label(self):
        return ROLE_LABELS[self._index] if self._index < len(ROLE_LABELS) else f"主播{self._index+1}"


class MiniMaxVoiceList(ctk.CTkFrame):
    """MiniMax 引擎用的动态音色列表。默认 2 行（甲/乙），可加可删，最多 MAX_VOICES 行。
    通过 get_voice_map() 拿到 {角色标签: voice_id} 字典，传给合成器。

    布局：行装在 CTkScrollableFrame 里（避免多了看不完），➕ 按钮固定在外侧。
    """

    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)  # model selector (固定高度)
        self.grid_rowconfigure(1, weight=1)  # scroll (扩展)
        self.rows = []

        # ============ 模型选择器（顶部，全局生效） ============
        model_frame = ctk.CTkFrame(self, fg_color="transparent")
        model_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(0, 4))
        model_frame.grid_columnconfigure(0, weight=0)
        model_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            model_frame, text="模型：",
            font=ctk.CTkFont("Helvetica Neue", 12),
            text_color=GHIBLI["text"],
        ).grid(row=0, column=0, sticky="w")
        self.model_var = ctk.StringVar(value=minimax_tts.DEFAULT_MODEL)
        ctk.CTkOptionMenu(
            model_frame, variable=self.model_var,
            values=minimax_tts.get_supported_models(),
            fg_color=GHIBLI["cream_dark"], button_color=GHIBLI["grass"],
            text_color=GHIBLI["text"], dropdown_fg_color=GHIBLI["white"],
            font=ctk.CTkFont("Helvetica Neue", 11),
            corner_radius=6, height=28,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        # 滚动容器：装 voice rows
        # 实测每个 VoiceConfigRow 实际高度 116px，2 行 ≈ 232px。
        # height=240 让 2 行完整可见；加 3+ 行时滚动条自然出现。
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color=GHIBLI["cream_dark"], corner_radius=6, height=240,
        )
        self._scroll.grid(row=1, column=0, sticky="nsew", padx=0, pady=(0, 4))
        self._scroll.grid_columnconfigure(0, weight=1)

        # 默认 2 行（保持与旧 MiniMaxVoiceRow 行为一致）
        self._add_row()  # 主播甲
        self._add_row()  # 主播乙
        self._build_add_button()

    def get_model(self) -> str:
        """当前选中的模型名称（全局）。"""
        return self.model_var.get()

    def _add_row(self):
        if len(self.rows) >= MAX_VOICES:
            return
        # rows 放进 scroll 容器而不是 self
        row = VoiceConfigRow(self._scroll, on_remove=self._remove_row)
        row.grid(row=len(self.rows), column=0, sticky="ew", padx=4, pady=(0, 4))
        row.set_index(len(self.rows))
        self.rows.append(row)
        self._relayout_add_btn()

    def _remove_row(self, index):
        # 至少保留 2 行（甲/乙），保证脚本里这俩角色永远有音色
        if index < 2 or index >= len(self.rows):
            return
        self.rows[index].destroy()
        self.rows.pop(index)
        # 重新编号，保证"主播甲/乙/丙/..."连续
        for i, row in enumerate(self.rows):
            row.set_index(i)
        self._relayout_add_btn()

    def _relayout_add_btn(self):
        if hasattr(self, "_add_btn") and self._add_btn is not None:
            # 按钮固定在 scroll 容器外面 row=2（model=row=0, scroll=row=1）
            self._add_btn.grid(
                row=2, column=0, sticky="ew", padx=12, pady=(4, 4)
            )

    def _build_add_button(self):
        self._add_btn = ctk.CTkButton(
            self, text="➕  添加音色",
            fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
            text_color=GHIBLI["white"], corner_radius=8, height=32,
            command=self._add_row,
        )
        self._relayout_add_btn()

    def get_voice_map(self):
        """返回 {角色标签: voice_id} 字典，按 index 顺序（甲→乙→丙...）。
        脚本里的"主播X"会按此映射选音色；脚本里出现但未配置的标签由合成器兜底为 voice1。"""
        return {row.get_label(): row.get_voice_id() for row in self.rows}

    def get_speed_map(self):
        """返回 {角色标签: speed} 字典（与 get_voice_map() 同序）。
        合成器会按角色标签匹配 speed，未命中时回退到 1.0。"""
        return {row.get_label(): row.get_speed() for row in self.rows}


# ============== 本地 VoxCPM 引擎专用：动态音色列表 ==============

class VoxCPMVoiceConfigRow(ctk.CTkFrame):
    """VoxCPM 本地克隆一行：角色标签 + 「从音色库选」按钮 + 语速滑块 + 删除按钮。

    区别于 MiniMax VoiceConfigRow：
      - 没有 MiniMax 预设 dropdown（VoxCPM 只用本地 wav_path）
      - 没有 custom_var 入口（直接显示已选 voice 名字）
      - 强制走 voice_library（库里有 wav_path 即可）
    """

    def __init__(self, parent, on_remove=None):
        super().__init__(parent, fg_color=GHIBLI["cream"], corner_radius=6)
        self._index = 0
        self.on_remove = on_remove
        self._voice_id = None  # 当前选中的 voice_id（库里的）
        self.grid_columnconfigure(1, weight=1)

        # 角色标签（固定：主播甲/乙/丙...）
        self._label_widget = ctk.CTkLabel(
            self, text="🎭 主播甲",
            font=ctk.CTkFont("Helvetica Neue", 12, "bold"),
            text_color=GHIBLI["text"], width=80, anchor="w",
        )
        self._label_widget.grid(row=0, column=0, sticky="w", padx=(10, 5), pady=(8, 0))

        # 「从音色库选」按钮（显示当前 voice 名或 "未选"）
        self.pick_btn = ctk.CTkButton(
            self, text="📚 从音色库选…",
            fg_color=GHIBLI["cream_dark"], hover_color=GHIBLI["dust"],
            text_color=GHIBLI["text"],
            font=ctk.CTkFont("Helvetica Neue", 12),
            corner_radius=6, height=30,
            command=self._open_picker,
        )
        self.pick_btn.grid(row=0, column=1, sticky="ew", padx=(5, 5), pady=(8, 0))

        # 删除按钮
        self._remove_btn = ctk.CTkButton(
            self, text="🗑", width=32, height=30,
            fg_color=GHIBLI["dust"], hover_color=GHIBLI["text_light"],
            text_color=GHIBLI["white"], corner_radius=6,
            state="disabled",
        )
        self._remove_btn.grid(row=0, column=2, sticky="e", padx=(5, 10), pady=(8, 0))

        # 语速滑块（VoxCPM 走 ffmpeg atempo 后期变速；范围保守 0.7-1.5）
        ctk.CTkLabel(
            self, text="🐢 语速",
            font=ctk.CTkFont("Helvetica Neue", 11),
            text_color=GHIBLI["text"], width=72, anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=(10, 5), pady=(4, 8))

        self.speed_var = ctk.DoubleVar(value=1.0)
        self.speed_slider = ctk.CTkSlider(
            self, from_=0.7, to=1.5, number_of_steps=16,
            variable=self.speed_var,
            fg_color=GHIBLI["cream"], progress_color=GHIBLI["grass"],
            button_color=GHIBLI["totoro"], button_hover_color=GHIBLI["totoro_dark"],
            height=14,
            command=self._on_speed_change,
        )
        self.speed_slider.grid(row=1, column=1, sticky="ew", padx=(5, 5), pady=(4, 8))

        self._speed_value_var = ctk.StringVar(value="1.00x")
        ctk.CTkLabel(
            self, textvariable=self._speed_value_var, width=48,
            font=ctk.CTkFont("Helvetica Neue", 11, "bold"),
            text_color=GHIBLI["text"],
        ).grid(row=1, column=2, sticky="e", padx=(5, 10), pady=(4, 8))

    def _open_picker(self):
        from gui.panels.voice_library_panel import VoicePickerModal
        modal = VoicePickerModal(self.winfo_toplevel(), on_pick=self._on_pick)
        self.wait_window(modal)

    def _on_pick(self, voice_id):
        import voice_library as vl
        rec = vl.get(voice_id)
        self._voice_id = voice_id
        display = rec["display_name"] if rec else voice_id
        self.pick_btn.configure(text=f"🎤 {display}  ▾")

    def _on_speed_change(self, value: float):
        self._speed_value_var.set(f"{value:.2f}x")

    def set_index(self, new_index):
        self._index = new_index
        label = ROLE_LABELS[new_index] if new_index < len(ROLE_LABELS) else f"主播{new_index+1}"
        self._label_widget.configure(text=f"🎭 {label}")
        if new_index < 2:
            self._remove_btn.configure(state="disabled")
        else:
            self._remove_btn.configure(
                state="normal",
                command=lambda: self.on_remove(self._index) if self.on_remove else None,
            )

    def get_voice_id(self):
        return self._voice_id or ""

    def get_speed(self) -> float:
        return round(self.speed_var.get(), 2)

    def get_label(self):
        return ROLE_LABELS[self._index] if self._index < len(ROLE_LABELS) else f"主播{self._index+1}"


class VoxCPMVoiceList(ctk.CTkFrame):
    """VoxCPM 本地引擎用的动态音色列表。

    与 MiniMaxVoiceList 同形，但每行直接走 voice_library（无预设 dropdown）。
    默认 2 行（甲/乙），可加可删，最多 MAX_VOICES 行。
    """

    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)  # scroll (扩展)
        self.rows = []

        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color=GHIBLI["cream_dark"], corner_radius=6, height=240,
        )
        self._scroll.grid(row=0, column=0, sticky="nsew", padx=0, pady=(0, 4))
        self._scroll.grid_columnconfigure(0, weight=1)

        self._add_row()  # 主播甲
        self._add_row()  # 主播乙
        self._build_add_button()

    def _add_row(self):
        if len(self.rows) >= MAX_VOICES:
            return
        row = VoxCPMVoiceConfigRow(self._scroll, on_remove=self._remove_row)
        row.grid(row=len(self.rows), column=0, sticky="ew", padx=4, pady=(0, 4))
        row.set_index(len(self.rows))
        self.rows.append(row)
        self._relayout_add_btn()

    def _remove_row(self, index):
        if index < 2 or index >= len(self.rows):
            return
        self.rows[index].destroy()
        self.rows.pop(index)
        for i, row in enumerate(self.rows):
            row.set_index(i)
        self._relayout_add_btn()

    def _relayout_add_btn(self):
        if hasattr(self, "_add_btn") and self._add_btn is not None:
            self._add_btn.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 4))

    def _build_add_button(self):
        self._add_btn = ctk.CTkButton(
            self, text="➕  添加音色",
            fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
            text_color=GHIBLI["white"], corner_radius=8, height=32,
            command=self._add_row,
        )
        self._relayout_add_btn()

    def get_voice_map(self):
        """返回 {角色标签: voice_id} 字典（只含已选 voice 的行）。"""
        return {row.get_label(): row.get_voice_id() for row in self.rows if row.get_voice_id()}

    def get_speed_map(self):
        """返回 {角色标签: speed} 字典（含所有行，未选 voice 的也返回默认 1.0）。"""
        return {row.get_label(): row.get_speed() for row in self.rows}
