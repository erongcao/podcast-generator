# GUI 拆分重构与音频后处理实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 1137 行 GUI 拆分为独立模块，实现音量标准化和临时文件清理功能

**Architecture:** 创建 gui/ 包，包含 panels/ 子包和 theme.py，通过 Panel 注册表模式管理面板

**Tech Stack:** Python 3, customtkinter, ffmpeg, 标准库

---

## 文件结构

```
podcast-generator/
├── gui/                      # [NEW] GUI 包
│   ├── __init__.py
│   ├── main_window.py        # [NEW] GhibliApp 主窗口
│   ├── panels/               # [NEW] 面板子包
│   │   ├── __init__.py
│   │   ├── input.py         # [NEW] InputPanel
│   │   ├── intro_outro.py    # [NEW] IntroOutroPanel
│   │   ├── speakers.py       # [NEW] SpeakerSelectionPanel
│   │   ├── credentials.py    # [NEW] CredentialPanel
│   │   └── output.py         # [NEW] OutputPanel
│   └── theme.py              # [NEW] GHIBLI 配色和常量
├── podcast_generator.py       # [MOD] 添加 normalize_audio(), cleanup_temp_files()
├── config.py                  # [MOD] 添加 post_processing 配置
├── config.json                # [MOD] 添加 post_processing 选项
└── podcast_gui.py             # [DEL] 删除（被 gui/main_window.py 替代）
```

**注意:** 项目无现有测试文件，本计划跳过 TDD 步骤，直接实现后手动验证。

---

## Phase 0: 配置更新

### Task 0: 更新 config.json 添加后处理选项

**Files:**
- Modify: `/Users/yirongcao/Documents/podcast-generator/config.json`

- [ ] **Step 1: 更新 config.json 添加 post_processing**

在 config.json 中添加:
```json
{
  "post_processing": {
    "normalize_audio": true,
    "cleanup_temp_files": true
  }
}
```

**注意:** 使用 JSON merge 方式，只添加 post_processing 字段，保留现有配置

- [ ] **Step 2: 验证**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from config import CONFIG; print(f'normalize={CONFIG.get(\"post_processing\", \"normalize_audio\")}')"`

- [ ] **Step 3: 提交**

```bash
git add config.json
git commit -m "feat: add post_processing config options"
```

---

## Phase 1: GUI 拆分

### Task 1: 创建 gui/ 目录结构

**Files:**
- Create: `/Users/yirongcao/Documents/podcast-generator/gui/__init__.py`
- Create: `/Users/yirongcao/Documents/podcast-generator/gui/panels/__init__.py`

- [ ] **Step 1: 创建目录和 __init__.py 文件**

Run:
```bash
mkdir -p /Users/yirongcao/Documents/podcast-generator/gui/panels
touch /Users/yirongcao/Documents/podcast-generator/gui/__init__.py
touch /Users/yirongcao/Documents/podcast-generator/gui/panels/__init__.py
```

- [ ] **Step 2: 提交**

```bash
git add gui/__init__.py gui/panels/__init__.py
git commit -m "feat: create gui package structure"
```

---

### Task 2: 创建 theme.py 共享主题

**Files:**
- Create: `/Users/yirongcao/Documents/podcast-generator/gui/theme.py`

- [ ] **Step 1: 从 podcast_gui.py 提取 GHIBLI 配色**

从 `podcast_gui.py` 第 13-31 行提取 GHIBLI 字典，创建 `theme.py`:

```python
#!/usr/bin/env python3
"""
GUI 主题配置
"""

# 吉卜力配色
GHIBLI = {
    "sky": "#B8D4E8",
    "sky_dark": "#8BB8D4",
    "forest": "#8DB48E",
    "forest_dark": "#6A9A70",
    "grass": "#A8C4A0",
    "cream": "#F5F0E1",
    "cream_dark": "#E8DCC8",
    "wood": "#8B7355",
    "wood_dark": "#6B5A47",
    "totoro": "#E07B54",
    "totoro_dark": "#C45D3A",
    "dust": "#D4C4A8",
    "text": "#4A4A4A",
    "text_light": "#7A7A7A",
    "white": "#FEFEFE",
    "shadow": "#D0C4A8",
}
```

- [ ] **Step 2: 提交**

```bash
git add gui/theme.py
git commit -m "feat: extract GHIBLI theme to gui/theme.py"
```

---

### Task 3: 拆分 CredentialPanel

**Files:**
- Create: `/Users/yirongcao/Documents/podcast-generator/gui/panels/credentials.py`
- Modify: `/Users/yirongcao/Documents/podcast-generator/podcast_gui.py:408-475`

**需要从 podcast_gui.py 提取并修改:**
- CredentialPanel 类（第 408-475 行附近）
- 使用 `CONFIG.get_minimax("api_key")` 替代硬编码 key
- 添加 `get_credentials()` 和 `get_minimax_key()` 方法

- [ ] **Step 1: 阅读 podcast_gui.py 找到 CredentialPanel**

Run: `grep -n "class CredentialPanel" /Users/yirongcao/Documents/podcast-generator/podcast_gui.py`

- [ ] **Step 2: 创建 credentials.py**

```python
#!/usr/bin/env python3
"""
凭证输入面板
"""

import customtkinter as ctk
from config import CONFIG


class CredentialPanel(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=GHIBLI["cream_dark"])

        self.appid_var = ctk.StringVar()
        self.token_var = ctk.StringVar()
        self.minimax_key_var = ctk.StringVar(value=CONFIG.get_minimax("api_key", ""))

        # Layout
        self._create_widgets()

    def _create_widgets(self):
        # MiniMax API Key
        ctk.CTkLabel(self, text="MiniMax API Key（始终显示，用于BGM合成）",
                    text_color=GHIBLI["text"]).grid(row=0, column=0, sticky="w", padx=12, pady=(8, 0))
        self.minimax_key_entry = ctk.CTkEntry(
            self, textvariable=self.minimax_key_var, width=400,
            placeholder_text="MiniMax API Key（用于AI生成BGM，必填）",
            show="*"
        )
        self.minimax_key_entry.grid(row=1, column=0, sticky="ew", padx=12, pady=(2, 8))

        # Volcengine credentials
        ctk.CTkLabel(self, text="火山引擎凭证（豆包播客模式）",
                    text_color=GHIBLI["text"]).grid(row=2, column=0, sticky="w", padx=12, pady=(8, 0))

        ctk.CTkLabel(self, text="APP ID", text_color=GHIBLI["text_light"]).grid(row=3, column=0, sticky="w", padx=12, pady=(4, 0))
        self.appid_entry = ctk.CTkEntry(self, textvariable=self.appid_var, width=400)
        self.appid_entry.grid(row=4, column=0, sticky="ew", padx=12, pady=(2, 4))

        ctk.CTkLabel(self, text="Access Token", text_color=GHIBLI["text_light"]).grid(row=5, column=0, sticky="w", padx=12, pady=(4, 0))
        self.token_entry = ctk.CTkEntry(self, textvariable=self.token_var, width=400, show="*")
        self.token_entry.grid(row=6, column=0, sticky="ew", padx=12, pady=(2, 8))

        self.grid_columnconfigure(0, weight=1)

    def get_credentials(self):
        return {
            "appid": self.appid_var.get().strip(),
            "token": self.token_var.get().strip(),
        }

    def get_minimax_key(self):
        return self.minimax_key_var.get().strip()
```

**注意:** 需要从原 podcast_gui.py 复制完整的 UI 代码和布局

- [ ] **Step 3: 验证导入**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from gui.panels.credentials import CredentialPanel; print('OK')"`

- [ ] **Step 4: 提交**

```bash
git add gui/panels/credentials.py
git commit -m "feat: extract CredentialPanel to gui/panels/credentials.py"
```

---

### Task 4: 拆分 SpeakerSelectionPanel

**Files:**
- Create: `/Users/yirongcao/Documents/podcast-generator/gui/panels/speakers.py`

- [ ] **Step 1: 阅读 podcast_gui.py 找到 SpeakerSelectionPanel**

Run: `grep -n "class.*Panel\|MALE_SPEAKERS\|FEMALE_SPEAKERS\|MINIMAX_VOICES" /Users/yirongcao/Documents/podcast-generator/podcast_gui.py | head -20`

- [ ] **Step 2: 创建 speakers.py**

从 podcast_gui.py 提取音色选择相关的类和方法，包括:
- `MALE_SPEAKERS`, `FEMALE_SPEAKERS`, `MINIMAX_VOICES` 常量
- `SpeakerSelectionPanel` 类
- `MiniMaxVoicePanel` 类

添加 `get_speakers()` 和 `get_voices()` 方法返回选中值

- [ ] **Step 3: 验证导入**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from gui.panels.speakers import SpeakerSelectionPanel; print('OK')"`

- [ ] **Step 4: 提交**

```bash
git add gui/panels/speakers.py
git commit -m "feat: extract SpeakerSelectionPanel to gui/panels/speakers.py"
```

---

### Task 5: 拆分 IntroOutroPanel

**Files:**
- Create: `/Users/yirongcao/Documents/podcast-generator/gui/panels/intro_outro.py`

- [ ] **Step 1: 阅读 podcast_gui.py 找到 IntroOutroPanel**

Run: `grep -n "class IntroOutroPanel\|intro_mode_var\|outro_mode_var" /Users/yirongcao/Documents/podcast-generator/podcast_gui.py | head -10`

- [ ] **Step 2: 创建 intro_outro.py**

从 podcast_gui.py 第 633-722 行附近提取 `IntroOutroPanel` 类

添加 `get_intro_outro()` 方法返回:
```python
{
    "intro_mode": "无/AI生成/纯音乐",
    "intro_prompt": "...",
    "outro_mode": "无/AI生成/纯音乐",
    "outro_prompt": "...",
}
```

- [ ] **Step 3: 验证导入**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from gui.panels.intro_outro import IntroOutroPanel; print('OK')"`

- [ ] **Step 4: 提交**

```bash
git add gui/panels/intro_outro.py
git commit -m "feat: extract IntroOutroPanel to gui/panels/intro_outro.py"
```

---

### Task 6: 拆分 InputPanel

**Files:**
- Create: `/Users/yirongcao/Documents/podcast-generator/gui/panels/input.py`

- [ ] **Step 1: 阅读 podcast_gui.py 找到 InputPanel**

Run: `grep -n "class InputPanel" /Users/yirongcao/Documents/podcast-generator/podcast_gui.py`

- [ ] **Step 2: 创建 input.py**

从 podcast_gui.py 提取 `InputPanel` 类，包括:
- URL/文本输入
- 生成按钮
- 预览按钮

添加 `get_input()` 方法返回:
```python
{
    "input_mode": "url/text",
    "url": "...",
    "text": "...",
}
```

- [ ] **Step 3: 验证导入**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from gui.panels.input import InputPanel; print('OK')"`

- [ ] **Step 4: 提交**

```bash
git add gui/panels/input.py
git commit -m "feat: extract InputPanel to gui/panels/input.py"
```

---

### Task 7: 拆分 OutputPanel（包含后处理选项）

**Files:**
- Create: `/Users/yirongcao/Documents/podcast-generator/gui/panels/output.py`

- [ ] **Step 1: 阅读 podcast_gui.py 找到 OutputPanel**

Run: `grep -n "class OutputPanel" /Users/yirongcao/Documents/podcast-generator/podcast_gui.py`

- [ ] **Step 2: 创建 output.py**

从 podcast_gui.py 提取 `OutputPanel` 类，**并添加新选项:**

```python
class OutputPanel(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=GHIBLI["cream_dark"])

        # 后处理选项 - 从配置加载
        self.normalize_var = ctk.BooleanVar(
            value=CONFIG.get("post_processing", "normalize_audio", default=True)
        )
        self.cleanup_var = ctk.BooleanVar(
            value=CONFIG.get("post_processing", "cleanup_temp_files", default=True)
        )

        # 绑定变量变化自动保存
        self.normalize_var.trace_add("write", lambda *_: self._save_config())
        self.cleanup_var.trace_add("write", lambda *_: self._save_config())

        self._create_widgets()

    def _create_widgets(self):
        # ... existing widgets from OutputPanel ...

        # 后处理选项
        ctk.CTkLabel(self, text="后处理", text_color=GHIBLI["text"]).grid(row=7, column=0, sticky="w", padx=12, pady=(8, 0))

        self.normalize_cb = ctk.CTkCheckBox(
            self, text="音量标准化 (loudnorm)",
            variable=self.normalize_var
        )
        self.normalize_cb.grid(row=8, column=0, sticky="w", padx=12, pady=2)

        self.cleanup_cb = ctk.CTkCheckBox(
            self, text="清理临时文件",
            variable=self.cleanup_var
        )
        self.cleanup_cb.grid(row=9, column=0, sticky="w", padx=12, pady=2)

    def _save_config(self):
        """保存选项到配置"""
        import json
        config_path = Path(__file__).parent.parent.parent / "config.json"
        if config_path.exists():
            config_data = json.loads(config_path.read_text())
        else:
            config_data = {}
        config_data.setdefault("post_processing", {})
        config_data["post_processing"]["normalize_audio"] = self.normalize_var.get()
        config_data["post_processing"]["cleanup_temp_files"] = self.cleanup_var.get()
        config_path.write_text(json.dumps(config_data, indent=2))

    def get_output_options(self):
        return {
            "output_dir": self.output_dir_var.get(),
            "normalize_audio": self.normalize_var.get(),
            "cleanup_temp_files": self.cleanup_var.get(),
        }
```

- [ ] **Step 3: 验证导入**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from gui.panels.output import OutputPanel; print('OK')"`

- [ ] **Step 4: 提交**

```bash
git add gui/panels/output.py
git commit -m "feat: extract OutputPanel with post-processing options"
```

---

### Task 8: 创建 main_window.py 整合面板

**Files:**
- Create: `/Users/yirongcao/Documents/podcast-generator/gui/main_window.py`

- [ ] **Step 1: 阅读原 podcast_gui.py 的 GhibliApp.__init__**

Run: `grep -n "class GhibliApp\|def __init__" /Users/yirongcao/Documents/podcast-generator/podcast_gui.py | head -20`

- [ ] **Step 2: 创建 main_window.py**

```python
#!/usr/bin/env python3
"""
播客生成器主窗口
"""

import asyncio
import threading
import customtkinter as ctk

from gui.theme import GHIBLI
from gui.panels.input import InputPanel
from gui.panels.intro_outro import IntroOutroPanel
from gui.panels.speakers import SpeakerSelectionPanel, MiniMaxVoicePanel
from gui.panels.credentials import CredentialPanel
from gui.panels.output import OutputPanel


class GhibliApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("豆包播客生成器 - 吉卜力风格")
        self.geometry("900x700")

        # Panel registry
        self.panels = {}

        self._create_ui()

    def _create_ui(self):
        # 主容器
        self.content = ctk.CTkFrame(self, fg_color=GHIBLI["cream"])
        self.content.pack(fill="both", expand=True)

        # 左右布局
        self.content.left = ctk.CTkFrame(self.content, fg_color=GHIBLI["cream"])
        self.content.left.pack(side="left", fill="both", expand=True, padx=8, pady=8)

        self.content.right = ctk.CTkFrame(self.content, fg_color=GHIBLI["cream_dark"])
        self.content.right.pack(side="right", fill="both", expand=True, padx=(0, 8), pady=8)

        # 创建面板
        self.panels["input"] = InputPanel(self.content.left)
        self.panels["input"].grid(row=0, column=0, sticky="ew", padx=12, pady=(0, 8))

        self.panels["intro_outro"] = IntroOutroPanel(self.content.left)
        self.panels["intro_outro"].grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))

        self.panels["speakers"] = SpeakerSelectionPanel(self.content.left)
        self.panels["speakers"].grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))

        self.panels["credentials"] = CredentialPanel(self.content.left)
        self.panels["credentials"].grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 8))

        self.panels["output"] = OutputPanel(self.content.right)
        self.panels["output"].pack(fill="both", expand=True, padx=12, pady=8)

    def run(self):
        self.mainloop()
```

**完整实现需要从原 podcast_gui.py 提取:**
- `generate()` 方法
- `_gen_from_script()` 方法
- `_apply_intro_outro()` 方法
- 所有事件处理和回调
- 日志显示
- 进度条
- 下载链接复制按钮

- [ ] **Step 3: 验证导入**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from gui.main_window import GhibliApp; print('OK')"`

- [ ] **Step 4: 提交**

```bash
git add gui/main_window.py
git commit -m "feat: create main_window.py integrating all panels"
```

---

### Task 9: 删除旧 podcast_gui.py

**Files:**
- Delete: `/Users/yirongcao/Documents/podcast-generator/podcast_gui.py`

- [ ] **Step 1: 验证新 GUI 可以导入所有依赖**

Run:
```bash
cd /Users/yirongcao/Documents/podcast-generator
python3 -c "
from gui.main_window import GhibliApp
from gui.panels.input import InputPanel
from gui.panels.intro_outro import IntroOutroPanel
from gui.panels.speakers import SpeakerSelectionPanel
from gui.panels.credentials import CredentialPanel
from gui.panels.output import OutputPanel
print('All imports OK')
"
```

- [ ] **Step 2: 删除旧文件**

```bash
rm /Users/yirongcao/Documents/podcast-generator/podcast_gui.py
```

- [ ] **Step 3: 提交**

```bash
git rm podcast_gui.py
git commit -m "refactor: delete old podcast_gui.py, replaced by gui package"
```

---

### Task 10: Phase 1 整体验证

- [ ] **Step 1: 验证 GUI 可以启动（无 display 环境可能失败）**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from gui.main_window import GhibliApp; print('GUI module OK')"`

- [ ] **Step 2: 验证所有模块导入**

Run:
```bash
cd /Users/yirongcao/Documents/podcast-generator
python3 -c "
import gui.panels.input
import gui.panels.intro_outro
import gui.panels.speakers
import gui.panels.credentials
import gui.panels.output
import gui.main_window
print('All GUI modules OK')
"
```

---

## Phase 2: 音频后处理

### Task 11: 添加 normalize_audio() 函数

**Files:**
- Modify: `/Users/yirongcao/Documents/podcast-generator/podcast_generator.py`

- [ ] **Step 1: 在 podcast_generator.py 末尾添加函数**

```python
def normalize_audio(input_path: str, output_path: str = None) -> str:
    """
    使用 ffmpeg loudnorm 标准化音量
    Returns: 标准化后文件路径
    Raises: AudioProcessingError if ffmpeg fails
    """
    import shutil
    import subprocess

    if not shutil.which('ffmpeg'):
        raise AudioProcessingError("ffmpeg not found in PATH")

    if output_path is None:
        output_path = input_path.replace(".mp3", "_normalized.mp3")

    result = subprocess.run([
        'ffmpeg', '-y', '-i', input_path,
        '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11',
        '-acodec', 'libmp3lame',
        output_path
    ], capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        raise AudioProcessingError(f"ffmpeg loudnorm failed: {result.stderr}")

    return output_path
```

- [ ] **Step 2: 添加 AudioProcessingError 导入**

确认 `from exceptions import APIError, AudioProcessingError` 已存在

- [ ] **Step 3: 验证**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from podcast_generator import normalize_audio; print('OK')"`

- [ ] **Step 4: 提交**

```bash
git add podcast_generator.py
git commit -m "feat: add normalize_audio function with ffmpeg loudnorm"
```

---

### Task 12: 集成音量标准化到 GUI

**Files:**
- Modify: `/Users/yirongcao/Documents/podcast-generator/gui/main_window.py`

- [ ] **Step 1: 在 _gen_from_script 或 generate() 中添加后处理调用**

在主音频生成完成后，检查 OutputPanel 的 `normalize_var` 选项，如果为 True 则调用 `normalize_audio()`

伪代码:
```python
# 生成主音频后
output_options = self.panels["output"].get_output_options()
main_audio_path = result["output_path"]

if output_options["normalize_audio"] and main_audio_path:
    normalized = normalize_audio(main_audio_path)
    final_path = normalized
else:
    final_path = main_audio_path
```

- [ ] **Step 2: 验证**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from gui.main_window import GhibliApp; print('OK')"`

- [ ] **Step 3: 提交**

```bash
git add gui/main_window.py
git commit -m "feat: integrate volume normalization in GUI workflow"
```

---

## Phase 3: 临时文件清理

### Task 13: 添加 cleanup_temp_files() 函数

**Files:**
- Modify: `/Users/yirongcao/Documents/podcast-generator/podcast_generator.py`

- [ ] **Step 1: 在 podcast_generator.py 末尾添加函数**

```python
def cleanup_temp_files(final_output_path: str = None):
    """
    清理临时文件

    Args:
        final_output_path: 最终输出文件路径（保留）
    """
    import re

    if final_output_path is None:
        final_output_path = "output/podcast_final.mp3"

    final_name = Path(final_output_path).name
    temp_indicators = [
        r'_\d+\.mp3$',             # chunk files: _0.mp3, _1.mp3
        r'_\d+\.txt$',             # concat lists: _0.txt, _1.txt
        r'intro_bgm\.mp3$',
        r'outro_bgm\.mp3$',
    ]

    output_dir = Path("output")
    for f in output_dir.iterdir():
        if f.name == final_name:
            continue
        if any(re.search(pattern, f.name) for pattern in temp_indicators):
            f.unlink(missing_ok=True)
```

- [ ] **Step 2: 验证**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from podcast_generator import cleanup_temp_files; print('OK')"`

- [ ] **Step 3: 提交**

```bash
git add podcast_generator.py
git commit -m "feat: add cleanup_temp_files function"
```

---

### Task 14: 集成临时文件清理到 GUI

**Files:**
- Modify: `/Users/yirongcao/Documents/podcast-generator/gui/main_window.py`

- [ ] **Step 1: 在生成流程完成后调用 cleanup**

在 `normalize_audio` 处理之后，检查 `cleanup_var` 选项，如果为 True 则调用 `cleanup_temp_files(final_output_path)`

- [ ] **Step 2: 验证**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from gui.main_window import GhibliApp; print('OK')"`

- [ ] **Step 3: 提交**

```bash
git add gui/main_window.py
git commit -m "feat: integrate temp file cleanup in GUI workflow"
```

---

## 整体验证

### Task 15: 最终验证

- [ ] **Step 1: 验证所有模块导入**

```bash
cd /Users/yirongcao/Documents/podcast-generator
python3 -c "
from config import CONFIG
from exceptions import APIError, AudioProcessingError
from logger import PodcastTTS, MiniMax, Audio
from gui.main_window import GhibliApp
from gui.panels.input import InputPanel
from gui.panels.intro_outro import IntroOutroPanel
from gui.panels.speakers import SpeakerSelectionPanel
from gui.panels.credentials import CredentialPanel
from gui.panels.output import OutputPanel
from podcast_generator import normalize_audio, cleanup_temp_files
print('All modules imported successfully')
"
```

- [ ] **Step 2: 验证 config.json 包含 post_processing**

```bash
cat /Users/yirongcao/Documents/podcast-generator/config.json
```

预期输出包含:
```json
{
  "post_processing": {
    "normalize_audio": true,
    "cleanup_temp_files": true
  }
}
```

- [ ] **Step 3: Git 状态检查**

```bash
git status
git log --oneline -15
```

---

## 验证检查清单

- [ ] gui/ 目录结构正确创建
- [ ] gui/theme.py 包含 GHIBLI 配色
- [ ] gui/panels/credentials.py 提取完成
- [ ] gui/panels/speakers.py 提取完成
- [ ] gui/panels/intro_outro.py 提取完成
- [ ] gui/panels/input.py 提取完成
- [ ] gui/panels/output.py 提取完成（含后处理选项）
- [ ] gui/main_window.py 整合所有面板
- [ ] 旧 podcast_gui.py 已删除
- [ ] normalize_audio() 已实现
- [ ] cleanup_temp_files() 已实现
- [ ] GUI 工作流集成后处理选项
- [ ] 所有导入无错误
