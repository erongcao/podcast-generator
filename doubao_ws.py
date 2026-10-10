#!/usr/bin/env python3
"""
豆包播客大模型 - WebSocket 事件发送/等待辅助函数

Pure async helpers for sending protocol events and waiting for specific
server responses on an open websockets connection.
"""

from doubao_protocol import (
    Message,
    MsgType,
    MsgTypeFlagBits,
    EventType,
)
from exceptions import APIError
from logger import PodcastTTS


# ============== 协议辅助函数 ==============

async def send_start_connection(ws):
    """发送 StartConnection 事件"""
    msg = Message(
        type=MsgType.FullClientRequest,
        flag=MsgTypeFlagBits.WithEvent,
        event=EventType.StartConnection,
        payload=b"{}"
    )
    PodcastTTS.info(f"[→] StartConnection")
    await ws.send(msg.marshal())


async def wait_for_event(ws, expected_event):
    """等待特定事件"""
    while True:
        data = await ws.recv()
        if not isinstance(data, bytes):
            continue
        msg = Message.from_bytes(data)
        PodcastTTS.info(f"[←] type={msg.type} event={msg.event} payload_size={len(msg.payload)}")
        if msg.event == expected_event:
            return msg
        if msg.type == MsgType.Error:
            err_msg = msg.payload.decode("utf-8") if msg.payload else "(空错误)"
            PodcastTTS.error(f"Server error: {err_msg}")
            raise APIError("Volcengine", err_msg)


async def send_start_session(ws, session_id, payload_bytes):
    """发送 StartSession 事件"""
    msg = Message(
        type=MsgType.FullClientRequest,
        flag=MsgTypeFlagBits.WithEvent,
        event=EventType.StartSession,
        session_id=session_id,
        payload=payload_bytes
    )
    PodcastTTS.info(f"[→] StartSession (session={session_id})")
    await ws.send(msg.marshal())


async def send_finish_session(ws, session_id):
    """发送 FinishSession 事件"""
    msg = Message(
        type=MsgType.FullClientRequest,
        flag=MsgTypeFlagBits.WithEvent,
        event=EventType.FinishSession,
        session_id=session_id,
        payload=b"{}"
    )
    PodcastTTS.info(f"[→] FinishSession")
    await ws.send(msg.marshal())


async def send_finish_connection(ws):
    """发送 FinishConnection 事件"""
    msg = Message(
        type=MsgType.FullClientRequest,
        flag=MsgTypeFlagBits.WithEvent,
        event=EventType.FinishConnection,
        payload=b"{}"
    )
    PodcastTTS.info(f"[→] FinishConnection")
    await ws.send(msg.marshal())