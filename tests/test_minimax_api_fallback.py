"""mmx CLI 认证失败后，synthesize() 必须只返回音频字节。

2026-11-03：CLI 报 API key failed，代码切到 HTTP。_synthesize_api 返回
(bytes, response_dict)，synthesize() 把整个元组交了出去，写 mp3 时
TypeError: a bytes-like object is required, not 'tuple'。
"""
from exceptions import AudioProcessingError
import minimax_tts


def test_api_fallback_returns_audio_bytes_only(monkeypatch):
    monkeypatch.setattr(minimax_tts, "_use_mmx_cli", lambda: True)

    def _cli_auth_failed(**_kwargs):
        raise AudioProcessingError(
            "mmx speech synthesize failed: Detecting region... failed\n"
            "Warning: invalid api key"
        )

    monkeypatch.setattr(minimax_tts, "_synthesize_cli", _cli_auth_failed)
    monkeypatch.setattr(
        minimax_tts,
        "_synthesize_api",
        lambda **_kwargs: (b"ID3-audio", {"base_resp": {"status_code": 0}}),
    )

    audio = minimax_tts.synthesize(text="你好", voice_id="v", api_key="k")
    assert audio == b"ID3-audio"
    assert isinstance(audio, bytes)
