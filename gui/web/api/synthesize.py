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

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel

from gui.web.db import SessionLocal
from gui.web.models import User, Script, Synthesis, Output
from gui.web.ws import ws_manager

log = logging.getLogger(__name__)


# ============================================================
# DB tracking helpers (Task 3)
# ============================================================
def _get_default_owner(db) -> Optional[User]:
    """单用户自托管场景下的默认 owner：第一个用户（优先 admin）。

    Returns None if no users exist. Callers must handle None gracefully
    (skip DB writes, still run synthesis).
    """
    return (
        db.query(User)
        .filter(User.is_admin == True)  # noqa: E712
        .order_by(User.id)
        .first()
        or db.query(User).order_by(User.id).first()
    )


def _create_synthesis(
    segments: List[Dict[str, str]],
    voice_map: Dict[str, str],
    speed_map: Dict[str, float],
    engine: str,
) -> Optional[Synthesis]:
    """
    Create scratch Script + Synthesis rows. Returns the Synthesis (with .id),
    or None if no user exists (caller should proceed without DB tracking).

    The script is "scratch" (topic auto-generated) because /api/synthesize/*
    requests don't include a topic. The Synthesis row links to it for
    provenance — you can always find what text was synthesized.
    """
    db = SessionLocal()
    try:
        owner = _get_default_owner(db)
        if owner is None:
            log.warning("No user exists; synthesis DB tracking skipped")
            return None

        ts = datetime.utcnow()
        scratch_topic = f"web_{engine}_{ts.strftime('%Y%m%d_%H%M%S')}_{len(segments)}seg"
        n_text = sum(1 for s in segments if (s.get("text") or "").strip())
        script = Script(
            owner_id=owner.id,
            topic=scratch_topic,
            editor_text="",
            segments_json=json.dumps(segments, ensure_ascii=False) if segments else None,
            n_segments=n_text,
            created_at=ts,
        )
        db.add(script)
        db.flush()  # populate script.id without committing

        synth = Synthesis(
            owner_id=owner.id,
            script_id=script.id,
            engine=engine,
            voice_map=voice_map or {},
            speed_map=speed_map or {},
            status="running",
            started_at=ts,
            created_at=ts,
        )
        db.add(synth)
        db.commit()
        db.refresh(synth)
        return synth
    except Exception as e:
        log.warning(f"Failed to create Synthesis row: {e}")
        db.rollback()
        return None
    finally:
        db.close()


def _complete_synthesis(synth: Optional[Synthesis], output_path: str) -> None:
    """Mark Synthesis as done + create Output row. No-op if synth is None."""
    if synth is None:
        return
    db = SessionLocal()
    try:
        # Update synthesis
        synth_db = db.get(Synthesis, synth.id)
        if synth_db is None:
            return
        synth_db.status = "done"
        synth_db.finished_at = datetime.utcnow()

        # Output row
        p = Path(output_path)
        file_size = p.stat().st_size if p.exists() else None
        out = Output(
            owner_id=synth_db.owner_id,
            synthesis_id=synth_db.id,
            file_path=str(p),
            file_size=file_size,
            format=p.suffix.lstrip(".") or "mp3",
            created_at=datetime.utcnow(),
        )
        db.add(out)
        db.commit()
    except Exception as e:
        log.warning(f"Failed to record synthesis completion: {e}")
        db.rollback()
    finally:
        db.close()


def _fail_synthesis(synth: Optional[Synthesis], error_message: str) -> None:
    """Mark Synthesis as failed with error_message. No-op if synth is None."""
    if synth is None:
        return
    db = SessionLocal()
    try:
        synth_db = db.get(Synthesis, synth.id)
        if synth_db is None:
            return
        synth_db.status = "failed"
        synth_db.finished_at = datetime.utcnow()
        synth_db.error_message = error_message[:2000]  # truncate
        db.commit()
    except Exception as e:
        log.warning(f"Failed to record synthesis failure: {e}")
        db.rollback()
    finally:
        db.close()


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

    DB tracking: 创建 Script (scratch) + Synthesis rows；done 时创建 Output。
    """
    synth = _create_synthesis(segments, voice_map, speed_map, engine="voxcpm")

    def on_log(msg: str) -> None:
        ws_manager.push({"type": "log", "level": "info", "message": msg})

    def on_progress(done: int, total: int) -> None:
        ws_manager.push({"type": "progress", "done": done, "total": total})

    try:
        # 1. 前置校验：voice 必须全覆盖
        err = _check_voice_coverage(segments, voice_map)
        if err:
            _fail_synthesis(synth, err)
            ws_manager.push({"type": "error", "message": err})
            return

        # 2. 检查引擎
        from voxcpm_tts import synthesize_segments  # noqa: F401
    except Exception as e:
        _fail_synthesis(synth, f"导入引擎失败: {e}")
        ws_manager.push({"type": "error", "message": f"导入引擎失败: {e}"})
        return

    # 3. 启动信号 — 立刻推一条，避免首段 model load 期间前端无反馈（5-10s 静默）
    n = sum(1 for s in segments if s.get("text", "").strip())
    ws_manager.push({
        "type": "log",
        "level": "info",
        "message": f"🚀 VoxCPM 引擎启动：{n} 段 · 首次加载模型约 5-10s，请耐心等待...",
    })
    ws_manager.push({"type": "progress", "done": 0, "total": max(n, 1)})
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
        _complete_synthesis(synth, out)
        ws_manager.push({
            "type": "done",
            "output": out,
            "order": order,
            "message": f"✅ 已生成 {Path(out).name}",
        })
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        _fail_synthesis(synth, f"{e}\n{tb[-500:]}")
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
    豆包凭证加载（兼容两套字段名）：
      config.json volcengine.{app_id, access_token} (config.py 默认 / 真实格式)
      config.json volcengine.{appid, token}        (旧版别名)
      env MINIMAX_APP_ID / MINIMAX_TOKEN

    Returns: {"appid": "...", "token": "..."}
    """
    out = {"appid": "", "token": ""}
    if CONFIG_PATH.exists():
        try:
            cfg = _json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            ve = cfg.get("volcengine") or {}
            # 兼容两套字段名：app_id / app_id → appid；access_token / token → token
            out["appid"] = (ve.get("app_id") or ve.get("appid") or "").strip()
            out["token"] = (ve.get("access_token") or ve.get("token") or "").strip()
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

    DB tracking: 创建 Script (scratch) + Synthesis rows；done 时创建 Output。
    """
    synth = _create_synthesis(segments, voice_map, speed_map, engine="minimax")

    def on_log(msg: str) -> None:
        ws_manager.push({"type": "log", "level": "info", "message": msg})

    def on_progress(done: int, total: int) -> None:
        ws_manager.push({"type": "progress", "done": done, "total": total})

    # 前置校验
    err = _check_voice_coverage(segments, voice_map)
    if err:
        _fail_synthesis(synth, err)
        ws_manager.push({"type": "error", "message": err})
        return

    # fallback：api_key 留空 → 自动从 config.json / env 读
    api_key = (api_key or "").strip() or _load_minimax_key()
    if not api_key:
        _fail_synthesis(synth, "MiniMax api_key 未配置")
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
        _fail_synthesis(synth, f"MiniMax 需要至少 2 个 voice，当前只有 {len(seen)} 个")
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
        _complete_synthesis(synth, merged_path)
        ws_manager.push({
            "type": "done",
            "output": merged_path,
            "order": order,
            "message": f"✅ MiniMax 已生成 {Path(merged_path).name}",
        })
    except Exception as e:
        import traceback
        _fail_synthesis(synth, f"{e}\n{traceback.format_exc()[-500:]}")
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

    DB tracking: 创建 Script (scratch) + Synthesis rows；done 时创建 Output。
    """
    import logging
    import io

    # voice_map 字段在 DB 里也需要存一下（豆包的 speaker 是火山引擎 voice_id 列表）
    voice_map = {f"speaker_{i}": spk for i, spk in enumerate(speakers or [])}
    synth = _create_synthesis(segments, voice_map, {}, engine="doubao")

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
            _fail_synthesis(synth, "豆包 appid / token 必填")
            ws_manager.push({
                "type": "error",
                "message": "豆包 appid / token 必填"
            })
            return

        if not speakers:
            _fail_synthesis(synth, "豆包至少需要 1 个 speaker voice")
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
        _complete_synthesis(synth, merged)
        ws_manager.push({"type": "progress", "done": n, "total": n})
        ws_manager.push({
            "type": "done",
            "output": merged,
            "order": order,
            "message": f"✅ 豆包已生成 {Path(merged).name}",
        })
    except Exception as e:
        import traceback
        _fail_synthesis(synth, f"{e}\n{traceback.format_exc()[-500:]}")
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
