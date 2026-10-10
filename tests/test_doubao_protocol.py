"""Tests for doubao_protocol binary encoding."""
import pytest

from doubao_protocol import Message, MsgType, EventType, MsgTypeFlagBits


def test_marshal_round_trip_full_request():
    msg = Message(
        type=MsgType.FullClientRequest,
        flag=MsgTypeFlagBits.WithEvent,
        event=EventType.StartConnection,
        payload=b"{}",
    )
    data = msg.marshal()
    assert len(data) >= 4
    parsed = Message.from_bytes(data)
    assert parsed.type == MsgType.FullClientRequest
    assert parsed.event == EventType.StartConnection


def test_marshal_round_trip_audio():
    # Audio frames: marshal() is used to send client-side frames we don't
    # parse back. from_bytes is used to parse server-side audio frames
    # (which include sequence + event from the server). Verify marshal
    # produces a valid 4-byte header prefix with correct type/flag bits.
    msg = Message(
        type=MsgType.AudioOnlyClient,
        flag=MsgTypeFlagBits.PositiveSeq,
        sequence=42,
        event=EventType.PodcastRoundResponse,
        payload=b"\x00\x01\x02\x03",
    )
    data = msg.marshal()
    assert len(data) >= 4
    # Byte 0: version (high nibble) + header_size (low nibble)
    assert (data[0] >> 4) == 1
    assert (data[0] & 0xF) == 1
    # Byte 1: type (high nibble) + flag (low nibble)
    assert (data[1] >> 4) == MsgType.AudioOnlyClient
    assert (data[1] & 0xF) == MsgTypeFlagBits.PositiveSeq
    # Byte 2: serialization (high nibble) + compression (low nibble)
    assert (data[2] >> 4) == 1  # JSON
    assert (data[2] & 0xF) == 0  # None


def test_marshal_round_trip_session():
    msg = Message(
        type=MsgType.FullClientRequest,
        flag=MsgTypeFlagBits.WithEvent,
        event=EventType.StartSession,
        session_id="abc-123",
        payload=b'{"key": "value"}',
    )
    data = msg.marshal()
    parsed = Message.from_bytes(data)
    assert parsed.event == EventType.StartSession
    assert parsed.session_id == "abc-123"
    assert parsed.payload == b'{"key": "value"}'


def test_from_bytes_too_short_raises():
    with pytest.raises(ValueError, match="too short"):
        Message.from_bytes(b"\x00\x00")