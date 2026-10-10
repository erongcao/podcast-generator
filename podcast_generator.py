#!/usr/bin/env python3
"""
豆包播客大模型 - 文档转语音播客生成器（legacy 模块）

This module is kept for backward compatibility. The implementation
has been split into:
  - doubao_protocol.py  (binary protocol, message types)
  - doubao_ws.py        (WebSocket event senders)
  - doubao_podcast.py   (generate_podcast, constants)
  - doubao_audio.py     (audio post-processing: normalize, concat, cleanup)
  - doubao_cli.py       (CLI argparse entry point)

CLI usage unchanged:
  python podcast_generator.py --url <URL> --appid <ID> --token <TOKEN>
"""

from doubao_protocol import (  # noqa: F401
    MsgType, MsgTypeFlagBits, SerializationBits, CompressionBits,
    EventType, Message,
)
from doubao_ws import (  # noqa: F401
    send_start_connection, wait_for_event, send_start_session,
    send_finish_session, send_finish_connection,
)
from doubao_podcast import (  # noqa: F401
    generate_podcast, SPEAKERS, AUDIO_FORMAT, SAMPLE_RATE,
    DEFAULT_APP_ID, DEFAULT_ACCESS_TOKEN,
)
from doubao_audio import add_intro_outro, normalize_audio, cleanup_temp_files  # noqa: F401
from doubao_cli import main  # noqa: F401


if __name__ == "__main__":
    main()