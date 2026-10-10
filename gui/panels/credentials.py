#!/usr/bin/env python3
"""
凭证输入面板
"""

import json
import os
import tempfile
import threading
from pathlib import Path

import customtkinter as ctk

from gui.theme import GHIBLI
from config import CONFIG

# 配置保存锁，防止并发写入
_config_lock = threading.Lock()


def _atomic_write_json(path: Path, data: dict):
    """原子写入 JSON 文件"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, dir=path.parent) as f:
        json.dump(data, f, indent=2)
        temp_path = f.name
    os.replace(temp_path, path)


class CredentialRow(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, height=90, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0)

        # 引擎切换（grid 布局，2 列：豆包+MiniMax 一行，VoxCPM 一行，避免裁切）
        self.engine_var = ctk.StringVar(value="doubao")
        engine_frame = ctk.CTkFrame(self, fg_color="transparent")
        engine_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(6, 4))
        engine_frame.grid_columnconfigure(0, weight=1)
        engine_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkRadioButton(
            engine_frame, text="🎙️ 豆包",
            variable=self.engine_var, value="doubao",
            fg_color=GHIBLI["grass"], text_color=GHIBLI["text"],
            font=ctk.CTkFont("Helvetica Neue", 11, "bold"),
            command=self._on_engine_change,
        ).grid(row=0, column=0, sticky="w", padx=(0, 4))

        ctk.CTkRadioButton(
            engine_frame, text="🎵 MiniMax",
            variable=self.engine_var, value="minimax",
            fg_color=GHIBLI["grass"], text_color=GHIBLI["text"],
            font=ctk.CTkFont("Helvetica Neue", 11, "bold"),
            command=self._on_engine_change,
        ).grid(row=0, column=1, sticky="w", padx=(4, 0))

        ctk.CTkRadioButton(
            engine_frame, text="🖥️ 本地 VoxCPM",
            variable=self.engine_var, value="voxcpm",
            fg_color=GHIBLI["grass"], text_color=GHIBLI["text"],
            font=ctk.CTkFont("Helvetica Neue", 11, "bold"),
            command=self._on_engine_change,
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=(0, 0), pady=(4, 0))

        # 台词生成引擎（与 TTS 引擎解耦，单独选择）
        script_engine_frame = ctk.CTkFrame(self, fg_color="transparent")
        script_engine_frame.grid(row=0, column=2, sticky="ew", padx=(10, 5), pady=(6, 4))
        ctk.CTkLabel(
            script_engine_frame, text="台词生成:",
            font=ctk.CTkFont("Helvetica Neue", 10),
            text_color=GHIBLI["text_light"], anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.script_engine_var = ctk.StringVar(value="doubao")
        ctk.CTkRadioButton(
            script_engine_frame, text="豆包",
            variable=self.script_engine_var, value="doubao",
            fg_color=GHIBLI["grass"], text_color=GHIBLI["text"],
            font=ctk.CTkFont("Helvetica Neue", 10),
        ).grid(row=1, column=0, sticky="w", padx=(0, 8))
        ctk.CTkRadioButton(
            script_engine_frame, text="MiniMax LLM",
            variable=self.script_engine_var, value="minimax_llm",
            fg_color=GHIBLI["grass"], text_color=GHIBLI["text"],
            font=ctk.CTkFont("Helvetica Neue", 10),
        ).grid(row=1, column=1, sticky="w")

        # MiniMax API Key（始终显示，用于TTS + BGM + AI生成台词）
        self.minimax_key_var = ctk.StringVar(value=CONFIG.get_minimax("api_key", ""))
        self.minimax_key_entry = ctk.CTkEntry(
            self, textvariable=self.minimax_key_var,
            placeholder_text="MiniMax API Key（用于TTS / BGM / AI生成台词）",
            fg_color=GHIBLI["cream"], border_color=GHIBLI["dust"],
            font=ctk.CTkFont("Helvetica Neue", 11),
            corner_radius=6, height=28,
        )
        self.minimax_key_entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(10, 5), pady=(0, 4))

        # 豆包凭证
        self.appid_var = ctk.StringVar(value=CONFIG.get_volcengine("app_id", ""))
        self.token_var = ctk.StringVar(value=CONFIG.get_volcengine("access_token", ""))

        self.doubao_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.doubao_frame.grid(row=2, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 4))
        self.doubao_frame.grid_columnconfigure(0, weight=1)
        self.doubao_frame.grid_columnconfigure(1, weight=1)
        self.doubao_frame.grid_columnconfigure(2, weight=0)

        self.doubao_appid_entry = ctk.CTkEntry(
            self.doubao_frame, textvariable=self.appid_var,
            placeholder_text="豆包 APP ID",
            fg_color=GHIBLI["cream"], border_color=GHIBLI["dust"],
            font=ctk.CTkFont("Helvetica Neue", 11),
            corner_radius=6, height=28,
        )
        self.doubao_appid_entry.grid(row=0, column=0, sticky="ew", padx=(2, 4), pady=(0, 4))

        self.doubao_token_entry = ctk.CTkEntry(
            self.doubao_frame, textvariable=self.token_var,
            placeholder_text="豆包 Access Token",
            fg_color=GHIBLI["cream"], border_color=GHIBLI["dust"],
            font=ctk.CTkFont("Helvetica Neue", 11),
            corner_radius=6, height=28, show="*",
        )
        self.doubao_token_entry.grid(row=0, column=1, sticky="ew", padx=(4, 4), pady=(0, 4))

        ctk.CTkButton(
            self.doubao_frame, text="📁", width=32, height=28,
            fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
            text_color=GHIBLI["white"], corner_radius=6,
            command=lambda: os.system("open ~/Documents/podcast-generator/output"),
        ).grid(row=0, column=2, sticky="e", padx=(4, 2), pady=(0, 4))

        self._on_engine_change()

        # 自动保存凭证到 config.json
        self.minimax_key_var.trace_add("write", lambda *_: self._save_credentials())
        self.appid_var.trace_add("write", lambda *_: self._save_credentials())
        self.token_var.trace_add("write", lambda *_: self._save_credentials())

    def _save_credentials(self):
        """保存凭证到配置文件（线程安全）"""
        config_path = Path(__file__).parent.parent.parent / "config.json"
        with _config_lock:
            if config_path.exists():
                config_data = json.loads(config_path.read_text())
            else:
                config_data = {}
            config_data.setdefault("volcengine", {})
            config_data.setdefault("minimax", {})
            config_data["volcengine"]["app_id"] = self.appid_var.get().strip()
            config_data["volcengine"]["access_token"] = self.token_var.get().strip()
            config_data["minimax"]["api_key"] = self.minimax_key_var.get().strip()
            _atomic_write_json(config_path, config_data)

    def _on_engine_change(self):
        engine = self.engine_var.get()
        # 豆包凭证：只在 doubao 引擎下启用
        doubao_state = "normal" if engine == "doubao" else "disabled"
        self.doubao_appid_entry.configure(state=doubao_state)
        self.doubao_token_entry.configure(state=doubao_state)
        # MiniMax API Key：始终启用——既用于 TTS / BGM，也用于脚本生成（LLM）
        minimax_state = "normal"
        self.minimax_key_entry.configure(state=minimax_state)

    def get_engine(self):
        return self.engine_var.get()

    def get_minimax_key(self):
        return self.minimax_key_var.get().strip()

    # ----- 台词生成引擎（与 TTS 引擎解耦）-----
    # 用户可独立选择「用什么 LLM 生成台词」：豆包（action=0 长文总结）
    # 或 MiniMax M3（用户自己的 Token Plan 订阅，提示词完全可控、医学风格更好）。
    # 选哪个都不会影响 TTS 引擎——TTS 仍由上方 engine_var 决定。

    def get_script_engine(self):
        return getattr(self, "script_engine_var", ctk.StringVar(value="doubao")).get()

    def get_credentials(self):
        return {
            "appid": self.appid_var.get().strip(),
            "token": self.token_var.get().strip(),
        }
