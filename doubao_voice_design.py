#!/usr/bin/env python3
"""
MiniMax Voice Design API client — generate a custom voice from a text description.

Verified end-to-end against the user's MiniMax account (api.minimaxi.com, cn region).

  POST https://api.minimaxi.com/v1/voice_design
  Headers: Authorization: Bearer <api_key>
           Content-Type: application/json
  Body (JSON):
    {
      "prompt":       str  (required) — text description of voice
      "gender":       "male" | "female" | "neutral"
      "language":     str  (e.g., "Chinese")
      "preview_text": str  — text to synthesize in the preview audio
      "voice_id":     str  — optional; if omitted, API auto-generates one
                              (format: "ttv-voice-<ts>-<8char>")
    }

  Success response (200):
    {
      "voice_id":    str  (the voice to use in subsequent TTS calls)
      "trial_audio": str  (hex-encoded MP3 bytes; ID3 + MP3 frames,
                           ~30-40 KB for short preview_text)
      "extra_info":  {"usage_characters": int,
                      "charge_method": "voice_design",
                      "charge_model": "preview_text"}
      "base_resp":   {"status_code": 0, "status_msg": "success"}
    }

  Error codes (base_resp.status_code; 0 = success):
    1004  account auth failed
    1008  insufficient balance
    2013  invalid params (bad prompt / unsupported language)
    2049  invalid api key

  Designed voices are usable in /v1/t2a_v2 immediately after this call returns
  (verified by synthesis test against user's account).
"""
import requests

from exceptions import APIError
from network_retry import retry_on_transient

DESIGN_URL = "https://api.minimaxi.com/v1/voice_design"


@retry_on_transient()
def _post_design_request(headers, json_body, timeout):
    return requests.post(DESIGN_URL, headers=headers, json=json_body, timeout=timeout)


def design_voice(prompt, api_key, gender="male", language="Chinese",
                 preview_text="你好，这是音色预览。", voice_id=None,
                 model=None, timeout=60):
    """Generate a custom voice from a text description.

    Args:
        prompt:       text description of the desired voice (required),
                      e.g., "40岁男医生，语速慢，沉稳专业"
        api_key:      MiniMax API key
        gender:       "male" | "female" | "neutral" (default "male")
        language:     e.g., "Chinese", "English" (default "Chinese")
        preview_text: text to synthesize in the preview audio (default short
                      Chinese greeting)
        voice_id:     optional custom ID; if None, the API auto-generates one
                      (format: "ttv-voice-<timestamp>-<8char>")
        model:        optional model override
        timeout:      HTTP timeout seconds

    Returns:
        dict with keys:
            "voice_id":       str — voice identifier for TTS calls
            "preview_audio":  bytes — raw MP3 bytes (already hex-decoded)

    Raises:
        APIError on non-zero base_resp.status_code or HTTP failure.
    """
    payload = {
        "prompt": prompt,
        "gender": gender,
        "language": language,
        "preview_text": preview_text,
    }
    if voice_id:
        payload["voice_id"] = voice_id
    if model:
        payload["model"] = model

    resp = _post_design_request(
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json_body=payload,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise APIError(
            "MiniMax",
            f"voice_design HTTP {resp.status_code}: {resp.text[:200]}",
            trace_id=None,
        )
    try:
        data = resp.json()
    except ValueError as e:
        raise APIError("MiniMax", f"voice_design invalid JSON: {e}")

    base = data.get("base_resp", {})
    if base.get("status_code", -1) != 0:
        raise APIError(
            "MiniMax",
            base.get("status_msg", "unknown"),
            trace_id=data.get("trace_id"),
        )

    voice_id_out = data.get("voice_id")
    if not voice_id_out:
        raise APIError("MiniMax", "voice_design response missing voice_id")

    # trial_audio is hex-encoded MP3 bytes (ID3 header confirmed in probe)
    trial_hex = data.get("trial_audio", "")
    try:
        preview_audio = bytes.fromhex(trial_hex) if trial_hex else b""
    except ValueError as e:
        raise APIError("MiniMax", f"voice_design bad trial_audio hex: {e}")

    return {
        "voice_id": voice_id_out,
        "preview_audio": preview_audio,
        "extra_info": data.get("extra_info", {}),
    }