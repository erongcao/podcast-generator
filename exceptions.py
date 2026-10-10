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


class VoiceCloneError(PodcastGeneratorError):
    """MiniMax 音色克隆 API 错误"""

    def __init__(self, message, status_code=None, trace_id=None):
        self.status_code = status_code
        super().__init__(message, trace_id)


class LibraryError(PodcastGeneratorError):
    """音色库文件 I/O 或数据错误"""
    pass


class RecorderError(PodcastGeneratorError):
    """麦克风录音错误"""
    pass