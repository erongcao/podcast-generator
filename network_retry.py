"""
网络请求重试装饰器：指数退避 + 抖动
"""
import logging
import random
import time
from functools import wraps

import requests


def retry_on_transient(
    max_attempts=3,
    base_delay=1.0,
    max_delay=10.0,
    backoff_factor=2.0,
    jitter=0.1,
    retryable_exceptions=(requests.ConnectionError, requests.Timeout),
    retryable_status=(429, 500, 502, 503, 504),
):
    """
    装饰器：transient 网络错误自动重试。
    - max_attempts: 总尝试次数（含首次）
    - base_delay: 首次重试前的等待秒数
    - max_delay: 单次重试最大等待
    - backoff_factor: 每次等待的乘数（指数退避）
    - jitter: 随机抖动比例（防止 thundering herd）
    - retryable_exceptions: 触发重试的异常
    - retryable_status: 触发重试的 HTTP status code
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger = logging.getLogger(func.__module__ or "network_retry")
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    resp = func(*args, **kwargs)
                    if resp.status_code in retryable_status:
                        if attempt < max_attempts:
                            delay = min(base_delay * (backoff_factor ** (attempt - 1)), max_delay)
                            delay *= (1 + random.uniform(-jitter, jitter))
                            logger.warning(
                                f"[retry] {func.__name__} attempt {attempt}/{max_attempts} "
                                f"got {resp.status_code}, retrying in {delay:.2f}s"
                            )
                            time.sleep(delay)
                            continue
                        else:
                            # 最后一次也失败，raise 出去由调用方处理
                            return resp
                    return resp
                except retryable_exceptions as e:
                    last_exc = e
                    if attempt < max_attempts:
                        delay = min(base_delay * (backoff_factor ** (attempt - 1)), max_delay)
                        delay *= (1 + random.uniform(-jitter, jitter))
                        logger.warning(
                            f"[retry] {func.__name__} attempt {attempt}/{max_attempts} "
                            f"raised {type(e).__name__}, retrying in {delay:.2f}s"
                        )
                        time.sleep(delay)
                    else:
                        raise
            # Should not reach here
            raise last_exc if last_exc else RuntimeError("retry decorator: unreachable")
        return wrapper
    return decorator