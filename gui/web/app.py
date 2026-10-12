"""
Podcast Generator — Web 看板 (FastAPI 后端)

启动方式（任选其一）：
    1. ~/.podcast-automation/.venv/bin/uvicorn gui.web.app:app --host 127.0.0.1 --port 8765
    2. ~/.podcast-automation/.venv/bin/python -m gui.web.app

启动后浏览器自动开 127.0.0.1:8765（macOS `open` 命令）。

为什么不是 PySide6：
    2026-10 用户报 PySide6 GUI 的 QScrollArea 在多行音色时滚动条不工作，
    栏目高度写死，窗口变小就截断内容。HTML/CSS 下 overflow-y:auto 一行解决。
    见 ~/.claude/projects/-Users-yirongcao/memory/podcast-script-format-actual.md
    和 podcast-voxcpm-1seg-ux-bug.md 的 GUI 部分反思。
"""
from __future__ import annotations

import logging
import sys
import time
import webbrowser
from pathlib import Path

# 把项目根加进 sys.path，使 voxcpm_tts / minimax_tts / voice_library 可直接 import
# （这些模块零改动，沿用 PySide6 时代的位置）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from gui.web.ws import ws_manager  # noqa: E402
from gui.web.auth import SESSION_SECRET, SESSION_MAX_AGE  # noqa: E402
from gui.web.paths import STATIC_DIR  # noqa: E402
from gui.web.api.auth import router as auth_router  # noqa: E402
from gui.web.api.synthesize import (  # noqa: E402
    SynthesizeRequest,
    SynthesizeStartResponse,
    SynthesizeMiniMaxRequest,
    SynthesizeDoubaoRequest,
    build_output_path,
    build_minimax_output_path,
    build_doubao_output_path,
    synthesize_voxcpm_thread,
    synthesize_minimax_thread,
    synthesize_doubao_thread,
    _load_minimax_key,
    _load_volcengine_creds,
)
from gui.web.api.voices import (  # noqa: E402
    list_voices_impl,
    voice_audio_response,
    delete_voice_impl,
    add_voice_impl,
)
from gui.web.api.script_gen import (  # noqa: E402
    generate_script_thread,
    list_recent_scripts,
    save_script_impl,
    load_script_impl,
    ScriptGenRequest,
    ScriptSaveRequest,
)
from gui.web.api.editor import export_impl  # noqa: E402

HOST = "127.0.0.1"
PORT = 8765

log = logging.getLogger(__name__)


def _reconcile_orphaned_syntheses() -> None:
    """Mark any Synthesis rows stuck in 'running' as failed at server boot.

    If the process is killed mid-synthesis (OOM, SIGKILL, hard crash),
    Synthesis rows stay 'status=running' forever with no finished_at —
    there's no watchdog to clean them up. This hook runs ONCE at startup
    and reconciles any row whose started_at is > 1h ago (forgiving enough
    that legitimately-running syntheses aren't disturbed).

    Error message: 'orphaned by process restart' — operators can grep for it.
    """
    from datetime import datetime, timezone, timedelta
    from gui.web.db import SessionLocal
    from gui.web.models import Synthesis

    db = SessionLocal()
    try:
        threshold = datetime.now(timezone.utc) - timedelta(hours=1)
        orphaned = (
            db.query(Synthesis)
            .filter(Synthesis.status == "running", Synthesis.started_at < threshold)
            .all()
        )
        now = datetime.now(timezone.utc)
        for s in orphaned:
            s.status = "failed"
            s.finished_at = now
            s.error_message = "orphaned by process restart"
            log.warning("Marked synthesis %s as failed (orphan)", s.id)
        if orphaned:
            db.commit()
            log.info(f"Reconciled {len(orphaned)} orphaned synthesis rows")
    except Exception as e:
        log.warning(f"Orphan reconciler failed: {e}")
        db.rollback()
    finally:
        db.close()

app = FastAPI(
    title="Podcast Generator",
    version="0.4.1-web",
    description="本地播客生成工具 — Web 看板版（替代 PySide6）",
)

# SessionMiddleware (signed cookie) — must be added BEFORE any @app.on_event hooks
# so Starlette applies it on every request (middleware order is LIFO, but
# add_middleware at startup is the conventional place).
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=SESSION_MAX_AGE,
    same_site="lax",
    https_only=False,  # localhost dev OK; flip to True behind reverse proxy
)

# 静态资源（CSS / JS / 图片）
app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)

# 输出音频（让浏览器 <audio> 能直接 src=/output/xxx.mp3）
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/output",
    StaticFiles(directory=str(OUTPUT_DIR)),
    name="output",
)

# Auth API (register / login / logout / me)
app.include_router(auth_router)


# ============================================================
# 启动 / 关闭事件：把主事件循环交给 ws_manager
# ============================================================
@app.on_event("startup")
async def on_startup():
    import asyncio
    ws_manager.set_loop(asyncio.get_running_loop())

    # 启动时清理 orphan synthesis rows（被 kill mid-synthesis 留下的"running"残骸）
    _reconcile_orphaned_syntheses()

    # 信号兜底：捕获 SIGTERM / SIGINT（uvicorn 转发的硬杀信号），写 log 留死机痕迹。
    # 必须在 startup 注册；on_shutdown 才注册就晚了（uvicorn 已经在 graceful shutdown）。
    import signal
    import sys

    def _on_signal(signum, _frame):
        log_path = OUTPUT_DIR / "web_server.log"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"server killed by signal {signum} at " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
        sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, _on_signal)
        signal.signal(signal.SIGINT, _on_signal)
    except ValueError:
        # 非主线程无法注册（理论上不会，BackgroundTasks 是 thread pool）
        pass


@app.on_event("shutdown")
async def on_shutdown():
    # 追加而非覆盖（write_text 会清掉整个 log，导致死机类型不可追溯）
    log_path = OUTPUT_DIR / "web_server.log"
    with log_path.open("a", encoding="utf-8") as f:
        f.write("server stopped (graceful) at " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n")


@app.get("/")
async def index():
    """主页面：两栏看板骨架。"""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/health")
async def health():
    """健康检查 — 启动后 curl 127.0.0.1:8765/health 确认服务在跑。"""
    return {"ok": True, "version": "0.4.1-web", "phase": 7}


@app.get("/api/version")
async def api_version():
    """前端调一次确认连上了。"""
    return JSONResponse(
        {
            "version": "0.4.1-web",
            "phase": 7,
            "features": {
                "parse": True,              # Phase 2 ✅
                "synthesize_voxcpm": True,  # Phase 3 ✅
                "synthesize_minimax": True, # Phase 4 ✅
                "synthesize_doubao": True,  # Phase 4 ✅
                "voice_library": True,      # Phase 5 ✅
                "script_gen": True,         # Phase 6 ✅
                "responsive": True,         # Phase 7 ✅
            },
        }
    )


# ============================================================
# /api/credentials — 凭证状态（仅显示前缀，不返明文）
# ============================================================
@app.get("/api/credentials")
async def api_credentials():
    """前端启动时调一次，知道哪些凭证已有、哪些需要用户填。"""
    mmx = _load_minimax_key()
    ve = _load_volcengine_creds()
    return JSONResponse({
        "minimax": {
            "present": bool(mmx),
            "source": "config.json" if mmx else "",
            "prefix": mmx[:7] + "..." if mmx else "",   # 演示用前缀
            "length": len(mmx),
        },
        "volcengine": {
            "appid_present": bool(ve["appid"]),
            "token_present": bool(ve["token"]),
        },
    })


# ============================================================
# /api/parse — 脚本解析
# ============================================================
class ParseRequest(BaseModel):
    text: str


class ParseResponse(BaseModel):
    segments: list
    count: int
    speakers: dict  # {"主播甲": 11, "主播乙": 11, ...}


@app.post("/api/parse")
async def api_parse(req: ParseRequest):
    """
    把脚本文本切成 [{speaker, text}, ...] 段列表。

    支持格式（Phase 2 新增）：
        ---                ← 段间分隔
        【主播甲】          ← 全角括号，marker 独立成行（用户真实格式）
        正文...

    向后兼容（旧 PySide6 时代）：
        [主播甲]: 正文
        主播乙：正文
        甲: 正文
        A: 正文

    Raises:
        400: 解析失败（既无 marker 又无内容）—— 不再静默 fallback
    """
    from gui.web.api.parse import parse_script_lines
    try:
        segments = parse_script_lines(req.text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"parse 内部错误: {e}")

    # 统计 speaker 分布
    speakers = {}
    for s in segments:
        speakers[s["speaker"]] = speakers.get(s["speaker"], 0) + 1

    return ParseResponse(
        segments=segments,
        count=len(segments),
        speakers=speakers,
    )


# ============================================================
# /api/synthesize/voxcpm — 本地 VoxCPM2 合成
# ============================================================
@app.post("/api/synthesize/voxcpm")
async def api_synthesize_voxcpm(
    req: SynthesizeRequest,
    background: BackgroundTasks,
):
    """
    异步合成：返回后合成在后台跑，进度/日志通过 WebSocket /ws 推。

    Request:
        segments: [{speaker, text}, ...]
        voice_map: {speaker: voice_id}
        speed_map: {speaker: speed}  （可选，省略 = 全部 1.0）

    WebSocket 推送格式：
        {type: "log", level: "...", message: "..."}
        {type: "progress", done: int, total: int}
        {type: "done", output: "output/xxx.mp3", order: "..."}
        {type: "error", message: "...", traceback?: "..."}
    """
    if not req.segments:
        raise HTTPException(status_code=400, detail="segments 不能为空")
    if not req.voice_map:
        raise HTTPException(status_code=400, detail="voice_map 不能为空")

    output_path = build_output_path()
    task_id = Path(output_path).stem

    background.add_task(
        synthesize_voxcpm_thread,
        req.segments,
        req.voice_map,
        req.speed_map,
        output_path,
    )

    return SynthesizeStartResponse(
        status="started",
        task_id=task_id,
        output=output_path,
    )


# ============================================================
# /api/synthesize/minimax — MiniMax 云端 TTS
# ============================================================
@app.post("/api/synthesize/minimax")
async def api_synthesize_minimax(
    req: SynthesizeMiniMaxRequest,
    background: BackgroundTasks,
):
    """
    MiniMax 异步合成。需要 api_key（从左侧凭证栏传）。
    voice_map 至少 2 个 voice（MiniMax 限制 2 个发音人）；超出回退到第一个。
    """
    if not req.segments:
        raise HTTPException(status_code=400, detail="segments 不能为空")
    if not req.voice_map or len([v for v in req.voice_map.values() if v]) < 2:
        raise HTTPException(
            status_code=400,
            detail="MiniMax 至少需要 2 个 voice_id（在左侧为甲、乙各选一个）",
        )
    # api_key 留空 → 后端从 config.json / env 拿，不强制前端输
    if not (req.api_key or "").strip() and not _load_minimax_key():
        raise HTTPException(
            status_code=400,
            detail="MiniMax api_key 未配置（config.json / env 都没有）",
        )

    output_path = build_minimax_output_path()
    task_id = Path(output_path).stem

    background.add_task(
        synthesize_minimax_thread,
        req.segments,
        req.voice_map,
        req.speed_map,
        req.api_key,
        req.model,
        output_path,
    )

    return SynthesizeStartResponse(
        status="started",
        task_id=task_id,
        output=output_path,
    )


# ============================================================
# /api/synthesize/doubao — 豆包（字节火山引擎）
# ============================================================
@app.post("/api/synthesize/doubao")
async def api_synthesize_doubao(
    req: SynthesizeDoubaoRequest,
    background: BackgroundTasks,
):
    """
    豆包异步合成。需要 appid + token；speakers 是火山引擎发音人 ID 列表。
    注：豆包不像 VoxCPM/MiniMax 有 per-segment progress，
    只能在 done / error 边界报状态。中间过程走 Python logger 推 ws log。
    """
    if not req.segments:
        raise HTTPException(status_code=400, detail="segments 不能为空")
    if not req.appid or not req.token:
        # fallback 到 config.json / env
        ve = _load_volcengine_creds()
        req.appid = req.appid or ve["appid"]
        req.token = req.token or ve["token"]
    if not req.appid or not req.token:
        raise HTTPException(
            status_code=400,
            detail="豆包 appid / token 未配置（左侧填，或 config.json / env）",
        )

    output_path = build_doubao_output_path()
    task_id = Path(output_path).stem

    background.add_task(
        synthesize_doubao_thread,
        req.segments,
        req.speakers,
        req.appid,
        req.token,
        output_path,
    )

    return SynthesizeStartResponse(
        status="started",
        task_id=task_id,
        output=output_path,
    )


# ============================================================
# /api/voices — 音色库
# ============================================================
@app.get("/api/voices")
async def api_list_voices():
    """列出音色库所有 voice。前端下拉用。"""
    return list_voices_impl()


@app.get("/api/voices/{vid}/audio")
async def api_voice_audio(vid: str):
    """试听 ref audio（mp3 / wav 直接 stream）。"""
    return voice_audio_response(vid)


@app.delete("/api/voices/{vid}")
async def api_delete_voice(vid: str):
    """删除一个 voice。"""
    return delete_voice_impl(vid)


@app.post("/api/voices")
async def api_add_voice(
    voice_id: str = Form(...),
    display_name: str = Form(""),
    notes: str = Form(""),
    ref_text: str = Form(""),
    tags: str = Form(""),  # 逗号分隔字符串
    file: UploadFile = File(...),
):
    """
    上传新 voice（multipart/form-data）：
        voice_id: 必填，唯一 ID
        display_name: 可选，默认 = voice_id
        notes / ref_text: 可选
        tags: 逗号分隔，如 "female,voice_design"
        file: 必填，WAV 文件
    """
    file_bytes = await file.read()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    return add_voice_impl(
        voice_id=voice_id,
        display_name=display_name,
        file_bytes=file_bytes,
        notes=notes,
        ref_text=ref_text,
        tags=tag_list,
    )


# ============================================================
# /api/script/* — Phase 6 脚本生成入口
# ============================================================
@app.post("/api/script/generate")
async def api_script_generate(
    req: ScriptGenRequest,
    background: BackgroundTasks,
):
    """Phase 6+: 引擎分发（minimax M3 / 豆包 PodcastTTS）。WebSocket 推 log + progress + script_done。"""
    background.add_task(
        generate_script_thread,
        req.topic,
        req.context,
        req.custom,
        req.n_speakers,
        req.api_key,
        req.show_name,
        req.engine,
        req.appid,
        req.token,
    )
    return {"status": "started", "topic": req.topic}


@app.get("/api/scripts/recent")
async def api_scripts_recent(limit: int = 20):
    """历史脚本列表（按 mtime 倒序）。"""
    return {"scripts": list_recent_scripts(limit=limit)}


@app.get("/api/scripts/get")
async def api_scripts_get(name: str):
    """
    读单个历史脚本（/api/scripts/recent 列出后点"加载"用）。
    name 形如 script_20261011_145102_脾硬化.json。

    返回 JSON：{topic, editor_text, segments, created_at, ...}
    """
    try:
        return load_script_impl(name)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/scripts/save")
async def api_scripts_save(req: ScriptSaveRequest):
    """把当前编辑器内容存盘。"""
    return save_script_impl(req.script_text, req.topic)


# ============================================================
# /api/scripts/export — 下载已保存脚本为 .md
# ============================================================
@app.get("/api/scripts/export")
async def api_scripts_export(name: str, format: str = "raw"):
    """
    导出已保存的脚本为 .md 文件（Content-Disposition: attachment 触发下载）。
    name: script_20261011_*.json
    format: raw | rendered
    """
    try:
        text, filename = export_impl(name, format)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))

    # 中文 / 非 ASCII filename 走 RFC 5987 (filename*=UTF-8''percent-encoded)，
    # 否则 Starlette 用 latin-1 编码 header 直接抛 UnicodeEncodeError。
    # ascii fallback = 纯 ASCII 切片（无中文时两者相同；浏览器优先用 filename*）
    import urllib.parse
    quoted = urllib.parse.quote(filename, safe="")
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    disposition = f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quoted}'

    from fastapi.responses import Response
    return Response(
        content=text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


# ============================================================
# /editor — 全屏脚本编辑器页面
# ============================================================
@app.get("/editor")
async def editor_index():
    """全屏脚本编辑器页面（静态 HTML）。"""
    return FileResponse(str(STATIC_DIR / "editor.html"))


@app.get("/login")
async def login_page():
    """登录页（静态 HTML）。"""
    return FileResponse(str(STATIC_DIR / "login.html"))


# ============================================================
# /ws — 实时日志 / 进度 WebSocket
# ============================================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        # 保持连接（前端不需要发消息，纯接收）
        while True:
            # 接收心跳（前端可以发 "ping"，我们回 "pong"）
            try:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_json({"type": "pong"})
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket)


def _open_browser():
    """macOS 自动开浏览器；其他平台打印 URL 让用户手动复制。"""
    url = f"http://{HOST}:{PORT}/"
    try:
        webbrowser.open(url)
        print(f"▶ 浏览器已开: {url}")
    except Exception as e:
        print(f"⚠ 自动开浏览器失败: {e}")
        print(f"  请手动访问: {url}")


if __name__ == "__main__":
    import uvicorn

    print(f"▶ Podcast Generator Web 看板启动中…")
    print(f"  监听: http://{HOST}:{PORT}/")
    print(f"  健康检查: http://{HOST}:{PORT}/health")

    # 仅在直接 python -m gui.web.app 时自动开浏览器
    # （uvicorn 直接启动时由 launcher 控制）
    import os
    if os.environ.get("PODCAST_WEB_NO_BROWSER") != "1":
        # 延迟 1.5s 等 uvicorn 起好再开
        import threading
        threading.Timer(1.5, _open_browser).start()

    uvicorn.run(
        "gui.web.app:app",
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,  # 生产模式，调试时改 True
    )
