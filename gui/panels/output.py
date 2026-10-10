#!/usr/bin/env python3
"""
输出面板 - 显示生成结果，包含后处理选项
"""

import asyncio
import os
import threading
from pathlib import Path

import customtkinter as ctk

import minimax_tts
from gui.theme import GHIBLI
from gui.utils import run_async, is_main_thread
from gui.panels.speakers import ROLE_LABELS
from config import CONFIG


# ============== 输出面板 ==============


def parse_script_segments(edited_text: str) -> list[dict]:
    """把编辑框文本解析成 [{"speaker": "主播甲", "text": "..."}]。

    格式：每段 ``【speaker】\\ntext``，段间用 ``\\n\\n---\\n\\n`` 分隔。

    健壮性：
      - 段首可能夹带 ``\\n\\n--\\n\\n`` 等残留（用户手编辑时常见），
        只看段内**第一对** ``【...】`` 的内容作为 speaker，之外的字符忽略。
      - 段尾空白自动 strip。
      - speaker 或 content 为空则跳过该段（不抛错）。
      - 没有 ``】`` 的段跳过。

    可作为纯函数测试，不依赖 GUI。
    """
    out: list[dict] = []
    for seg in edited_text.split("\n\n---\n\n"):
        seg = seg.strip()
        if not seg:
            continue
        brace_open = seg.find("【")
        brace_close = seg.find("】", brace_open + 1) if brace_open != -1 else -1
        if brace_open == -1 or brace_close == -1:
            continue
        speaker = seg[brace_open + 1:brace_close].strip()
        content = seg[brace_close + 1:].strip()
        if speaker and content:
            out.append({"speaker": speaker, "text": content})
    return out

# 配置保存锁，防止并发写入
_config_lock = threading.Lock()


def _atomic_write_json(path: Path, data: dict):
    """原子写入 JSON 文件"""
    import json
    import tempfile
    # 写入临时文件再原子替换
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, dir=path.parent) as f:
        json.dump(data, f, indent=2)
        temp_path = f.name
    os.replace(temp_path, path)


class OutputPanel(ctk.CTkFrame):
    def __init__(self, parent, input_panel=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.input_panel = input_panel  # 引用 InputPanel 读音色库选中状态
        self.grid_rowconfigure(4, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self._audio_url = None
        self._pending_texts = None  # 暂存的台词，用于编辑后生成
        self._status_var = ctk.StringVar(value="等待输入内容...")
        self._status_var.trace_add("write", lambda *_: self._on_status_change())
        self.normalize_var = ctk.BooleanVar(
            value=CONFIG.get("post_processing", "normalize_audio", default=True)
        )
        self.cleanup_var = ctk.BooleanVar(
            value=CONFIG.get("post_processing", "cleanup_temp_files", default=True)
        )

        # Auto-save on change
        self.normalize_var.trace_add("write", lambda *_: self._save_config())
        self.cleanup_var.trace_add("write", lambda *_: self._save_config())

        # 标题 + 生成按钮（同一行，按钮在右侧）
        ctk.CTkLabel(
            self, text="🎧  生成结果",
            font=ctk.CTkFont("Helvetica Neue", 16, "bold"),
            text_color=GHIBLI["sky_dark"], anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 4))

        self.gen_btn = ctk.CTkButton(
            self, text="🎵 生成台词",
            font=ctk.CTkFont("Helvetica Neue", 14, "bold"),
            fg_color=GHIBLI["totoro"], hover_color=GHIBLI["totoro_dark"],
            text_color=GHIBLI["white"], corner_radius=8, height=36, width=160,
            command=self._on_generate,
        )
        self.gen_btn.grid(row=0, column=1, sticky="e", padx=18, pady=(16, 4))

        # 状态
        self.status_lbl = ctk.CTkLabel(
            self, textvariable=self._status_var,
            font=ctk.CTkFont("Helvetica Neue", 12),
            text_color=GHIBLI["text_light"], anchor="w",
        )
        self.status_lbl.grid(row=1, column=0, sticky="w", padx=18, pady=(0, 6))

        # 进度条
        self.progress = ctk.CTkProgressBar(
            self, progress_color=GHIBLI["totoro"],
            fg_color=GHIBLI["cream_dark"], corner_radius=8, height=8,
        )
        self.progress.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 10))
        self.progress.set(0)

        # 后处理选项
        post_processing_frame = ctk.CTkFrame(self, fg_color="transparent")
        post_processing_frame.grid(row=3, column=0, sticky="w", padx=18, pady=(0, 8))
        ctk.CTkLabel(
            post_processing_frame, text="后处理:",
            font=ctk.CTkFont("Helvetica Neue", 12),
            text_color=GHIBLI["text_light"],
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.normalize_cb = ctk.CTkCheckBox(
            post_processing_frame, text="音量标准化 (loudnorm)",
            variable=self.normalize_var,
            fg_color=GHIBLI["grass"],
        )
        self.normalize_cb.grid(row=0, column=1, sticky="w", padx=(0, 12))
        self.cleanup_cb = ctk.CTkCheckBox(
            post_processing_frame, text="清理临时文件",
            variable=self.cleanup_var,
            fg_color=GHIBLI["grass"],
        )
        self.cleanup_cb.grid(row=0, column=2, sticky="w")

        # 导出格式（MiniMax 引擎时生效；loudnorm 仅对 mp3 起作用）
        ctk.CTkLabel(
            post_processing_frame, text="导出格式：",
            font=ctk.CTkFont("Helvetica Neue", 12),
            text_color=GHIBLI["text_light"],
        ).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(4, 0))
        self.format_var = ctk.StringVar(value="mp3")
        self.format_menu = ctk.CTkOptionMenu(
            post_processing_frame, variable=self.format_var,
            values=minimax_tts.get_supported_formats(),
            fg_color=GHIBLI["cream_dark"], button_color=GHIBLI["grass"],
            text_color=GHIBLI["text"], dropdown_fg_color=GHIBLI["white"],
            font=ctk.CTkFont("Helvetica Neue", 11),
            corner_radius=6, height=28, width=110,
            command=self._on_format_change,
        )
        self.format_menu.grid(row=1, column=1, sticky="w", padx=(0, 12), pady=(4, 0))

        # 标签页：日志 / 台词编辑
        self.tabs = ctk.CTkTabview(
            self, fg_color=GHIBLI["cream"],
            height=320,
        )
        self.tabs.grid(row=4, column=0, sticky="nsew", padx=14, pady=(0, 8))
        self.tabs.add("📋 日志")
        self.tabs.add("✏️ 台词编辑")

        # 日志面板
        log_tab = self.tabs.tab("📋 日志")
        log_tab.grid_rowconfigure(0, weight=1)
        log_tab.grid_columnconfigure(0, weight=1)
        self.log = ctk.CTkTextbox(
            log_tab, fg_color=GHIBLI["cream"], border_width=0,
            font=ctk.CTkFont("Menlo", 11), text_color=GHIBLI["text"],
            corner_radius=8,
        )
        self.log.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))

        # 日志按钮行
        log_btn_row = ctk.CTkFrame(log_tab, fg_color="transparent")
        log_btn_row.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        log_btn_row.grid_columnconfigure(0, weight=1)
        log_btn_row.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            log_btn_row, text="🗑️  清空日志",
            fg_color=GHIBLI["dust"], hover_color=GHIBLI["text_light"],
            text_color=GHIBLI["white"], corner_radius=8, height=28,
            command=self._clear_log,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))

        ctk.CTkButton(
            log_btn_row, text="💾  导出日志",
            fg_color=GHIBLI["sky_dark"], hover_color=GHIBLI["sky"],
            text_color=GHIBLI["white"], corner_radius=8, height=28,
            command=self._export_log,
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.log.configure(state="disabled")
        self._log_buffer = []  # 日志缓冲区

        # 台词编辑面板（常驻可输入：AI 生成 / 粘贴导入 / 手写均可）
        edit_tab = self.tabs.tab("✏️ 台词编辑")
        edit_tab.grid_rowconfigure(0, weight=1)
        edit_tab.grid_columnconfigure(0, weight=1)
        self.script_editor = ctk.CTkTextbox(
            edit_tab, fg_color=GHIBLI["cream"],
            border_color=GHIBLI["sky_dark"], border_width=1.5,
            font=ctk.CTkFont("Helvetica Neue", 13),
            text_color=GHIBLI["text"], corner_radius=8,
        )
        self.script_editor.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.script_editor.insert(
            "0.0",
            "【甲】\n台词正文...\n\n---\n\n【乙】\n台词正文...",
        )

        # 按钮行（4 列：打开文件夹 / 用台词生成音频 / 保存脚本 / 复制链接）
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=5, column=0, sticky="ew", padx=14, pady=(0, 14))
        btn_row.grid_columnconfigure(0, weight=1)
        btn_row.grid_columnconfigure(1, weight=1)
        btn_row.grid_columnconfigure(2, weight=1)
        btn_row.grid_columnconfigure(3, weight=1)

        ctk.CTkButton(
            btn_row, text="📂  打开文件夹",
            fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
            text_color=GHIBLI["white"], corner_radius=10,
            command=lambda: os.system("open ~/Documents/podcast-generator/output"),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.gen_from_script_btn = ctk.CTkButton(
            btn_row, text="🎵  用台词生成音频",
            fg_color=GHIBLI["totoro"], hover_color=GHIBLI["totoro_dark"],
            text_color=GHIBLI["white"], corner_radius=10,
            command=self._on_gen_from_script,
        )
        self.gen_from_script_btn.grid(row=0, column=1, sticky="ew", padx=(4, 4))

        self.save_script_btn = ctk.CTkButton(
            btn_row, text="💾  保存脚本",
            fg_color=GHIBLI["sky_dark"], hover_color=GHIBLI["sky"],
            text_color=GHIBLI["white"], corner_radius=10,
            command=self._on_save_script_clicked,
        )
        self.save_script_btn.grid(row=0, column=2, sticky="ew", padx=(4, 4))

        self.copy_btn = ctk.CTkButton(
            btn_row, text="🔗  复制链接",
            fg_color=GHIBLI["dust"], hover_color=GHIBLI["forest_dark"],
            text_color=GHIBLI["white"], corner_radius=10,
            state="disabled", command=self._do_copy,
        )
        self.copy_btn.grid(row=0, column=3, sticky="ew", padx=(4, 0))

    # ============== 线程安全 widget 更新 ==============
    #
    # 所有 async 协程（run_async 跑到 worker 线程里的）必须通过 _ui() 跳回
    # Tk 主线程做 widget 操作。直接 self.set_status(...) / self.progress.set(...)
    # 在 worker 线程上跑会触发 macOS 27 / Tcl-Tk 9 的
    # `Tk_FreeGC + ConfigurePolygon` SIGSEGV —— crash report 已实测确认。
    #
    # 用法：
    #   await asyncio.sleep(0.05)        # 让出一次事件循环
    #   self._ui(self._log, "hello")     # 异步协程里写日志
    #   self._ui(self.progress.set, 0.5) # 异步协程里更新进度
    #
    # 同步代码（非 async）里直接 self.set_status(X) 即可 —— 它本来就在主线程。

    def _ui(self, fn, *args, **kwargs):
        """把 widget 方法调用调度到 Tk 主线程（after(0, ...) 模式）。

        - 在主线程调用：直接执行 fn(*args, **kwargs)
        - 在 worker 线程调用：schedule to main thread via self.after(0, ...)
          返回一个 handle，可选择 .get() 同步等待结果（不常用，因为会卡住事件循环）

        这是修 async-on-worker-thread Tk crash 的核心 helper。
        """
        if is_main_thread():
            return fn(*args, **kwargs)
        # lambda 闭包捕获 args/kwargs，避免可变默认参数陷阱
        self.after(0, lambda: fn(*args, **kwargs))

    def _ui_get(self, getter_fn):
        """在主线程执行 getter_fn() 并**同步等待**返回值。

        用于从 worker 线程读取 widget 状态（如 StringVar.get()、widget.cget()）。
        用 after(0,...) 调度 + queue.Queue 同步等待结果。
        主线程调用时直接执行，零开销。
        """
        if is_main_thread():
            return getter_fn()
        import queue
        q: "queue.Queue" = queue.Queue(maxsize=1)
        def _run():
            try:
                q.put(("ok", getter_fn()))
            except BaseException as e:  # noqa: BLE001  任何异常都回传
                q.put(("err", e))
        self.after(0, _run)
        kind, val = q.get()
        if kind == "err":
            raise val
        return val

    def _save_config(self):
        """保存后处理选项到配置文件（线程安全）"""
        import json
        config_path = Path(__file__).parent.parent.parent / "config.json"
        with _config_lock:
            if config_path.exists():
                config_data = json.loads(config_path.read_text())
            else:
                config_data = {}
            config_data.setdefault("post_processing", {})
            config_data["post_processing"]["normalize_audio"] = self.normalize_var.get()
            config_data["post_processing"]["cleanup_temp_files"] = self.cleanup_var.get()
            _atomic_write_json(config_path, config_data)

    def _get_scripts_dir(self):
        """返回脚本保存目录（自动创建）。"""
        scripts_dir = (
            Path.home()
            / "Library"
            / "Application Support"
            / "PodcastGenerator"
            / "scripts"
        )
        scripts_dir.mkdir(parents=True, exist_ok=True)
        return scripts_dir

    def _save_script(self, segments, source_meta):
        """保存生成的台词为 JSON，便于后续「用台词生成音频」时无 NLP 重新加载。

        Args:
            segments: list of {"speaker": str, "text": str}
            source_meta: dict, 描述本次生成来源（engine / source 模式等）

        Returns:
            写入文件的 Path
        """
        import datetime
        import json
        import uuid

        scripts_dir = self._get_scripts_dir()
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        short = uuid.uuid4().hex[:6]
        path = scripts_dir / f"script_{stamp}_{short}.json"
        payload = {
            "created_at": datetime.datetime.now().isoformat(),
            "source": source_meta or {},
            "segments": list(segments or []),
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def get_output_options(self):
        """获取输出选项"""
        return {
            "normalize_audio": self.normalize_var.get(),
            "cleanup_temp_files": self.cleanup_var.get(),
            "output_format": self.format_var.get(),
        }

    def _on_format_change(self, value):
        """导出格式变更时：非 mp3 自动取消音量标准化（loudnorm 仅适用 mp3-like 输入）。"""
        if value != "mp3" and self.normalize_var.get():
            self.normalize_var.set(False)
            self._log("[*] 切换为非 mp3 格式：自动禁用音量标准化（loudnorm 仅兼容 mp3）")

    def _get_voice_role_map(self):
        """从 SpeakerRow 读取当前豆包 voice 列表，构造 voice_id -> 角色标签 的映射。

        SpeakerRow 中第一项固定是「男声主播」（"主播甲"），第二项是「女声主播」（"主播乙"）。
        返回 dict，例如 {"zh_male_xxx": "主播甲", "zh_female_xxx": "主播乙"}。
        缺省时仅返回已有的映射，未配置的槽位不会出现在结果中。
        """
        speakers = self.winfo_toplevel().content.left.speaker_row.get_speakers()
        voice_to_label = {}
        for i, voice_id in enumerate(speakers[:2]):
            voice_to_label[voice_id] = ROLE_LABELS[i]
        return voice_to_label

    def set_status(self, msg):
        self.status_lbl.configure(text=msg)
        self._status_var.set(msg)
        self._log(f"[STATUS] {msg}")

    def _on_status_change(self):
        """Re-enable generate button when status reaches a terminal state."""
        if not hasattr(self, "gen_btn"):
            return
        msg = self._status_var.get()
        if msg.startswith("✅") or msg.startswith("❌") or msg.startswith("⚠️"):
            self.gen_btn.configure(state="normal", text="🎵 生成台词")

    def _on_generate(self):
        """Top-right generate button: reads input from left panel, kicks off generation.

        根据凭证面板的「台词生成」选择分发：
          - 豆包（默认）：原 generate_podcast 异步流程
          - MiniMax LLM：MiniMaxScriptGenerator 同步生成（线程异步调用，结果灌入同一编辑器）
        """
        left = self.master.left  # MainContent.left
        active = left.tabs.get()

        # 抽取输入（URL/文本/文件）
        url, text = self._extract_input(left, active)
        if url is None and text is None:
            return  # 状态已写入

        # Disable button + change label while running
        self.gen_btn.configure(state="disabled", text="🔄 生成中...")
        self.update_idletasks()

        script_engine = left.cred.get_script_engine()
        if script_engine == "minimax_llm":
            if url is not None:
                self.set_status("⚠️ MiniMax LLM 不支持 URL，请把内容粘贴到「📝 输入文本」tab")
                self.gen_btn.configure(state="normal", text="🎵 生成台词")
                return
            self._generate_via_minimax_llm(text, active)
        else:
            # 豆包原路径
            if url is not None:
                self.set_status(f"🔄  生成台词中: {url[:60]}...")
                run_async(self.generate(url=url, only_nlp_text=True))
            else:
                if active.startswith("📁"):
                    label = os.path.basename(left.selected_file_path)
                else:
                    label = "文本内容"
                self.set_status(f"🔄  生成台词中: {label[:60]}...")
                run_async(self.generate(text=text, only_nlp_text=True))

    def _extract_input(self, left, active):
        """从左侧当前激活 tab 取输入。

        Returns:
            (url, text) — URL tab 返回 (url, None)；其他 tab 返回 (None, text)。
            出错时返回 (None, None)，状态条已写入。
        """
        if active.startswith("🌐 URL"):
            raw = left.url_box.get("0.0", "end").strip()
            lines = [l.strip() for l in raw.split("\n") if l.strip()]
            if not lines:
                self.set_status("⚠️  请先输入 URL")
                return None, None
            url = lines[0]
            if not url.startswith("http"):
                self.set_status("⚠️  URL 格式不正确")
                return None, None
            return url, None
        elif active.startswith("📁"):
            if not left.selected_file_path:
                self.set_status("⚠️  请先选择文件")
                return None, None
            text = left._extract_text(left.selected_file_path)
            if text.startswith("["):
                self.set_status(f"⚠️  {text}")
                return None, None
            return None, text
        else:
            text = left.txt_box.get("0.0", "end").strip()
            if not text:
                self.set_status("⚠️  请先输入文本内容")
                return None, None
            return None, text

    def _generate_via_minimax_llm(self, text, active_tab):
        """走 MiniMax LLM 生成台词（线程异步，结果灌入同一 script_editor）。"""
        left = self.master.left
        api_key = left.cred.get_minimax_key()
        if not api_key:
            self.set_status("⚠️  请先填写 MiniMax API Key")
            self.gen_btn.configure(state="normal", text="🎵 生成台词")
            return

        self._log("[*] 用 MiniMax LLM 生成台词...")
        self.set_status("🔄 调用 MiniMax LLM（深度思考生成脚本，复杂主题可能 30-60 秒到 5-10 分钟）...")
        self.progress.set(0.05)

        def _worker():
            try:
                from minimax_script_gen import MiniMaxScriptGenerator, DEFAULT_HOSTS_2
                gen = MiniMaxScriptGenerator(api_key=api_key)
                # 长文本视为 context，让 LLM 自己抽取主题；短文本视为 topic
                if len(text) > 600:
                    topic, context = "", text
                else:
                    topic, context = text, ""
                segments = gen.generate(
                    topic=topic,
                    host_descriptions=DEFAULT_HOSTS_2,
                    context=context,
                    custom="",
                    max_tokens=None,  # 用户要求不限制 token 预算；脚本长度由主题复杂度决定
                )
                self.after(0, lambda: self._on_llm_success(segments))
            except Exception as e:
                err = str(e)
                self.after(0, lambda msg=err: self._on_llm_failure(msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_llm_success(self, segments):
        self.gen_btn.configure(state="normal", text="🎵 生成台词")
        self.progress.set(1.0)
        self.load_segments_into_editor(segments, source="minimax_llm")

    def _on_llm_failure(self, err):
        self.gen_btn.configure(state="normal", text="🎵 生成台词")
        self.progress.set(0)
        self._log(f"[!] MiniMax LLM 失败: {err}")
        self.set_status(f"❌ MiniMax LLM 失败: {err[:200]}")

    def load_segments_into_editor(self, segments, source=None, fallback_text=None):
        """共用入口：把 segments 灌进 script_editor + 切 tab + 启用按钮 + 写日志 + 写状态。

        Args:
            segments: list of {"speaker": str, "text": str}
            source: "doubao" / "minimax_llm" / "file" — 决定 speaker 翻译方式（豆包需把
                    voice_id 翻译成 role label；其他来源已是中文角色标签）
            fallback_text: 解析失败时降级为纯文本填入（让用户手动整理）
        """
        # 1) 过滤 head_music/tail_music（豆包特有）
        if segments:
            segments = [s for s in segments
                        if s.get("speaker") not in ("head_music", "tail_music")
                        and s.get("text", "").strip()]

        # 2) 解析失败 + 有 fallback → 降级填文本
        if not segments and fallback_text:
            self.script_editor.configure(state="normal")
            self.script_editor.delete("0.0", "end")
            self.script_editor.insert("0.0", fallback_text)
            self.script_editor.see("0.0")
            self.tabs.set("✏️ 台词编辑")
            self._log(f"[!] 解析失败，已按纯文本填入编辑器（请手动整理）")
            self.set_status("⚠️ 解析失败，请手动整理后生成音频")
            return

        if not segments:
            self.set_status("⚠️ 解析到 0 轮台词")
            return

        # 3) 格式化：【speaker】\ntext\n\n---\n\n...
        # 豆包 speaker 是 voice_id，需要翻译成 role label；MiniMax LLM 已经是中文角色标签
        if source == "doubao":
            voice_to_label = self._get_voice_role_map()
            formatted = []
            for s in segments:
                spk = s.get("speaker", "")
                label = voice_to_label.get(spk, spk)
                formatted.append(f"【{label}】\n{s.get('text', '')}")
        else:
            formatted = [f"【{s.get('speaker', '')}】\n{s.get('text', '')}" for s in segments]

        script_text = "\n\n---\n\n".join(formatted)

        # 4) 写 editor
        self.script_editor.configure(state="normal")
        self.script_editor.delete("0.0", "end")
        self.script_editor.insert("0.0", script_text)
        self.script_editor.see("0.0")

        # 5) 切 tab + 启用「用台词生成音频」按钮 + 暂存
        self.tabs.set("✏️ 台词编辑")
        self.gen_from_script_btn.configure(state="normal")
        self._pending_texts = segments

        # 6) 落盘（best effort）
        try:
            engine = self.input_panel.cred.get_engine() if self.input_panel else ""
            script_path = self._save_script(segments, {
                "script_engine": source or "unknown",
                "tts_engine": engine,
            })
            self._log(f"[💾] 脚本已保存: {script_path}")
        except Exception as e:
            self._log(f"[!] 脚本保存失败（非致命）: {e}")

        # 7) 日志 + 状态
        src_label = {
            "doubao": "豆包",
            "minimax_llm": "MiniMax LLM",
            "file": "文件",
        }.get(source, "")
        self._log(f"[+] 已加载 {len(segments)} 轮台词（{src_label or '来源'}）")
        self.set_status(f"✏️ 预览完成！共 {len(segments)} 轮，请编辑后点击「用台词生成音频」")

    def _log(self, msg):
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {msg}"
        self._log_buffer.append(full_msg)
        self.log.configure(state="normal")
        self.log.insert("end", full_msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        """清空日志"""
        self._log_buffer.clear()
        self.log.configure(state="normal")
        self.log.delete("0.0", "end")
        self.log.configure(state="disabled")

    def _export_log(self):
        """导出日志到文件"""
        import tkinter.filedialog as fd
        import datetime
        if not self._log_buffer:
            self.set_status("⚠️  日志为空，无需导出")
            return
        default_name = f"podcast_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path = fd.asksaveasfilename(
            title="导出日志",
            initialfile=default_name,
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(self._log_buffer))
            self.set_status(f"✅  日志已导出: {path}")
        except Exception as e:
            self.set_status(f"❌  导出失败: {e}")

    def _do_copy(self):
        if self._audio_url:
            self.clipboard_clear()
            self.clipboard_append(self._audio_url)
            self.set_status("✅ 下载链接已复制！")

    async def _apply_intro_outro(self, main_audio_path, minimax_key):
        """应用片头片尾，返回最终文件路径"""
        if not main_audio_path:
            return main_audio_path

        intro_mode, intro_prompt, outro_mode, outro_prompt = (
            self.winfo_toplevel().content.left.intro_outro_row.get_intro_outro()
        )

        self._ui(self._log, f"[*] 片头模式: {intro_mode}, 片尾模式: {outro_mode}")

        if intro_mode == "无" and outro_mode == "无":
            return main_audio_path

        intro_path = None
        outro_path = None

        try:
            from podcast_generator import add_intro_outro
            import minimax_music

            # 生成片头BGM
            if intro_mode == "AI生成" and intro_prompt and minimax_key:
                self._ui(self._log, f"[*] 正在生成片头音乐: {intro_prompt[:30]}...")
                intro_path = minimax_music.generate_bgm_to_file(
                    prompt=intro_prompt,
                    api_key=minimax_key,
                    output_path="output/intro_bgm.mp3",
                )
                self._ui(self._log, f"[+] 片头已生成")
            elif intro_mode == "纯音乐":
                # 使用内置BGM目录下的文件
                bgm_dir = os.path.join(os.path.dirname(__file__), "assets", "bgm")
                candidates = [f for f in os.listdir(bgm_dir) if f.endswith(".mp3")] if os.path.exists(bgm_dir) else []
                if candidates:
                    intro_path = os.path.join(bgm_dir, candidates[0])

            # 生成片尾BGM
            if outro_mode == "AI生成" and outro_prompt and minimax_key:
                self._ui(self._log, f"[*] 正在生成片尾音乐: {outro_prompt[:30]}...")
                outro_path = minimax_music.generate_bgm_to_file(
                    prompt=outro_prompt,
                    api_key=minimax_key,
                    output_path="output/outro_bgm.mp3",
                )
                self._ui(self._log, f"[+] 片尾已生成")
            elif outro_mode == "纯音乐":
                bgm_dir = os.path.join(os.path.dirname(__file__), "assets", "bgm")
                candidates = [f for f in os.listdir(bgm_dir) if f.endswith(".mp3")] if os.path.exists(bgm_dir) else []
                if candidates:
                    outro_path = os.path.join(bgm_dir, candidates[0])

            # 合并
            if intro_path or outro_path:
                self._ui(self._log, "[*] 正在合并片头片尾...")
                final_path = add_intro_outro(
                    main_audio=main_audio_path,
                    intro_path=intro_path,
                    outro_path=outro_path,
                    output_path=main_audio_path.replace(".mp3", "_with_intro_outro.mp3"),
                )
                self._ui(self._log, "[+] 片头片尾合并完成")
                return final_path

        except Exception as e:
            self._ui(self._log, f"[!] 片头片尾处理失败: {e}")
            import traceback
            self._ui(self._log, traceback.format_exc())

        return main_audio_path

    def _on_gen_from_script(self):
        """使用编辑后的台词生成音频。

        优先级：
          1. 编辑器当前内容（永远可用，无需先存 JSON）
          2. 若编辑器为空，尝试加载已保存的 JSON
        """
        edited_text = self.script_editor.get("0.0", "end").strip()
        # placeholder / 真实内容的区分
        if not edited_text or edited_text.startswith("【甲】\n台词正文"):
            # 编辑器还是空的占位符 → 退而求其次加载 JSON
            if self._pending_texts is None:
                loaded = self._load_script_into_editor()
                if not loaded:
                    return
            run_async(self._gen_from_script())
            return
        run_async(self._gen_from_script())

    def _on_save_script_clicked(self):
        """保存当前编辑器内容为 JSON，便于后续直接加载复用。"""
        edited_text = self.script_editor.get("0.0", "end").strip()
        if not edited_text or edited_text.startswith("【甲】\n台词正文"):
            self.set_status("⚠️ 编辑器为空，无可保存内容")
            return
        # 解析成 segments
        from minimax_script_gen import parse_script as _parse
        segments = _parse(edited_text)
        if not segments:
            self.set_status("⚠️ 解析失败，请检查格式（【角色】\\n正文 + --- 分隔）")
            return
        try:
            path = self._save_script(
                segments,
                {"source": "manual", "engine": "manual_edit"},
            )
            self.set_status(f"💾 脚本已保存: {path.name}（{len(segments)} 轮）")
            self._log(f"[💾] 已保存脚本: {path}")
        except Exception as e:
            self.set_status(f"❌ 保存失败: {e}")

    def _load_script_into_editor(self):
        """弹出文件选择框，从保存的 JSON 脚本恢复台词到编辑器。

        Returns:
            True 表示成功加载（编辑器已被填充，可继续生成）；
            False 表示用户取消或加载失败。
        """
        import json
        import tkinter.filedialog as fd

        scripts_dir = self._get_scripts_dir()
        path = fd.askopenfilename(
            title="选择已保存的脚本",
            initialdir=str(scripts_dir),
            filetypes=[("脚本 JSON", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            self.set_status("⚠️  未选择脚本文件")
            return False

        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            self.set_status(f"❌  读取脚本失败: {e}")
            return False

        segments = payload.get("segments") if isinstance(payload, dict) else None
        if not isinstance(segments, list):
            self.set_status("❌  脚本格式无效（缺少 segments）")
            return False

        # 过滤 head_music / tail_music，与 generate() 预览模式保持一致
        filtered = [
            s for s in segments
            if isinstance(s, dict)
            and s.get("speaker") not in ("head_music", "tail_music")
            and s.get("text")
        ]
        if not filtered:
            self.set_status("⚠️  脚本为空或全部为片头片尾标记")
            return False

        # 用中文角色标签显示（如有 voice_id 则翻译，否则保留原 speaker 字段）
        voice_to_label = self._get_voice_role_map()
        formatted = []
        for seg in filtered:
            speaker = seg.get("speaker", "")
            content = seg.get("text", "")
            label = voice_to_label.get(speaker, speaker)
            formatted.append(f"【{label}】\n{content}")
        script_text = "\n\n---\n\n".join(formatted)

        self.script_editor.configure(state="normal")
        self.script_editor.delete("0.0", "end")
        self.script_editor.insert("0.0", script_text)
        self.script_editor.see("0.0")  # 滚到顶部，看到新内容开头

        self._pending_texts = filtered
        self.gen_from_script_btn.configure(state="normal")
        self.tabs.set("✏️ 台词编辑")

        self._log(f"[📂] 已加载脚本: {path}（共 {len(filtered)} 轮）")
        self.set_status(f"📂  已加载脚本（{len(filtered)} 轮），可编辑后生成音频")
        return True

    async def _gen_from_script(self):
        """从编辑的台词生成音频"""
        edited_text = self.script_editor.get("0.0", "end").strip()
        if not edited_text or edited_text.startswith("【甲】\n台词正文"):
            self._ui(self.set_status, "⚠️ 台词为空，请先在「📋 台词输入」tab 粘贴/生成，或在编辑区直接输入")
            return

        self._ui(self.progress.set, 0.05)
        self._ui(self._log, "[*] 使用编辑后的台词生成音频...")
        self._ui(self.set_status, "🔄  生成音频中...")

        try:
            # 解析编辑的台词格式【speaker】\ntext\n\n---\n\n...
            parsed_segments = parse_script_segments(edited_text)

            if not parsed_segments:
                self._ui(self.set_status, "⚠️ 解析台词失败，请检查格式")
                self._ui(self.progress.set, 0)
                return

            self._ui(self._log, f"[*] 解析到 {len(parsed_segments)} 轮台词")

            # 判断引擎
            engine = self.winfo_toplevel().content.left.cred.get_engine()
            # 文件名带时间戳 + UUID 短码：避免并发 / 同一分钟内多次运行覆盖
            import datetime, uuid
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            short = uuid.uuid4().hex[:6]
            # 导出格式（豆包路径暂固定 mp3，仅 MiniMax 支持多格式）
            output_opts = self.get_output_options()
            ext = output_opts.get("output_format", "mp3") if engine == "minimax" else "mp3"
            output_path = os.path.expanduser(
                f"~/Documents/podcast-generator/output/podcast_{stamp}_{short}.{ext}"
            )

            if engine == "minimax":
                # MiniMax TTS
                minimax_key = self.winfo_toplevel().content.left.cred.get_minimax_key()
                if not minimax_key:
                    self._ui(self.set_status, "⚠️ MiniMax API Key 未填写")
                    self._ui(self.progress.set, 0)
                    return

                voice_map = self.winfo_toplevel().content.left.minimax_voice_list.get_voice_map()
                speed_map = self.winfo_toplevel().content.left.minimax_voice_list.get_speed_map()
                model = self.winfo_toplevel().content.left.minimax_voice_list.get_model()
                output_opts = self.get_output_options()
                output_format = output_opts["output_format"]
                self._ui(self._log, f"[*] MiniMax 模式: model={model}, voice_map={voice_map}, speed_map={speed_map}, output_format={output_format}")

                # 显式角色标签 -> 音色映射，避免按"首现顺序"导致性别错乱
                # voice_map 包含用户在 MiniMax 面板配置的所有 N 个音色（2-10 个）
                import minimax_tts
                total_segments = sum(1 for s in parsed_segments if s.get("text", "").strip())
                self._ui(self.set_status, f"🔄 合成中：0/{total_segments} 段...")
                self._ui(self.progress.set, 0.1)

                def _on_seg_done(done, total):
                    self._ui(self.set_status, f"🔄 合成中：{done}/{total} 段...")
                    # 进度条 10%-90% 留给分段合成
                    self._ui(self.progress.set, 0.1 + 0.8 * done / max(total, 1))

                combined_path, order_desc = minimax_tts.synthesize_segments(
                    segments=parsed_segments,
                    voice1_id=voice_map.get(ROLE_LABELS[0], ""),
                    voice2_id=voice_map.get(ROLE_LABELS[1], ""),
                    api_key=minimax_key,
                    speaker_map=voice_map,
                    speed_map=speed_map,
                    progress_callback=_on_seg_done,
                    model=model,
                    output_format=output_format,
                )
                self._ui(self.progress.set, 0.9)
                self._ui(self._log, f"[*] 段落顺序: {order_desc}")
                self._ui(self._log, "[+] 主音频生成完成")

                # 片头片尾处理（AI生成BGM用MiniMax）
                final_path = await self._apply_intro_outro(combined_path, minimax_key)

                # 后处理：音量标准化
                output_opts = self.get_output_options()
                if output_opts["normalize_audio"] and final_path:
                    try:
                        from podcast_generator import normalize_audio
                        self._ui(self._log, "[*] 正在标准化音量...")
                        normalized = normalize_audio(final_path)
                        final_path = normalized
                        self._ui(self._log, "[+] 音量标准化完成")
                    except Exception as e:
                        self._ui(self._log, f"[!] 音量标准化失败: {e}")

                # 后处理：清理临时文件
                if output_opts["cleanup_temp_files"] and final_path:
                    try:
                        from podcast_generator import cleanup_temp_files
                        self._ui(self._log, "[*] 正在清理临时文件...")
                        cleanup_temp_files(final_path)
                        self._ui(self._log, "[+] 临时文件清理完成")
                    except Exception as e:
                        self._ui(self._log, f"[!] 临时文件清理失败: {e}")

                self._ui(self._log, f"[+] 文件: {final_path}")
                self._ui(self.progress.set, 1.0)
                self._ui(self.set_status, f"✅ 完成！")

            elif engine == "voxcpm":
                # 本地 VoxCPM2（Apple Silicon / mlx-audio）
                # 不需要 ref_text：只要 ref_audio（qwen3_wav_path 优先，10-30s 干净片段）
                # 多 speaker 走 speaker_map 路由，每个 speaker 独立 voice + speed
                import voice_library as vl
                import voxcpm_tts

                # 从 VoxCPMVoiceList 拿 speaker→voice + speed 映射
                vl_panel = self.input_panel.voice_library_panel if self.input_panel else None
                vlist = self.input_panel.voxcpm_voice_list if self.input_panel else None

                voice_map = vlist.get_voice_map() if vlist else {}
                speed_map = vlist.get_speed_map() if vlist else {}

                # 兜底：如果 VoxCPMVoiceList 没配置任何 voice，回退到 voice_library_panel 选用 + 第一个候选
                voice_id_fallback = None
                if vl_panel:
                    voice_id_fallback = vl_panel.get_selected_voice_id()
                if not voice_map:
                    candidates = [v for v in vl.list_all() if v.get("qwen3_wav_path") or v.get("wav_path")]
                    if candidates:
                        voice_id_fallback = voice_id_fallback or candidates[0]["voice_id"]

                if not voice_map and not voice_id_fallback:
                    self._ui(self._log, "[!] VoxCPMVoiceList 没配置 voice，且音色库里没有可用 wav_path")
                    self._ui(self.set_status, "⚠️ VoxCPM：先在「音色选择」面板为「主播甲/乙」选 voice")
                    self._ui(self.progress.set, 0)
                    return

                self._ui(self._log, 
                    f"[*] VoxCPM2 模式: voice_map={voice_map}, speed_map={speed_map}, "
                    f"fallback_voice={voice_id_fallback}"
                )

                output_opts = self.get_output_options()
                output_format = output_opts.get("output_format", "mp3")
                ext = output_format if output_format in ("mp3", "wav") else "mp3"
                output_path = os.path.expanduser(
                    f"~/Documents/podcast-generator/output/podcast_{stamp}_{short}.{ext}"
                )

                total_segments = sum(1 for s in parsed_segments if s.get("text", "").strip())
                self._ui(self.set_status, f"🔄 合成中：0/{total_segments} 段...")
                self._ui(self.progress.set, 0.1)

                def _on_seg_done_voxcpm(done, total):
                    self._ui(self.set_status, f"🔄 合成中：{done}/{total} 段...")
                    self._ui(self.progress.set, 0.1 + 0.8 * done / max(total, 1))

                try:
                    combined_path, order_desc = voxcpm_tts.synthesize_segments(
                        segments=parsed_segments,
                        voice_id=voice_id_fallback,
                        speaker_map=voice_map,
                        speed_map=speed_map,
                        progress_callback=_on_seg_done_voxcpm,
                        output_path=output_path,
                    )
                except Exception as e:
                    self._ui(self._log, f"[!] VoxCPM2 合成失败: {e}")
                    self._ui(self.set_status, f"❌ VoxCPM2 合成失败")
                    self._ui(self.progress.set, 0)
                    return

                self._ui(self.progress.set, 0.9)
                self._ui(self._log, f"[*] 段落顺序: {order_desc}")
                self._ui(self._log, "[+] 主音频生成完成")

                # 片头片尾暂跳过（VoxCPM2 路径尚未接 BGM 拼接）
                final_path = combined_path

                output_opts = self.get_output_options()
                if output_opts.get("normalize_audio") and final_path:
                    try:
                        from podcast_generator import normalize_audio
                        self._ui(self._log, "[*] 正在标准化音量...")
                        normalized = normalize_audio(final_path)
                        final_path = normalized
                        self._ui(self._log, "[+] 音量标准化完成")
                    except Exception as e:
                        self._ui(self._log, f"[!] 音量标准化失败: {e}")

                if output_opts.get("cleanup_temp_files") and final_path:
                    try:
                        from podcast_generator import cleanup_temp_files
                        self._ui(self._log, "[*] 正在清理临时文件...")
                        cleanup_temp_files(final_path)
                        self._ui(self._log, "[+] 临时文件清理完成")
                    except Exception as e:
                        self._ui(self._log, f"[!] 临时文件清理失败: {e}")

                self._ui(self._log, f"[+] 文件: {final_path}")
                self._ui(self.progress.set, 1.0)
                self._ui(self.set_status, f"✅ 完成！")

            else:
                # 豆包 TTS
                from podcast_generator import generate_podcast

                appid = self._ui_get(lambda: self.winfo_toplevel().content.left.cred.appid_var.get()).strip()
                token = self._ui_get(lambda: self.winfo_toplevel().content.left.cred.token_var.get()).strip()
                speakers = self.winfo_toplevel().content.left.speaker_row.get_speakers()
                self._ui(self._log, f"[*] 豆包模式: appid={appid[:8]}..., speakers={speakers}")

                # 将角色标签翻译回豆包 voice ID（nlp_texts 的 speaker 字段必须是 voice ID）
                voice_to_label = self._get_voice_role_map()
                role_to_voice = {v: k for k, v in voice_to_label.items()}
                default_voice = speakers[0] if speakers else ""

                doubao_segments = []
                for seg in parsed_segments:
                    speaker = seg.get("speaker", "")
                    text = seg.get("text", "")
                    voice_id = role_to_voice.get(speaker, default_voice)
                    doubao_segments.append({"speaker": voice_id, "text": text})

                result = await generate_podcast(
                    appid=appid,
                    token=token,
                    output_path=output_path,
                    speakers=speakers,
                    only_nlp_text=False,
                    nlp_texts=doubao_segments,
                )

                audio_url = result.get("audio_url")
                result_path = result.get("output_path")

                self._ui(self._log, "[+] 主音频生成完成")

                # 片头片尾处理（AI生成BGM始终用MiniMax）
                minimax_key = self.winfo_toplevel().content.left.cred.get_minimax_key()
                final_path = await self._apply_intro_outro(result_path, minimax_key)

                # 后处理：音量标准化
                output_opts = self.get_output_options()
                if output_opts["normalize_audio"] and final_path:
                    try:
                        from podcast_generator import normalize_audio
                        self._ui(self._log, "[*] 正在标准化音量...")
                        normalized = normalize_audio(final_path)
                        final_path = normalized
                        self._ui(self._log, "[+] 音量标准化完成")
                    except Exception as e:
                        self._ui(self._log, f"[!] 音量标准化失败: {e}")

                # 后处理：清理临时文件
                if output_opts["cleanup_temp_files"] and final_path:
                    try:
                        from podcast_generator import cleanup_temp_files
                        self._ui(self._log, "[*] 正在清理临时文件...")
                        cleanup_temp_files(final_path)
                        self._ui(self._log, "[+] 临时文件清理完成")
                    except Exception as e:
                        self._ui(self._log, f"[!] 临时文件清理失败: {e}")

                self._ui(self.progress.set, 1.0)
                if final_path:
                    self._ui(self._log, f"[+] 文件: {final_path}")
                if audio_url:
                    self._ui(self._log, f"[+] 下载: {audio_url}")
                    self._audio_url = audio_url
                    self.copy_btn.configure(state="normal")
                self._ui(self.set_status, f"✅ 完成！")

        except Exception as e:
            import traceback
            self._ui(self._log, f"[!] 错误: {e}")
            self._ui(self._log, traceback.format_exc())
            self._ui(self.progress.set, 0)
            self._ui(self.set_status, f"❌ 失败: {e}")

    async def generate(self, url=None, text=None, only_nlp_text=False):
        self._ui(self.progress.set, 0.05)
        self._ui(self._log, "[*] 连接豆包播客 API...")

        # 防重名：时间戳 + UUID 短码
        import datetime, uuid
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        short = uuid.uuid4().hex[:6]

        try:
            from podcast_generator import generate_podcast

            # 取凭证
            try:
                appid = self._ui_get(lambda: self.winfo_toplevel().content.left.cred.appid_var.get()).strip()
                token = self._ui_get(lambda: self.winfo_toplevel().content.left.cred.token_var.get()).strip()
                speakers = self.winfo_toplevel().content.left.speaker_row.get_speakers()
                self._ui(self._log, f"[*] 凭证: appid={appid[:8]}..., speakers={speakers}")
            except Exception as e:
                self._ui(self._log, f"[!] 取凭证失败: {e}")
                self._ui(self.set_status, "⚠️  获取凭证失败，请检查输入")
                self._ui(self.progress.set, 0)
                return

            result = await generate_podcast(
                file_url=url,
                text=text,
                appid=appid,
                token=token,
                output_path=os.path.expanduser(
                    f"~/Documents/podcast-generator/output/podcast_{stamp}_{short}.mp3"
                ),
                speakers=speakers,
                only_nlp_text=only_nlp_text,
            )

            audio_url = result.get("audio_url")
            output_path = result.get("output_path")
            texts = result.get("texts", [])

            self._ui(self.progress.set, 1.0)

            if only_nlp_text:
                # 预览模式：走共用 load_segments_into_editor（与 MiniMax LLM 同一条路）
                self._ui(self._log, "[+] 预览完成！")
                self._ui(self.load_segments_into_editor, texts, source="doubao")
                # 显式复位 gen_btn（豆包预览完成 set_status 以 ✏️ 开头，
                # 不触发 _on_status_change 的自动重置）
                self._ui(self.gen_btn.configure, state="normal", text="🎵 生成台词")
            else:
                # 直接生成模式
                self._ui(self._log, "[+] 生成完成！")
                if output_path:
                    self._ui(self._log, f"[+] 文件: {output_path}")
                if audio_url:
                    self._ui(self._log, f"[+] 下载: {audio_url}")
                    self._audio_url = audio_url
                    self.copy_btn.configure(state="normal")
                self._ui(self.set_status, f"✅ 完成！共 {len(texts)} 轮")
                self._ui(self.gen_btn.configure, state="normal", text="🎵 生成台词")

        except Exception as e:
            import traceback
            self._ui(self._log, f"[!] 错误: {e}")
            self._ui(self._log, traceback.format_exc())
            self._ui(self.progress.set, 0)
            self._ui(self.set_status, f"❌ 失败: {e}")
