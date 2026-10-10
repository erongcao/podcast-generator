"""
synthesize.py — 合成路由

VoxCPM2 本地合成。Phase 3 只做这一个引擎；MiniMax / 豆包在 Phase 4。

关键设计：
    - 用 FastAPI BackgroundTasks 把合成扔到线程池（不阻塞事件循环）
    - 合成线程里调 voxcpm_tts.synthesize_segments(...) 走原 on_log / progress_callback
    - 这些回调把消息 push 到 ws_manager，前端 WebSocket 实时收到

**不静默 fallback**（接 parse.py 的设计）：
    voice_map 必须覆盖 parse 出来的所有 distinct speaker，否则 raise。
    不允许"主播甲没人选 voice 就用主播乙的声音顶上"——这是用户已经受够的 bug。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, List

from pydantic import BaseModel

from gui.web.ws import ws_manager

log = logging.getLogger(__name__)


# ============================================================
# /api/synthesize/voxcpm
# ============================================================
class SynthesizeRequest(BaseModel):
    segments: List[Dict[str, str]]   # [{speaker, text}, ...]
    voice_map: Dict[str, str]        # {speaker: voice_id}，如 {"主播甲": "jiangyu-4"}
    speed_map: Dict[str, float] = {} # {speaker: speed}，如 {"主播甲": 0.9}


class SynthesizeStartResponse(BaseModel):
    status: str
    task_id: str
    output: str


def _check_voice_coverage(segments: List[Dict], voice_map: Dict[str, str]) -> str:
    """
    校验 voice_map 是否覆盖所有 distinct speaker。
    返回错误信息（如果都覆盖，返回空字符串）。
    """
    distinct = sorted({s["speaker"] for s in segments if s.get("text", "").strip()})
    missing = [sp for sp in distinct if not voice_map.get(sp)]
    if missing:
        return (
            f"以下 speaker 在 voice_map 中没有对应 voice_id: {missing}\n"
            f"已知 voice: {sorted(voice_map.keys())}\n"
            f"请在左侧音色列表为这些 speaker 选好 voice。"
        )
    return ""


def synthesize_voxcpm_thread(
    segments: List[Dict[str, str]],
    voice_map: Dict[str, str],
    speed_map: Dict[str, float],
    output_path: str,
) -> None:
    """
    实际跑合成的线程函数。由 BackgroundTasks 调起。

    通过 ws_manager.push 实时推：
        {"type": "log", "level": "info"|"success"|"warning"|"error", "message": str}
        {"type": "progress", "done": int, "total": int}
        {"type": "done", "output": str, "order": str}
        {"type": "error", "message": str}
    """
    def on_log(msg: str) -> None:
        ws_manager.push({"type": "log", "level": "info", "message": msg})

    def on_progress(done: int, total: int) -> None:
        ws_manager.push({"type": "progress", "done": done, "total": total})

    try:
        # 1. 前置校验：voice 必须全覆盖
        err = _check_voice_coverage(segments, voice_map)
        if err:
            ws_manager.push({"type": "error", "message": err})
            return

        # 2. 检查引擎
        from voxcpm_tts import synthesize_segments  # noqa: F401
    except Exception as e:
        ws_manager.push({"type": "error", "message": f"导入引擎失败: {e}"})
        return

    # 3. 启动信号
    n = sum(1 for s in segments if s.get("text", "").strip())
    ws_manager.push({
        "type": "log",
        "level": "info",
        "message": f"🎙️ VoxCPM 合成开始：共 {n} 段，输出 {Path(output_path).name}",
    })

    try:
        from voxcpm_tts import synthesize_segments as _synth
        out, order = _synth(
            segments=segments,
            voice_id=None,            # 强制用 speaker_map，不走兜底
            output_path=output_path,
            speaker_map=voice_map,
            speed_map=speed_map,
            progress_callback=on_progress,
            on_log=on_log,
        )
        ws_manager.push({
            "type": "done",
            "output": out,
            "order": order,
            "message": f"✅ 已生成 {Path(out).name}",
        })
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        ws_manager.push({
            "type": "error",
            "message": f"{e}",
            "traceback": tb[-500:],   # 截最后 500 字符
        })


def build_output_path() -> str:
    """output/web_voxcpm_YYYYMMDD_HHMMSS.mp3"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)
    return str(output_dir / f"web_voxcpm_{ts}.mp3")


# ============================================================
# 凭证 helper：从 config.json / env / request body 拿 MiniMax key
# ============================================================
import json as _json
import os as _os

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent.parent / "config.json"


def _load_minimax_key() -> str:
    """
    优先级：config.json 的 minimax.api_key > env MINIMAX_API_KEY > ""
    """
    if CONFIG_PATH.exists():
        try:
            cfg = _json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            k = (cfg.get("minimax") or {}).get("api_key") or ""
            if k:
                return k.strip()
        except Exception:
            pass
    return _os.environ.get("MINIMAX_API_KEY", "").strip()


def _load_volcengine_creds() -> dict:
    """
    豆包凭证：config.json 的 volcengine.{appid, token} > env MINIMAX_APP_ID / MINIMAX_TOKEN > ""
    """
    out = {"appid": "", "token": ""}
    if CONFIG_PATH.exists():
        try:
            cfg = _json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            ve = cfg.get("volcengine") or {}
            out["appid"] = (ve.get("appid") or "").strip()
            out["token"] = (ve.get("token") or "").strip()
        except Exception:
            pass
    out["appid"] = out["appid"] or _os.environ.get("MINIMAX_APP_ID", "").strip()
    out["token"] = out["token"] or _os.environ.get("MINIMAX_TOKEN", "").strip()
    return out


# ============================================================
# /api/synthesize/minimax — MiniMax 云端 TTS
# ============================================================
class SynthesizeMiniMaxRequest(BaseModel):
    segments: List[Dict[str, str]]
    voice_map: Dict[str, str]
    speed_map: Dict[str, float] = {}
    api_key: str = ""           # 留空 → 后端 fallback 到 config.json / env
    model: str = "speech-2.6-hd"  # 沿用 minimax_tts 默认


def synthesize_minimax_thread(
    segments: List[Dict[str, str]],
    voice_map: Dict[str, str],
    speed_map: Dict[str, float],
    api_key: str,
    model: str,
    output_path: str,
) -> None:
    """MiniMax 合成线程。

    关键：minimax_tts.synthesize_segments 接受 voice1_id/voice2_id + speaker_map。
    我们把 voice_map 的前 2 个值当 voice1/voice2，speaker_map 显式给优先匹配。
    """
    def on_log(msg: str) -> None:
        ws_manager.push({"type": "log", "level": "info", "message": msg})

    def on_progress(done: int, total: int) -> None:
        ws_manager.push({"type": "progress", "done": done, "total": total})

    # 前置校验
    err = _check_voice_coverage(segments, voice_map)
    if err:
        ws_manager.push({"type": "error", "message": err})
        return

    # fallback：api_key 留空 → 自动从 config.json / env 读
    api_key = (api_key or "").strip() or _load_minimax_key()
    if not api_key:
        ws_manager.push({
            "type": "error",
            "message": "MiniMax api_key 未配置。在左侧凭证栏填，或在 config.json 设 minimax.api_key，或设环境变量 MINIMAX_API_KEY",
        })
        return

    # 拆 voice_map → voice1 / voice2（保持顺序，按 speaker 出现顺序）
    seen = []
    voice1 = voice2 = ""
    for v in voice_map.values():
        if v and v not in seen:
            seen.append(v)
            if not voice1:
                voice1 = v
            elif not voice2:
                voice2 = v
    if not voice1 or not voice2:
        ws_manager.push({
            "type": "error",
            "message": f"MiniMax 需要至少 2 个 voice，当前只有 {len(seen)} 个"
        })
        return

    n = sum(1 for s in segments if s.get("text", "").strip())
    ws_manager.push({
        "type": "log",
        "level": "info",
        "message": f"🌐 MiniMax ({model}) 合成开始：共 {n} 段，voice1={voice1[:12]} voice2={voice2[:12]}",
    })

    try:
        from minimax_tts import synthesize_segments as _synth
        merged_path, order = _synth(
            segments=segments,
            voice1_id=voice1,
            voice2_id=voice2,
            api_key=api_key,
            model=model,
            speaker_map=voice_map,
            speed_map=speed_map,
            output_format="mp3",
            language_boost="auto",
            progress_callback=on_progress,
            on_log=on_log,
        )
        ws_manager.push({
            "type": "done",
            "output": merged_path,
            "order": order,
            "message": f"✅ MiniMax 已生成 {Path(merged_path).name}",
        })
    except Exception as e:
        import traceback
        ws_manager.push({
            "type": "error",
            "message": f"{e}",
            "traceback": traceback.format_exc()[-500:],
        })


def build_minimax_output_path() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return str(Path("output") / f"web_minimax_{ts}.mp3")


# ============================================================
# /api/synthesize/doubao — 豆包（字节火山引擎）
# ============================================================
class SynthesizeDoubaoRequest(BaseModel):
    segments: List[Dict[str, str]]
    speakers: List[str]                        # 火山引擎发音人 id 列表（≥1）
    appid: str
    token: str


def synthesize_doubao_thread(
    segments: List[Dict[str, str]],
    speakers: List[str],
    appid: str,
    token: str,
    output_path: str,
) -> None:
    """
    豆包走 doubao_podcast.generate_podcast (async WSS)。

    豆包不像 VoxCPM / MiniMax 有 on_log / on_progress，
    只在 Python logger (PodcastTTS) 里打 info 日志，
    合成完直接返回 output_path。
    """
    import logging
    import io

    # 捕获 PodcastTTS 的日志转发到 ws_manager
    doubao_logger = logging.getLogger("PodcastTTS")
    doubao_logger.setLevel(logging.INFO)

    class WSHandler(logging.Handler):
        def emit(self, record):
            try:
                msg = self.format(record)
                # 火山引擎的日志大多很长，截前 200
                ws_manager.push({
                    "type": "log",
                    "level": "info",
                    "message": f"[豆包] {msg[:200]}"
                })
            except Exception:
                pass

    handler = WSHandler()
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)
    doubao_logger.addHandler(handler)

    try:
        if not appid or not token:
            ws_manager.push({
                "type": "error",
                "message": "豆包 appid / token 必填"
            })
            return

        if not speakers:
            ws_manager.push({
                "type": "error",
                "message": "豆包至少需要 1 个 speaker voice"
            })
            return

        n = sum(1 for s in segments if s.get("text", "").strip())
        ws_manager.push({
            "type": "log",
            "level": "info",
            "message": f"🎙️ 豆包合成开始：共 {n} 段，speakers={speakers}",
        })
        ws_manager.push({"type": "progress", "done": 0, "total": n})

        import asyncio
        from doubao_podcast import generate_podcast
        result = asyncio.run(generate_podcast(
            nlp_texts=segments,
            appid=appid,
            token=token,
            speakers=speakers,
            output_path=output_path,
        ))
        merged = result.get("output_path") or output_path
        order = " → ".join(f"{s['speaker']}({s['text'][:20]})" for s in segments)
        ws_manager.push({"type": "progress", "done": n, "total": n})
        ws_manager.push({
            "type": "done",
            "output": merged,
            "order": order,
            "message": f"✅ 豆包已生成 {Path(merged).name}",
        })
    except Exception as e:
        import traceback
        ws_manager.push({
            "type": "error",
            "message": f"{e}",
            "traceback": traceback.format_exc()[-500:],
        })
    finally:
        doubao_logger.removeHandler(handler)


def build_doubao_output_path() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return str(Path("output") / f"web_doubao_{ts}.mp3")
