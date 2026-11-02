"""
MiniMax LLM 脚本生成必须走流式（2026-10-26 事故）

事故：非流式 POST 的总超时 240s，把"没有轮数上限 + thinking"的长脚本
（实测 4-10 分钟）拦腰砍断 —— 用户连续 3 次生成失败全是这个。

流式把超时语义从「总时长」变成「停滞检测」：只要 token 还在流就让它跑完，
60 秒无新数据才判死。这里用假 SSE 流钉死：
  - 多 chunk 的 content 被完整拼起来
  - reasoning_content 在 content 没有 <think> 时会被包回去（parser 依赖）
  - 非 200 / 中途停滞都要抛 APIError（GUI 靠它显示红色错误行）
"""
import json

import pytest
import requests

import minimax_script_gen
from exceptions import APIError
from minimax_script_gen import MiniMaxScriptGenerator


class _FakeStreamResp:
    def __init__(self, lines, status_code=200, text=""):
        self._lines = lines
        self.status_code = status_code
        self.text = text

    def iter_lines(self, decode_unicode=False):
        return iter(self._lines)

    def close(self):
        pass


def _sse(content=None, reasoning=None):
    delta = {}
    if content:
        delta["content"] = content
    if reasoning:
        delta["reasoning_content"] = reasoning
    return "data: " + json.dumps({"choices": [{"delta": delta}]})


def _make_gen(monkeypatch, resp):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None, stream=None):
        seen["stream"] = stream
        seen["timeout"] = timeout
        return resp

    monkeypatch.setattr(requests, "post", fake_post)
    gen = MiniMaxScriptGenerator(api_key="test-key")
    return gen, seen


def test_post_streams_and_accumulates(monkeypatch):
    resp = _FakeStreamResp([
        _sse(content="【主播甲】"),
        _sse(content="大家好"),
        ":",                       # 非 data 行要能跳过
        "data: {not json",         # 坏行要能跳过
        _sse(content="，欢迎收听"),
        "data: [DONE]",
        _sse(content="这行不该出现"),
    ])
    gen, seen = _make_gen(monkeypatch, resp)

    out = gen._post([{"role": "user", "content": "hi"}], max_tokens=None)

    assert out == "【主播甲】大家好，欢迎收听"
    assert seen["stream"] is True, "必须以流式发请求（总时长超时会砍断长脚本）"
    # 停滞检测语义：read timeout 作用于 chunk 间隔，不是总时长
    assert isinstance(seen["timeout"], tuple)


def test_reasoning_wrapped_back_into_think_block(monkeypatch):
    resp = _FakeStreamResp([
        _sse(reasoning="让我想想"),
        _sse(content="【主播甲】正文"),
        "data: [DONE]",
    ])
    gen, _ = _make_gen(monkeypatch, resp)
    out = gen._post([{"role": "user", "content": "hi"}], max_tokens=None)
    assert out.startswith("<think>让我想想</think>")
    assert "【主播甲】正文" in out


def test_non_200_raises_api_error(monkeypatch):
    resp = _FakeStreamResp([], status_code=401, text="unauthorized")
    gen, _ = _make_gen(monkeypatch, resp)
    with pytest.raises(APIError):
        gen._post([{"role": "user", "content": "hi"}], max_tokens=None)


def test_stall_raises_api_error(monkeypatch):
    def stuck_post(url, headers=None, json=None, timeout=None, stream=None):
        raise requests.ReadTimeout("read timed out")

    monkeypatch.setattr(requests, "post", stuck_post)
    gen = MiniMaxScriptGenerator(api_key="test-key")
    with pytest.raises(APIError, match="停滞|超时"):
        gen._post([{"role": "user", "content": "hi"}], max_tokens=None)
