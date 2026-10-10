# 播客生成器代码质量改进设计

> **Goal:** 将硬编码配置分离、统一错误处理、优化日志系统

**Architecture:** 新增配置加载、异常、日志三个独立模块，所有核心模块引用统一入口。

---

## 1. 配置分离

### 1.1 config.json

```json
{
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
```

### 1.2 config.py

```python
import json
import os
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"

class Config:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r") as f:
                self._data = json.load(f)
        else:
            self._data = {}

    def get(self, *keys, default=None):
        val = self._data
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default
            if val is None:
                return default
        return val

    @property
    def volcengine(self):
        return self._data.get("volcengine", {})

    @property
    def minimax(self):
        return self._data.get("minimax", {})

    @property
    def audio(self):
        return self._data.get("audio", {})

CONFIG = Config()
```

---

## 2. 统一异常类

### 2.1 exceptions.py

```python
class PodcastGeneratorError(Exception):
    """播客生成器基础异常"""
    def __init__(self, message, trace_id=None):
        self.trace_id = trace_id
        super().__init__(message)

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
```

---

## 3. 统一日志

### 3.1 logger.py

```python
import logging
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

def setup_logger(name, level=logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(handler)

    return logger

# 模块级 logger
PodcastTTS = setup_logger("PodcastTTS")
MiniMax = setup_logger("MiniMax")
Audio = setup_logger("Audio")
```

---

## 4. 文件结构

```
podcast-generator/
├── config.json              # [NEW] 配置文件
├── config.py                # [NEW] 配置加载
├── exceptions.py           # [NEW] 统一异常
├── logger.py               # [NEW] 统一日志
├── podcast_generator.py     # [MOD] 使用新模块
├── minimax_tts.py          # [MOD] 使用新模块
├── minimax_music.py         # [MOD] 使用新模块
├── podcast_gui.py          # [MOD] 使用新模块
└── docs/superpowers/specs/
    └── 2026-04-09-code-quality-improvement.md
```

---

## 5. 实现顺序

1. `config.py` + `config.json`
2. `exceptions.py`
3. `logger.py`
4. 更新 `podcast_generator.py`
5. 更新 `minimax_tts.py`
6. 更新 `minimax_music.py`
7. 更新 `podcast_gui.py`
8. 测试运行

---

## 6. 兼容性

- 配置缺失时使用环境变量或空字符串
- 向后兼容：旧的硬编码值作为 fallback
