#!/usr/bin/env python3
"""
豆包播客大模型 - 二进制协议层

Encapsulates the wire-format Message type and the IntEnums used by
the Volcengine PodcastTTS WebSocket protocol. Has no dependency on
websockets / asyncio / config — it is pure I/O + struct.
"""

import io
import struct
from dataclasses import dataclass
from enum import IntEnum


# ============== 二进制协议（参照官方 SDK protocols.py） ==============

class MsgType(IntEnum):
    Invalid = 0
    FullClientRequest = 0b1
    AudioOnlyClient = 0b10
    FullServerResponse = 0b1001
    AudioOnlyServer = 0b1011
    FrontEndResultServer = 0b1100
    Error = 0b1111


class MsgTypeFlagBits(IntEnum):
    NoSeq = 0
    PositiveSeq = 0b1
    LastNoSeq = 0b10
    NegativeSeq = 0b11
    WithEvent = 0b100


class SerializationBits(IntEnum):
    Raw = 0
    JSON = 0b1
    Thrift = 0b11
    Custom = 0b1111


class CompressionBits(IntEnum):
    None_ = 0
    Gzip = 0b1
    Custom = 0b1111


class EventType(IntEnum):
    None_ = 0
    # Connection events
    StartConnection = 1
    FinishConnection = 2
    ConnectionStarted = 50
    ConnectionFailed = 51
    ConnectionFinished = 52
    # Session events
    StartSession = 100
    CancelSession = 101
    FinishSession = 102
    SessionStarted = 150
    SessionFinished = 152
    UsageResponse = 154
    # Podcast events
    PodcastRoundStart = 360
    PodcastRoundResponse = 361
    PodcastRoundEnd = 362
    PodcastEnd = 363


@dataclass
class Message:
    version: int = 1
    header_size: int = 1  # 1 = 4 bytes
    type: int = MsgType.Invalid
    flag: int = MsgTypeFlagBits.NoSeq
    serialization: int = SerializationBits.JSON
    compression: int = CompressionBits.None_
    event: int = EventType.None_
    session_id: str = ""
    connect_id: str = ""
    sequence: int = 0
    error_code: int = 0
    payload: bytes = b""

    def marshal(self) -> bytes:
        """将 Message 序列化为字节"""
        buffer = io.BytesIO()
        # Byte 0: version (4bit) + header_size (4bit in units of 4 bytes)
        buffer.write(bytes([(self.version << 4) | self.header_size]))
        # Byte 1: msg_type (4bit) + flags (4bit)
        buffer.write(bytes([(self.type << 4) | self.flag]))
        # Byte 2: serialization (4bit) + compression (4bit)
        buffer.write(bytes([(self.serialization << 4) | self.compression]))
        # Byte 3: reserved
        buffer.write(bytes([0]))

        # event number (if WithEvent flag)
        if self.flag == MsgTypeFlagBits.WithEvent:
            buffer.write(struct.pack(">i", self.event))

        # session_id（取决于事件类型）
        if self.event not in [EventType.StartConnection, EventType.FinishConnection,
                               EventType.ConnectionStarted, EventType.ConnectionFailed]:
            sid = self.session_id.encode("utf-8")
            buffer.write(struct.pack(">I", len(sid)))
            buffer.write(sid)

        # payload
        buffer.write(struct.pack(">I", len(self.payload)))
        buffer.write(self.payload)

        return buffer.getvalue()

    def _unmarshal(self, data: bytes) -> None:
        """从字节反序列化 Message（内部方法）"""
        buf = io.BytesIO(data)

        # Byte 0: version + header_size
        b0 = buf.read(1)[0]
        self.version = b0 >> 4
        self.header_size = b0 & 0xF

        # Byte 1: type + flag
        b1 = buf.read(1)[0]
        self.type = b1 >> 4
        self.flag = b1 & 0xF

        # Byte 2: serialization + compression
        b2 = buf.read(1)[0]
        self.serialization = b2 >> 4
        self.compression = b2 & 0xF

        # Skip reserved byte 3
        buf.read(1)

        # Skip header padding (header_size * 4 - 4 bytes already read)
        header_total = 4 * self.header_size
        padding = header_total - 4
        if padding > 0:
            buf.read(padding)

        # Audio frames (AudioOnlyServer/Client) have: [seq?][event][payload]
        # No session_id, no connect_id
        is_audio = self.type in [MsgType.AudioOnlyServer, MsgType.AudioOnlyClient]

        # Read sequence (if PositiveSeq or NegativeSeq flag)
        if self.flag in [MsgTypeFlagBits.PositiveSeq, MsgTypeFlagBits.NegativeSeq]:
            self.sequence = struct.unpack(">i", buf.read(4))[0]

        # Read event - audio frames always have event after sequence
        if is_audio:
            self.event = struct.unpack(">i", buf.read(4))[0]
        elif self.flag == MsgTypeFlagBits.WithEvent:
            self.event = struct.unpack(">i", buf.read(4))[0]

        # Read session_id (not for connection events, not for audio events)
        if not is_audio and self.event not in [
            EventType.StartConnection, EventType.FinishConnection,
            EventType.ConnectionStarted, EventType.ConnectionFailed,
        ]:
            size = struct.unpack(">I", buf.read(4))[0]
            if size > 0:
                self.session_id = buf.read(size).decode("utf-8")

        # Read connect_id (only for connection events)
        if not is_audio and self.event in [
            EventType.ConnectionStarted, EventType.ConnectionFailed, EventType.ConnectionFinished
        ]:
            size = struct.unpack(">I", buf.read(4))[0]
            if size > 0:
                self.connect_id = buf.read(size).decode("utf-8")

        # Read payload - check remaining data first
        remaining = len(data) - buf.tell()
        if remaining >= 4:
            size = struct.unpack(">I", buf.read(4))[0]
            if size > 0 and buf.tell() + size <= len(data):
                self.payload = buf.read(size)
        elif remaining > 0:
            # Streaming audio frame: remaining bytes are the payload
            self.payload = buf.read(remaining)

    @classmethod
    def from_bytes(cls, data: bytes) -> "Message":
        """从字节解析 Message"""
        if len(data) < 4:
            raise ValueError(f"Data too short: expected at least 4 bytes, got {len(data)}")
        msg = cls()
        # Extract type and flag from byte 1
        msg.type = data[1] >> 4
        msg.flag = data[1] & 0xF
        msg._unmarshal(data)
        return msg