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
