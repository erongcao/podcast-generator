#!/usr/bin/env python3
"""
片头片尾设置面板
"""

import customtkinter as ctk
from gui.theme import GHIBLI


class IntroOutroPanel(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, height=90, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self, text="🎶 片头音乐",
            font=ctk.CTkFont("Helvetica Neue", 11, "bold"),
            text_color=GHIBLI["text"],
        ).grid(row=0, column=0, sticky="w", padx=(10, 5), pady=(6, 3))

        ctk.CTkLabel(
            self, text="🎶 片尾音乐",
            font=ctk.CTkFont("Helvetica Neue", 11, "bold"),
            text_color=GHIBLI["text"],
        ).grid(row=0, column=1, sticky="w", padx=(5, 10), pady=(6, 3))

        # 片头模式选择
        self.intro_mode_var = ctk.StringVar(value="none")
        self.intro_menu = ctk.CTkOptionMenu(
            self, variable=self.intro_mode_var,
            values=["无", "纯音乐", "AI生成"],
            fg_color=GHIBLI["cream"], button_color=GHIBLI["grass"],
            text_color=GHIBLI["text"], dropdown_fg_color=GHIBLI["white"],
            font=ctk.CTkFont("Helvetica Neue", 10),
            corner_radius=6, height=24,
            command=self._on_intro_mode_change,
        )
        self.intro_menu.grid(row=1, column=0, sticky="ew", padx=(10, 5), pady=(0, 3))

        # 片头 AI prompt（AI生成时显示）
        self.intro_prompt_var = ctk.StringVar()
        self.intro_prompt = ctk.CTkEntry(
            self, textvariable=self.intro_prompt_var,
            placeholder_text="AI音乐描述，如：calm piano",
            fg_color=GHIBLI["cream"], border_color=GHIBLI["dust"],
            font=ctk.CTkFont("Helvetica Neue", 9),
            corner_radius=6, height=22,
        )
        self.intro_prompt.grid(row=2, column=0, sticky="ew", padx=(10, 5), pady=(0, 6))
        self.intro_prompt.grid_remove()

        # 片尾模式选择
        self.outro_mode_var = ctk.StringVar(value="none")
        self.outro_menu = ctk.CTkOptionMenu(
            self, variable=self.outro_mode_var,
            values=["无", "纯音乐", "AI生成"],
            fg_color=GHIBLI["cream"], button_color=GHIBLI["grass"],
            text_color=GHIBLI["text"], dropdown_fg_color=GHIBLI["white"],
            font=ctk.CTkFont("Helvetica Neue", 10),
            corner_radius=6, height=24,
            command=self._on_outro_mode_change,
        )
        self.outro_menu.grid(row=1, column=1, sticky="ew", padx=(5, 10), pady=(0, 3))

        # 片尾 AI prompt（AI生成时显示）
        self.outro_prompt_var = ctk.StringVar()
        self.outro_prompt = ctk.CTkEntry(
            self, textvariable=self.outro_prompt_var,
            placeholder_text="AI音乐描述，如：peaceful ending",
            fg_color=GHIBLI["cream"], border_color=GHIBLI["dust"],
            font=ctk.CTkFont("Helvetica Neue", 9),
            corner_radius=6, height=22,
        )
        self.outro_prompt.grid(row=2, column=1, sticky="ew", padx=(5, 10), pady=(0, 6))
        self.outro_prompt.grid_remove()

    def _on_intro_mode_change(self, value):
        if value == "AI生成":
            self.intro_prompt.grid()
        else:
            self.intro_prompt.grid_remove()

    def _on_outro_mode_change(self, value):
        if value == "AI生成":
            self.outro_prompt.grid()
        else:
            self.outro_prompt.grid_remove()

    def get_intro_outro(self):
        """返回 (intro_mode, intro_prompt, outro_mode, outro_prompt)"""
        return (
            self.intro_mode_var.get(),
            self.intro_prompt_var.get().strip(),
            self.outro_mode_var.get(),
            self.outro_prompt_var.get().strip(),
        )