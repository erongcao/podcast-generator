#!/usr/bin/env python3
"""
统一日志模块
所有日志统一从此模块获取

输出到两个地方：
  - stdout（保留现有行为，方便调试）
  - ~/Library/Logs/PodcastGenerator/podcast-YYYY-MM-DD.log
    （macOS 路径；Linux 改 XDG）
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_LEVEL = logging.INFO

LOG_DIR = Path.home() / "Library" / "Logs" / "PodcastGenerator"
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
LOG_FILE_BACKUP_COUNT = 3  # 保留 3 个旧文件


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
        formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

        # stdout handler
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        # 文件 handler（带 rotation）
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            from datetime import datetime
            log_path = LOG_DIR / f"podcast-{datetime.now().strftime('%Y-%m-%d')}.log"
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=LOG_FILE_MAX_BYTES,
                backupCount=LOG_FILE_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except (OSError, PermissionError):
            # 文件日志失败时仍允许 stdout 工作
            pass

    return logger


# 模块级 logger 实例
PodcastTTS = setup_logger("PodcastTTS")
MiniMax = setup_logger("MiniMax")
Audio = setup_logger("Audio")
General = setup_logger("General")