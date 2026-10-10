# 代码质量改进实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将硬编码配置分离到 config.json，统一错误处理和日志系统

**Architecture:** 新增 config.py、exceptions.py、logger.py 三个独立模块，所有核心模块统一引用。保持向后兼容。

**Tech Stack:** Python 3, 标准库 (json, logging, pathlib), 现有依赖不变

---

## 文件结构

```
podcast-generator/
├── config.json              # [NEW] 配置文件
├── config.py                # [NEW] 配置加载模块 (Singleton)
├── exceptions.py           # [NEW] 统一异常类
├── logger.py               # [NEW] 统一日志模块
├── podcast_generator.py     # [MOD] 替换硬编码配置
├── minimax_tts.py          # [MOD] 替换硬编码配置
├── minimax_music.py         # [MOD] 替换硬编码配置
├── podcast_gui.py          # [MOD] 替换硬编码配置
└── docs/superpowers/plans/
    └── 2026-04-09-code-quality-improvement.md
```

**注意:** 项目目前无测试文件，本计划跳过 TDD 步骤，直接实现后手动验证。

---

## 实现任务

### Task 1: 创建 config.py 配置加载模块

**Files:**
- Create: `/Users/yirongcao/Documents/podcast-generator/config.py`

- [ ] **Step 1: 创建 config.py**

```python
#!/usr/bin/env python3
"""
配置加载模块
所有配置统一从此模块获取
"""

import json
import os
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"


class Config:
    """单例配置类"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        """加载配置文件"""
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        else:
            self._data = {}
            self._create_default_config()

    def _create_default_config(self):
        """创建默认配置文件"""
        default_config = {
            "volcengine": {
                "app_id": "",
                "access_token": "",
                "resource_id": "volc.service_type.10050",
                "app_key": "",
                "wss_url": "wss://openspeech.bytedance.com/api/v3/sami/podcasttts"
            },
            "minimax": {
                "api_key": "",
                "api_base": "https://api.minimaxi.com"
            },
            "audio": {
                "sample_rate": 24000,
                "format": "mp3",
                "bitrate": 128000
            },
            "output": {
                "dir": "output"
            }
        }
        self._data = default_config
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=2, ensure_ascii=False)

    def get(self, *keys, default=None):
        """按层级获取配置值"""
        val = self._data
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default
            if val is None:
                return default
        return val

    def get_volcengine(self, key, default=None):
        return self.get("volcengine", key, default=default)

    def get_minimax(self, key, default=None):
        return self.get("minimax", key, default=default)

    def get_audio(self, key, default=None):
        return self.get("audio", key, default=default)

    def get_output(self, key, default=None):
        return self.get("output", key, default=default)


CONFIG = Config()
```

- [ ] **Step 2: 验证模块可导入**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from config import CONFIG; print('OK')"`
Expected: 输出 "OK"，config.json 被创建

- [ ] **Step 3: 提交**

```bash
git add config.py
git commit -m "feat: add config.py configuration loader"
```

---

### Task 2: 创建 exceptions.py 统一异常类

**Files:**
- Create: `/Users/yirongcao/Documents/podcast-generator/exceptions.py`

- [ ] **Step 1: 创建 exceptions.py**

```python
#!/usr/bin/env python3
"""
统一异常类
所有播客生成器的异常统一使用此模块
"""


class PodcastGeneratorError(Exception):
    """播客生成器基础异常"""

    def __init__(self, message, trace_id=None):
        self.trace_id = trace_id
        super().__init__(message)

    def __str__(self):
        if self.trace_id:
            return f"{super().__str__()} (trace_id={self.trace_id})"
        return super().__str__()


class APIError(PodcastGeneratorError):
    """API 调用错误"""

    def __init__(self, provider, message, trace_id=None):
        self.provider = provider
        super().__init__(f"[{provider}] {message}", trace_id)


class AudioProcessingError(PodcastGeneratorError):
    """音频处理错误"""
    pass


class ConfigError(PodcastGeneratorError):
    """配置错误"""
    pass


class ValidationError(PodcastGeneratorError):
    """参数验证错误"""
    pass
```

- [ ] **Step 2: 验证模块可导入**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from exceptions import APIError, AudioProcessingError; e = APIError('Test', 'msg', '123'); print(str(e))"`
Expected: `[Test] msg (trace_id=123)`

- [ ] **Step 3: 提交**

```bash
git add exceptions.py
git commit -m "feat: add unified exception classes"
```

---

### Task 3: 创建 logger.py 统一日志模块

**Files:**
- Create: `/Users/yirongcao/Documents/podcast-generator/logger.py`

- [ ] **Step 1: 创建 logger.py**

```python
#!/usr/bin/env python3
"""
统一日志模块
所有日志统一从此模块获取
"""

import logging
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_LEVEL = logging.INFO


def setup_logger(name):
    """
    创建并返回指定名称的 logger

    Args:
        name: logger 名称

    Returns:
        logging.Logger 实例
    """
    logger = logging.getLogger(name)
    logger.setLevel(LOG_LEVEL)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(handler)

    return logger


# 模块级 logger 实例
PodcastTTS = setup_logger("PodcastTTS")
MiniMax = setup_logger("MiniMax")
Audio = setup_logger("Audio")
General = setup_logger("General")
```

- [ ] **Step 2: 验证模块可导入**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from logger import PodcastTTS, MiniMax; PodcastTTS.info('Test message'); print('OK')"`
Expected: `2026-04-09 HH:MM:SS [INFO] PodcastTTS: Test message`

- [ ] **Step 3: 提交**

```bash
git add logger.py
git commit -m "feat: add unified logger module"
```

---

### Task 4: 更新 minimax_music.py 使用新模块

**Files:**
- Modify: `/Users/yirongcao/Documents/podcast-generator/minimax_music.py:1-81`

**变更内容:**
- 移除硬编码的 `DEFAULT_MODEL` 和 `url`
- 导入 `CONFIG` 获取 API 配置
- 导入 `logger` 和 `APIError`
- 错误处理使用 `APIError` 并记录 trace_id

- [ ] **Step 1: 更新 import 和常量定义**

原文件开头:
```python
import binascii
import os
import requests

DEFAULT_MODEL = "music-2.5+"
```

更新为:
```python
import binascii
import os
import requests

from config import CONFIG
from exceptions import APIError
from logger import MiniMax

DEFAULT_MODEL = "music-2.5+"
```

- [ ] **Step 2: 更新 generate_bgm 函数中的 url 获取**

原:
```python
url = "https://api.minimaxi.com/v1/music_generation"
headers = {
    "Authorization": f"Bearer {api_key}",
    ...
}
```

更新为:
```python
api_base = CONFIG.get_minimax("api_base", "https://api.minimaxi.com")
url = f"{api_base}/v1/music_generation"
headers = {
    "Authorization": f"Bearer {api_key}",
    ...
}
```

- [ ] **Step 3: 更新错误处理**

原:
```python
base_resp = data.get("base_resp", {})
if base_resp.get("status_code") != 0:
    raise RuntimeError(f"MiniMax Music API error: {base_resp.get('status_msg')}")
```

更新为:
```python
base_resp = data.get("base_resp", {})
trace_id = data.get("trace_id")
if base_resp.get("status_code") != 0:
    MiniMax.error(f"Music API error: {base_resp.get('status_msg')} (trace_id={trace_id})")
    raise APIError("MiniMax", base_resp.get("status_msg"), trace_id)
```

- [ ] **Step 4: 验证**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "import minimax_music; print('OK')"`
Expected: 无错误输出

- [ ] **Step 5: 提交**

```bash
git add minimax_music.py
git commit -m "refactor: use CONFIG, logger and APIError in minimax_music"
```

---

### Task 5: 更新 minimax_tts.py 使用新模块

**Files:**
- Modify: `/Users/yirongcao/Documents/podcast-generator/minimax_tts.py:1-192`

**变更内容:**
- 移除硬编码的 `DEFAULT_MODEL` 和 API URL
- 导入 `CONFIG`, `logger`, `APIError`
- 错误处理使用 `APIError` 并记录 trace_id

- [ ] **Step 1: 更新 import 和常量定义**

原:
```python
import binascii
import os
import subprocess
import tempfile
from typing import List, Tuple
import requests

DEFAULT_MODEL = "speech-2.8-hd"
DEFAULT_SAMPLE_RATE = 32000
DEFAULT_FORMAT = "mp3"
```

更新为:
```python
import binascii
import os
import subprocess
import tempfile
from typing import List, Tuple
import requests

from config import CONFIG
from exceptions import APIError
from logger import MiniMax

DEFAULT_MODEL = "speech-2.8-hd"
DEFAULT_SAMPLE_RATE = 32000
DEFAULT_FORMAT = "mp3"
```

- [ ] **Step 2: 更新 synthesize 函数中的 url 获取**

原:
```python
url = "https://api.minimaxi.com/v1/t2a_v2"
```

更新为:
```python
api_base = CONFIG.get_minimax("api_base", "https://api.minimaxi.com")
url = f"{api_base}/v1/t2a_v2"
```

- [ ] **Step 3: 更新错误处理**

原:
```python
if data.get("base_resp", {}).get("status_code") != 0:
    raise RuntimeError(f"MiniMax API error: {data.get('base_resp', {}).get('status_msg')}")
```

更新为:
```python
base_resp = data.get("base_resp", {})
trace_id = data.get("trace_id")
if base_resp.get("status_code") != 0:
    MiniMax.error(f"TTS API error: {base_resp.get('status_msg')} (trace_id={trace_id})")
    raise APIError("MiniMax", base_resp.get("status_msg"), trace_id)
```

- [ ] **Step 4: 验证**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "import minimax_tts; print('OK')"`
Expected: 无错误输出

- [ ] **Step 5: 提交**

```bash
git add minimax_tts.py
git commit -m "refactor: use CONFIG, logger and APIError in minimax_tts"
```

---

### Task 6: 更新 podcast_generator.py 使用新模块

**Files:**
- Modify: `/Users/yirongcao/Documents/podcast-generator/podcast_generator.py:1-635`

**变更内容:**
- 移除顶部的硬编码配置常量
- 导入 `CONFIG`, `logger`, `APIError`, `AudioProcessingError`
- 使用 `CONFIG` 获取 volcengine 配置
- 错误处理使用 `APIError` 并记录 trace_id
- 将 `print()` 替换为 `logger`

- [ ] **Step 1: 更新 import 和配置部分**

移除:
```python
DEFAULT_APP_ID = "2672263369"          # ← REDACTED in public repo
DEFAULT_ACCESS_TOKEN = ""              # ← REDACTED in public repo
RESOURCE_ID = "volc.service_type.10050"
APP_KEY = ""                            # ← REDACTED in public repo
WSS_URL = "wss://openspeech.bytedance.com/api/v3/sami/podcasttts"
```

替换为:
```python
from config import CONFIG
from exceptions import APIError, AudioProcessingError
from logger import PodcastTTS

# 从配置获取，如果没有则使用空字符串（CLI 需要显式传入）
DEFAULT_APP_ID = ""
DEFAULT_ACCESS_TOKEN = ""
```

- [ ] **Step 2: 更新 generate_podcast 函数中的配置获取**

原:
```python
appid = appid or DEFAULT_APP_ID
token = token or DEFAULT_ACCESS_TOKEN
```

更新为:
```python
appid = appid or CONFIG.get_volcengine("app_id", "")
token = token or CONFIG.get_volcengine("access_token", "")
resource_id = CONFIG.get_volcengine("resource_id", "volc.service_type.10050")
app_key = CONFIG.get_volcengine("app_key", "")
wss_url = CONFIG.get_volcengine("wss_url", "wss://openspeech.bytedance.com/api/v3/sami/podcasttts")
```

- [ ] **Step 3: 更新 headers**

原:
```python
headers = {
    "X-Api-App-Id": appid,
    "X-Api-Access-Key": token,
    "X-Api-Resource-Id": RESOURCE_ID,
    "X-Api-App-Key": APP_KEY,
}
```

更新为:
```python
headers = {
    "X-Api-App-Id": appid,
    "X-Api-Access-Key": token,
    "X-Api-Resource-Id": resource_id,
    "X-Api-App-Key": app_key,
}
```

- [ ] **Step 4: 替换 logger.info/print 为统一 logger**

原多处:
```python
logger.info(f"[*] Session: {session_id}")
```

更新为:
```python
PodcastTTS.info(f"Session: {session_id}")
```

原多处:
```python
print("[!] 请提供 --url 或 --text 参数")
```

更新为:
```python
PodcastTTS.error("请提供 --url 或 --text 参数")
```

- [ ] **Step 5: 验证**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from podcast_generator import generate_podcast; print('OK')"`
Expected: 无错误输出

- [ ] **Step 6: 提交**

```bash
git add podcast_generator.py
git commit -m "refactor: use CONFIG, logger and APIError in podcast_generator"
```

---

### Task 7: 更新 podcast_gui.py 使用新模块

**Files:**
- Modify: `/Users/yirongcao/Documents/podcast-generator/podcast_gui.py`

**变更内容:**
- 移除顶部硬编码的 `GHIBLI` 配色（保留在 GUI 中）
- 移除硬编码的 MiniMax API key
- 导入 `CONFIG` 从配置获取 API key
- 导入 `logger` 用于日志
- 保留 GUI 特定日志方法 `_log`

- [ ] **Step 1: 添加 import**

在文件开头添加:
```python
from config import CONFIG
from logger import General
```

- [ ] **Step 2: 更新 MiniMax API key 获取**

找到第 413 行左右:
```python
self.minimax_key_var = ctk.StringVar(value=os.environ.get("MINIMAX_API_KEY", ""))  # ← REDACTED in public repo
```

替换为:
```python
self.minimax_key_var = ctk.StringVar(value=CONFIG.get_minimax("api_key", ""))
```

- [ ] **Step 3: 更新日志调用**

将 `self._log()` 保留（GUI 特有），在其他地方添加 `General` logger 调用用于调试。

- [ ] **Step 4: 验证**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from podcast_gui import GhibliApp; print('OK')"`
Expected: 无错误输出（需要 display 环境变量，可能报 tkinter 错误，可忽略）

- [ ] **Step 5: 提交**

```bash
git add podcast_gui.py
git commit -m "refactor: use CONFIG in podcast_gui"
```

---

### Task 8: 创建初始 config.json

**Files:**
- Create: `/Users/yirongcao/Documents/podcast-generator/config.json`

- [ ] **Step 1: 运行 config.py 生成 config.json**

Run: `cd /Users/yirongcao/Documents/podcast-generator && python3 -c "from config import CONFIG; print('Config initialized')"`
Expected: config.json 被创建，输出 "Config initialized"

- [ ] **Step 2: 编辑 config.json 填入你的凭证**

```json
{
  "volcengine": {
    "app_id": "2672263369",
    "access_token": "<VOLCENGINE_ACCESS_TOKEN>",
    "resource_id": "volc.service_type.10050",
    "app_key": "<VOLCENGINE_APP_KEY>",
    "wss_url": "wss://openspeech.bytedance.com/api/v3/sami/podcasttts"
  },
  "minimax": {
    "api_key": "<MINIMAX_API_KEY>",
    "api_base": "https://api.minimaxi.com"
  },
  "audio": {
    "sample_rate": 24000,
    "format": "mp3",
    "bitrate": 128000
  },
  "output": {
    "dir": "output"
  }
}
```

- [ ] **Step 3: 提交 config.json（可选，包含占位符）**

如果 config.json 已在 .gitignore 中则跳过

```bash
git add config.json
git commit -m "chore: add config.json template"
```

---

### Task 9: 整体验证

- [ ] **Step 1: 验证所有模块可导入**

Run:
```bash
cd /Users/yirongcao/Documents/podcast-generator
python3 -c "
from config import CONFIG
from exceptions import APIError, AudioProcessingError, ConfigError
from logger import PodcastTTS, MiniMax, Audio, General
print('All modules imported successfully')
"
```

- [ ] **Step 2: 验证配置加载**

Run:
```bash
cd /Users/yirongcao/Documents/podcast-generator
python3 -c "
from config import CONFIG
print(f'Volcengine app_id: {CONFIG.get_volcengine(\"app_id\", \"NOT SET\")[:8]}...')
print(f'MiniMax api_key: {CONFIG.get_minimax(\"api_key\", \"NOT SET\")[:8]}...')
print(f'Audio sample_rate: {CONFIG.get_audio(\"sample_rate\")}')
"
```

- [ ] **Step 3: 提交所有变更**

```bash
git status
git diff --stat
```

---

## 验证检查清单

- [ ] config.py 创建并可导入
- [ ] exceptions.py 创建并可导入
- [ ] logger.py 创建并可导入
- [ ] config.json 被创建
- [ ] minimax_music.py 更新完成
- [ ] minimax_tts.py 更新完成
- [ ] podcast_generator.py 更新完成
- [ ] podcast_gui.py 更新完成
- [ ] 所有 import 无错误
- [ ] 日志格式统一
- [ ] 错误处理使用统一异常类
