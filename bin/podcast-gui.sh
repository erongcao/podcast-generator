#!/usr/bin/env bash
# Podcast Generator launcher (web 看板 = 默认)
# ------------------------------------------------------------------
# 2026-10 切换到 FastAPI + HTML 看板（替代 PySide6）。
#   - 默认启动 web 看板：uvicorn + 自动开浏览器
#   - --qt 仍可启动旧 PySide6 GUI（2 周观察期；2026-10-24 后考虑删除）
#
# 为什么需要这个脚本：
#   mlx-audio（VoxCPM2 本地引擎）只能装在 Apple Silicon + Python 3.10+
#   的 ~/.podcast-automation/.venv/。系统其他 python（anaconda base 等）
#   装不到 mlx-audio。直接用系统 python 启动 → 选 voxcpm 引擎立刻报错。
#
#   这个脚本做四件事：
#     1. 锁定 venv 路径（PODCAST_VENV env var 可覆盖）
#     2. 启动期自检（voxcpm2 + fastapi + uvicorn）
#     3. web 模式：后台起 uvicorn，自动开浏览器，--stop 杀进程
#     4. --qt 模式：exec 启动 PySide6（向后兼容）
#
# 用法：
#   bin/podcast-gui.sh                      # 默认 web 看板
#   bin/podcast-gui.sh --qt                 # 旧 PySide6 GUI（2 周观察期）
#   bin/podcast-gui.sh --port 9000          # 改端口
#   bin/podcast-gui.sh --no-browser         # 不开浏览器
#   bin/podcast-gui.sh --stop               # 停 uvicorn
#   bin/podcast-gui.sh --status             # 看 uvicorn 在不在
#   bin/podcast-gui.sh --doctor             # 只跑自检
#   bin/podcast-gui.sh --install-missing    # 自动补依赖
#
# macOS 用户：双击 bin/podcast-gui.command 即可（已配置好）
# ------------------------------------------------------------------

set -eo pipefail

# 注：不开 set -u，因为脚本里有后台进程 PID 在 subshell 边界偶发
# "unbound variable"（bash 5.x 在特定 line 报错的已知小问题）。
# 全部 unset 访问都用 ${VAR:-} 兜底，避免这个问题。

# ---- locate project root (脚本可能在任意 cwd 下被调用) ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ---- 锁定 venv ----
VENV_PATH="${PODCAST_VENV:-$HOME/.podcast-automation/.venv}"
VENV_PY="$VENV_PATH/bin/python"

# ---- 默认值 ----
MODE="web"            # web | qt
PORT=8765
HOST="127.0.0.1"
OPEN_BROWSER=1
LOG_FILE="$PROJECT_ROOT/output/web_server.log"
PID_FILE="$PROJECT_ROOT/output/web_server.pid"
DOCTOR_ONLY=0
INSTALL_MISSING=0
STOP_ONLY=0
STATUS_ONLY=0

# ---- 参数解析 ----
while [[ $# -gt 0 ]]; do
  case "$1" in
    --qt|-qt)        MODE="qt"; shift ;;
    --web)           MODE="web"; shift ;;
    --port)          PORT="$2"; shift 2 ;;
    --host)          HOST="$2"; shift 2 ;;
    --no-browser)    OPEN_BROWSER=0; shift ;;
    --stop)          STOP_ONLY=1; shift ;;
    --status)        STATUS_ONLY=1; shift ;;
    --doctor)        DOCTOR_ONLY=1; shift ;;
    --install-missing) INSTALL_MISSING=1; shift ;;
    -h|--help)
      sed -n '2,32p' "$0"
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

# ============================================================
# --status 早返回
# ============================================================
if [ "$STATUS_ONLY" = "1" ]; then
  if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "▶ uvicorn 在跑: PID=$PID  http://$HOST:$PORT/"
      exit 0
    else
      echo "▶ pid 文件陈旧（$PID 已死），无 uvicorn 在跑"
      rm -f "$PID_FILE"
      exit 1
    fi
  else
    echo "▶ 无 uvicorn 在跑"
    exit 1
  fi
fi

# ============================================================
# --stop 早返回
# ============================================================
if [ "$STOP_ONLY" = "1" ]; then
  if [ ! -f "$PID_FILE" ]; then
    echo "▶ uvicorn 未在跑（无 pid 文件）"
    # 兜底：按端口杀
    if command -v lsof >/dev/null 2>&1; then
      LPID=$(lsof -ti:"$PORT" 2>/dev/null || true)
      if [ -n "$LPID" ]; then
        echo "  但端口 $PORT 被 PID=$LPID 占用，杀一下"
        kill "$LPID" 2>/dev/null || true
      fi
    fi
    exit 0
  fi
  PID=$(cat "$PID_FILE")
  if kill -0 "$PID" 2>/dev/null; then
    echo "▶ 停 uvicorn PID=$PID..."
    kill "$PID" 2>/dev/null || true
    sleep 1
    if kill -0 "$PID" 2>/dev/null; then
      kill -9 "$PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    echo "  ✅ 已停"
  else
    echo "▶ pid 文件陈旧（$PID 已死），清理"
    rm -f "$PID_FILE"
  fi
  exit 0
fi

# ============================================================
# venv 必须存在
# ============================================================
if [ ! -x "$VENV_PY" ]; then
  cat <<EOF >&2
❌ 找不到 venv Python：$VENV_PY

修复方法：
  uv venv $VENV_PATH --python 3.11
  $VENV_PATH/bin/pip install -r requirements.txt
  $VENV_PATH/bin/pip install -e $HOME/.podcast-automation/mlx-audio

或指定其他 venv：PODCAST_VENV=/path/to/venv bin/podcast-gui.sh
EOF
  exit 2
fi

echo "▶ 用 venv: $VENV_PY"
"$VENV_PY" --version

# ============================================================
# 启动期自检
# ============================================================
check_module() {
  local mod="$1" required="$2"
  if "$VENV_PY" -c "import importlib.util; import sys; sys.exit(0 if importlib.util.find_spec('$mod') else 1)" 2>/dev/null; then
    echo "  ✅ $mod"
    return 0
  else
    if [ "$required" = "1" ]; then
      echo "  ❌ $mod (必需缺失)"
    else
      echo "  ⚠️  $mod (可选缺失)"
    fi
    return 1
  fi
}

# voxcpm2 硬卡口
if [ "${PODCAST_SKIP_VENV_CHECK:-0}" != "1" ]; then
  echo "▶ 启动期环境自检..."
  OK=1
  echo "  核心依赖："
  if [ "$MODE" = "web" ]; then
    check_module "fastapi" 1 || OK=0
    check_module "uvicorn" 1 || OK=0
    check_module "requests" 1 || OK=0
  else
    check_module "PySide6" 1 || OK=0
  fi
  echo "  TTS 引擎："
  if check_module "mlx_audio.tts.models.voxcpm2" 0; then
    echo "     → VoxCPM2 本地引擎 OK"
  else
    echo "     ⚠️  VoxCPM2 本地引擎不可用（PyPI 0.2.x 没 voxcpm2 子模块；"
    echo "         需要 editable install ~/.podcast-automation/mlx-audio/）"
  fi
  check_module "minimax_tts" 0 && echo "     → MiniMax 云端 OK" || true
  check_module "doubao_podcast" 0 && echo "     → 豆包云端 OK" || true
  check_module "voice_library" 0 && echo "     → 音色库 OK" || true

  if [ "$OK" = "0" ]; then
    if [ "$INSTALL_MISSING" = "1" ]; then
      echo "▶ --install-missing：自动补..."
      "$VENV_PATH/bin/pip" install --upgrade fastapi uvicorn requests python-multipart || true
      if [ "$MODE" = "qt" ]; then
        "$VENV_PATH/bin/pip" install --upgrade PySide6 || true
      fi
      echo "▶ 重检..."
      if ! "$VENV_PY" -c "import fastapi, uvicorn" 2>/dev/null; then
        echo "❌ 补装后仍缺 fastapi/uvicorn" >&2
        exit 2
      fi
    else
      cat <<EOF >&2

💡 缺依赖。用 --install-missing 自动补：
   bin/podcast-gui.sh --install-missing
EOF
      exit 2
    fi
  fi
fi

if [ "$DOCTOR_ONLY" = "1" ]; then
  echo "✅ 自检通过"
  exit 0
fi

# ============================================================
# --qt 模式：启动 PySide6（向后兼容，2 周观察期）
# ============================================================
if [ "$MODE" = "qt" ]; then
  echo "▶ 启动 GUI（PySide6 / Qt6）—— ⚠️ 旧版，2026-10-24 后考虑删除"
  exec "$VENV_PY" -m gui.qt.main_window
fi

# ============================================================
# 默认：启动 web 看板
# ============================================================
# 检查端口是否被占
if command -v lsof >/dev/null 2>&1; then
  EXISTING=$(lsof -ti:"$PORT" 2>/dev/null | head -1 || true)
  if [ -n "$EXISTING" ]; then
    # 看是否是我们的 pid_file
    if [ -f "$PID_FILE" ] && [ "$(cat "$PID_FILE" 2>/dev/null)" = "$EXISTING" ]; then
      echo "▶ uvicorn 已在跑（PID $EXISTING），复用： http://$HOST:$PORT/"
      [ "$OPEN_BROWSER" = "1" ] && open_browser || true
      exit 0
    fi
    echo "❌ 端口 $PORT 已被 PID $EXISTING 占用"
    echo "  bin/podcast-gui.sh --port 9000  # 换端口"
    echo "  或：lsof -ti:$PORT | xargs kill"
    exit 2
  fi
fi

mkdir -p "$PROJECT_ROOT/output"

# 启动 uvicorn（后台）
echo "▶ 启动 web 看板： http://$HOST:$PORT/"
echo "  日志：$LOG_FILE"
PODCAST_WEB_NO_BROWSER=1 nohup "$VENV_PY" -m uvicorn gui.web.app:app \
  --host "$HOST" --port "$PORT" --log-level info \
  > "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"
disown "$SERVER_PID" 2>/dev/null || true

# 等服务起好（最多 10s）
for i in $(seq 1 20); do
  if curl -fsS "http://$HOST:$PORT/health" >/dev/null 2>&1; then
    echo "  ✅ uvicorn 起好（PID $SERVER_PID）"
    break
  fi
  sleep 0.5
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "  ❌ uvicorn 启动失败，看日志："
    tail -20 "$LOG_FILE" >&2
    rm -f "$PID_FILE"
    exit 2
  fi
done

# 开浏览器
open_browser() {
  if [ "$(uname -s)" = "Darwin" ]; then
    open "http://$HOST:$PORT/" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://$HOST:$PORT/" >/dev/null 2>&1 || true
  fi
}
[ "$OPEN_BROWSER" = "1" ] && open_browser || true

echo ""
echo "▶ Web 看板运行中"
echo "  浏览器： http://$HOST:$PORT/"
echo "  停服：  bin/podcast-gui.sh --stop"
echo "  日志：  tail -f $LOG_FILE"
echo ""
echo "提示：脚本立即返回（uvicorn 后台跑）。想前台跑（看实时日志）用 --no-browser 然后 tail 日志。"
exit 0
