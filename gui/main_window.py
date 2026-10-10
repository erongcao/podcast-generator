#!/usr/bin/env python3
"""
主窗口 - 整合所有面板
"""

import asyncio
import os
import sys
import threading

import customtkinter as ctk

from gui.theme import GHIBLI
from gui.panels.input import InputPanel
from gui.panels.output import OutputPanel
from gui.env_check import EnvReport, run_startup_check


# ============== 标题栏 ==============

class HeaderFrame(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, height=72, **kwargs)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 绿色条纹装饰（用 CTkFrame 而不是 CTkCanvas —— 后者在 macOS 27 / Tcl-Tk 9
        # 下偶发 Tk_FreeGC + ConfigurePolygon 段错误，crash report 实测确认）
        stripe = ctk.CTkFrame(self, height=6, fg_color=GHIBLI["forest_dark"], corner_radius=0)
        stripe.grid(row=0, column=0, sticky="ew")
        stripe.grid_propagate(False)

        # 主标题
        ctk.CTkLabel(
            self, text="  豆包播客生成器",
            font=ctk.CTkFont("Helvetica Neue", 24, "bold"),
            text_color=GHIBLI["white"],
            fg_color=GHIBLI["forest"],
        ).grid(row=1, column=0, pady=(10, 4))

        # 副标题
        ctk.CTkLabel(
            self, text="上传文档 · AI 生成双人播客音频",
            font=ctk.CTkFont("Helvetica Neue", 13),
            text_color=GHIBLI["cream"],
            fg_color=GHIBLI["forest"],
        ).grid(row=2, column=0, pady=(0, 10))


# ============== 主内容 ==============

class MainContent(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # 左：输入
        self.left = InputPanel(self, fg_color=GHIBLI["white"],
                               border_color=GHIBLI["grass"],
                               border_width=2, corner_radius=12)
        self.left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        # 右：输出（注入 input_panel 引用，便于读音色库选中状态）
        self.right = OutputPanel(self, input_panel=self.left,
                                 fg_color=GHIBLI["white"],
                                 border_color=GHIBLI["sky_dark"],
                                 border_width=2, corner_radius=12)
        self.right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))


# ============== 底部栏 ==============

class FooterFrame(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, height=30, **kwargs)
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text="吉卜力风格 · 豆包播客生成器 · Claude Code",
            font=ctk.CTkFont("Helvetica Neue", 10),
            text_color=GHIBLI["text_light"],
        ).grid(row=0, column=0, sticky="w", padx=15, pady=5)


# ============== 环境提示条 ==============

class EnvBanner(ctk.CTkFrame):
    """显示当前 Python 的 TTS 引擎可用性；按 env_report.engines 渲染。

    设计意图：让用户不用进 GUI 也能立刻看到"我选的引擎当前能不能跑"。
    不可用的引擎用 🚫 + 灰色显示，可用的用 ✅。
    """

    def __init__(self, parent, env_report: EnvReport, **kwargs):
        super().__init__(parent, fg_color=GHIBLI["cream_dark"], **kwargs)

        # 单行标签
        parts = []
        for eng in env_report.engines:
            mark = "✅" if eng.available else "🚫"
            parts.append(f"{mark} {eng.key}")
        label = " · ".join(parts)
        if not env_report.warnings:
            label = "引擎就绪 · " + label

        self._label_var = ctk.StringVar(value=label)
        ctk.CTkLabel(
            self,
            textvariable=self._label_var,
            font=ctk.CTkFont("Helvetica Neue", 11),
            text_color=GHIBLI["text"],
            anchor="w",
            cursor="hand2",
        ).pack(side="left", padx=15, pady=4, fill="x", expand=True)

        # 不可用的引擎展开后给出修复提示（点一下 toggle）
        self._detail_visible = False
        self._detail_text: ctk.CTkTextbox | None = None
        if env_report.warnings or not env_report.ok:
            self._detail_text = ctk.CTkTextbox(
                self, height=80, fg_color=GHIBLI["cream"],
                text_color=GHIBLI["text"],
                font=ctk.CTkFont("Menlo", 10),
                wrap="word",
            )
            # 默认隐藏；点 banner 才展开
            self._detail_text.pack_forget()
            detail = env_report.render()
            self._detail_text.insert("1.0", detail)
            self._detail_text.configure(state="disabled")
            # 点击 banner 切换显示 —— 只绑 self 和 label（不递归子节点，避免误绑 CTkTextbox 等）
            self.bind("<Button-1>", self._toggle_detail)
            # label 是第一个 child（pack side="left"），单独 bind
            for w in self.winfo_children():
                if isinstance(w, ctk.CTkLabel):
                    w.bind("<Button-1>", self._toggle_detail)
                    break

    def _toggle_detail(self, _evt=None):
        if self._detail_text is None:
            return
        self._detail_visible = not self._detail_visible
        if self._detail_visible:
            self._detail_text.pack(side="bottom", fill="x", padx=8, pady=(0, 4))
        else:
            self._detail_text.pack_forget()


# ============== 主窗口 ==============

class GhibliApp(ctk.CTk):
    def __init__(self, env_report: EnvReport):
        super().__init__()
        self.env_report = env_report
        self.title("豆包播客生成器 · Ghibli Style")
        self.geometry("900x720")
        self.minsize(700, 480)
        self.configure(bg_color=GHIBLI["cream"])

        self.update_idletasks()
        x = (self.winfo_screenwidth() // 2) - 450
        y = (self.winfo_screenheight() // 2) - 360
        self.geometry(f"+{x}+{y}")

        # 布局：env banner / header / scroll / footer
        self.grid_rowconfigure(0, weight=0)   # env banner
        self.grid_rowconfigure(1, weight=0)   # header
        self.grid_rowconfigure(2, weight=1)   # scroll
        self.grid_rowconfigure(3, weight=0)   # footer
        self.grid_columnconfigure(0, weight=1)

        # 启动检查条（点击可展开看修复指引）
        self.env_banner = EnvBanner(self, env_report, height=28)
        self.env_banner.grid(row=0, column=0, sticky="ew")

        # 顶部（固定）
        self.header = HeaderFrame(self, fg_color=GHIBLI["forest"])
        self.header.grid(row=1, column=0, sticky="ew")

        # 中间滚动容器 —— 内容超长时窗口右侧出现滚动条
        self._scroll = ctk.CTkScrollableFrame(self, fg_color=GHIBLI["cream"])
        self._scroll.grid(row=2, column=0, sticky="nsew", padx=20, pady=0)

        # 主内容放在滚动容器里
        self.content = MainContent(self._scroll, fg_color=GHIBLI["cream"])
        self.content.pack(fill="both", expand=True, padx=0, pady=12)

        # 底部（固定）
        self.footer = FooterFrame(self, fg_color=GHIBLI["cream_dark"])
        self.footer.grid(row=3, column=0, sticky="ew")

        self.protocol("WM_DELETE_WINDOW", self.destroy)


# ============== 启动 ==============

if __name__ == "__main__":
    # 启动期环境自检：必需依赖（ctk/requests/soundfile）缺失则直接弹错误并退出，
    # 避免进 GUI 之后用户点了引擎才发现跑不起来。
    env_report = run_startup_check()
    if not env_report.ok:
        msg = env_report.render()
        print(msg, file=sys.stderr)
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Podcast Generator · 启动检查失败", msg)
            root.destroy()
        except Exception:
            pass
        sys.exit(2)
    app = GhibliApp(env_report)
    app.mainloop()

