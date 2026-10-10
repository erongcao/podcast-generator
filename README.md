# 患者科普播客生成器 · Patient Podcast Generator

Web 看板驱动的中文播客生成器：把文档/URL/粘贴文本转成甲乙双主播科普音频，
底层接 3 个 TTS 引擎（豆包火山 / MiniMax / VoxCPM2 本地）。

> Originally built for a doctor producing patient-education audio content.

---

## ⚠️ 迁移状态 (2026-10-10): FastAPI Web 看板

旧 GUI（`gui/qt/` PySide6、`gui/main_window.py` customtkinter）已
**全面迁移到 FastAPI + HTML 看板**（新代码在 `gui/web/`）。
**默认启动 web 看板，不要再直接用 `python -m gui.qt.main_window`**。

**为什么要从 PySide6 迁到 web：**

- QScrollArea 在多行音色时滚动条不工作（addWidget 后不重算 sizeHint）
- 栏目高度写死，窗口缩放就截断内容
- 跨线程 signal/slot 调试路径比 WebSocket 长
- 浏览器原生的 `overflow-y: auto` + `@media` 响应式一行解决

**当前状态（2026-10-10）：**

- ✅ 8 阶段迁移全通：parse / 3 引擎合成 / 音色库 CRUD+录音 / AI 脚本生成 / 响应式 / 速度滑块 / 音频历史 / WebSocket 状态
- ✅ 凭证自动加载（config.json → env fallback，前端不暴露明文）
- ✅ AI 脚本生成绕开 upstream parser bug，10 段甲 + 10 段乙 round-trip 一致
- ✅ launcher 硬卡口 venv 路径（`~/.podcast-automation/.venv/`，mlx-audio 必装）
- 🟡 旧 PySide6 (`gui/qt/`) 仍可通过 `--qt` 启，作为 2 周观察期备份
- 🟡 旧 customtkinter (`gui/main_window.py` + `gui/panels/`) 已废弃

**回退路径（仍在）：** `bin/podcast-gui.sh --qt` 启动旧 PySide6；
`gui/main_window.py` 自定义 tkinter 已知 macOS 27 + Tk 9.0 会崩，**不要回归**。

---

## Features

- **3 个 TTS 引擎**：
  - **豆包 / 火山**（云端，APP ID + Access Token）
  - **MiniMax**（云端，API Key，支持 voice_clone 音色库）
  - **VoxCPM2**（本地，Apple Silicon，mlx-audio 加速）
- **脚本 AI 生成**（MiniMax M3 chat completion）：3 个入口
  - 直接粘贴 / 导入历史脚本 / 输入主题让 LLM 生成
- **音色库 CRUD + 浏览器录音**：上传 wav / 麦克风录制 → 注册 voice_id → 复用
- **鲁棒脚本解析**：`---` 分段 + 全角 `【主播甲】` marker，向后兼容 `[甲]: text` 老格式
- **WebSocket 实时日志 / 进度 / 完成事件**
- **响应式布局**（< 900px 自动堆叠，< 500px 紧凑模式）
- **音频历史**（最近 5 个生成结果，可重播 / 删除）
- **每主播速度滑块**（0.5–1.5×，foreign 音色 0.85，native 中文 1.0）
- **凭证 fallback 链**：HTTP body → `config.json` → env var

---

## Quick start

### 1. Clone + 装依赖

```bash
git clone https://github.com/erongcao/podcast-generator.git
cd podcast-generator

# mlx-audio（VoxCPM2 本地引擎）只能装在 Apple Silicon + Python 3.10+ 的独立 venv
# launcher 会自动用它，**不要**用系统 python / anaconda base
uv venv ~/.podcast-automation/.venv --python 3.11
~/.podcast-automation/.venv/bin/pip install -r requirements.txt
~/.podcast-automation/.venv/bin/pip install -e ~/.podcast-automation/mlx-audio
```

### 2. 配置 API 密钥

```bash
cp config.example.json config.json
# 编辑 config.json，填入：
#   volcengine.app_id / access_token  (豆包)
#   minimax.api_key                   (MiniMax)
```

`config.json` 已在 `.gitignore` 里，永远不会进 git。Web 后端优先从这里读，
读不到再 fallback 到 env var（`MINIMAX_API_KEY` / `MINIMAX_TOKEN` / `MINIMAX_APP_ID`）。

### 3. 启动 web 看板

```bash
# 一行搞定：venv 自检 → 启 uvicorn → 开浏览器
bin/podcast-gui.sh

# macOS 用户：Finder 双击 bin/podcast-gui.command 也行
```

启动后浏览器自动开 <http://127.0.0.1:8765/>。其他 flag：

```bash
bin/podcast-gui.sh --port 9000      # 换端口
bin/podcast-gui.sh --no-browser     # 不开浏览器
bin/podcast-gui.sh --status         # 看 PID
bin/podcast-gui.sh --stop           # 停服
bin/podcast-gui.sh --doctor         # 只跑依赖自检
bin/podcast-gui.sh --install-missing # 缺依赖自动装
bin/podcast-gui.sh --qt             # 启旧 PySide6（2 周观察期）
```

---

## Architecture

```
bin/
  podcast-gui.sh                # 唯一入口：venv 卡口 + 自检 + 启 uvicorn
  podcast-gui.command           # macOS Finder 双击包装

gui/web/                        # 当前默认（FastAPI + HTML 看板）
  app.py                        # FastAPI app + 路由注册
  ws.py                         # WebSocketManager（线程 → 事件循环）
  api/
    parse.py                    # 鲁棒脚本解析（--- + 全角【】 + 老格式）
    synthesize.py               # 3 引擎合成（VoxCPM / MiniMax / 豆包）
    voices.py                   # 音色库 CRUD
    script_gen.py               # MiniMax M3 AI 脚本生成
  static/
    index.html                  # 两栏看板
    css/theme.css               # GHIBLI 配色 + 响应式
    js/app.js                   # WS 重连 / 速度滑块 / 音频历史

gui/qt/                         # 旧 PySide6（2 周观察期，2026-10-24 后考虑删）
gui/main_window.py              # 旧 customtkinter（已废弃）

voice_library.py                # CRUD on cloned-voice metadata
voice_recorder.py               # sounddevice 录音
voice_cloner.py                 # MiniMax clone API client
minimax_tts.py                  # MiniMax TTS API
minimax_script_gen.py           # MiniMax M3 chat completion
minimax_music.py                # MiniMax BGM API
voxcpm_tts.py                   # VoxCPM2 本地推理（mlx-audio）
doubao_*.py                     # 豆包 / 火山 PodcastTTS WebSocket

config.py                       # Config singleton wrapping config.json
config.example.json             # 模板（**已 git 跟踪**）
exceptions.py                   # Custom exception hierarchy
logger.py                       # Per-module logger
network_retry.py                # retry-with-backoff

tests/                          # pytest 单测 + 集成
docs/superpowers/               # 设计文档（specs + plans）
```

---

## API 速查

所有 API 都挂在 <http://127.0.0.1:8765/api/> 下：

| Method | Path | 用途 |
|---|---|---|
| GET  | `/`                          | 主页（index.html）|
| GET  | `/api/version`               | 后端版本 + features |
| GET  | `/api/credentials`           | 凭证加载状态（prefix only，不返明文）|
| POST | `/api/parse`                 | 脚本解析 |
| POST | `/api/synthesize/voxcpm`     | VoxCPM2 本地合成（异步 + WebSocket）|
| POST | `/api/synthesize/minimax`    | MiniMax 云端合成 |
| POST | `/api/synthesize/doubao`     | 豆包云端合成 |
| POST | `/api/script/generate`       | AI 脚本生成（MiniMax M3）|
| GET  | `/api/scripts/recent`        | 历史脚本 |
| POST | `/api/scripts/save`          | 手动存盘 |
| GET  | `/api/voices`                | 音色库列表 |
| POST | `/api/voices`                | 上传新音色（multipart）|
| GET  | `/api/voices/{vid}/audio`    | 试听 ref audio |
| DELETE | `/api/voices/{vid}`        | 删除音色 |
| WS   | `/ws`                        | 实时 log / progress / done / error |

---

## Script 格式

**新格式**（推荐）：

```
---

【主播甲】
大家好，欢迎收听。

---

【主播乙】
今天聊脾脏硬度。
```

**老格式**（向后兼容）：

```
[甲]: 大家好
主播乙：今天聊脾脏硬度
A: hello
```

Web 后端先尝试鲁棒 parser（`---` + 全角 `【】`），失败再 fallback 到正则
（覆盖 `甲/乙/A/B/1-9` + 全/半角冒号）。**400 错误不再静默 fallback**。

---

## Testing

```bash
# 单测
pytest

# GUI 稳定性烧录（PySide6 时代留下，新版 web 看板暂未适配）
~/.podcast-automation/.venv/bin/python tests/stability_burn.py --duration 300
```

---

## Known limitations

- **macOS-only voice library 路径**：`voice_library.py` hardcode
  `~/Library/Application Support/...`。Linux/Windows 需 XDG/AppData 支持。
- **API key 在 config.json 明文**：任何有文件读权限的进程都能拿到。
  OS keyring 集成在 roadmap。
- **豆包无 per-segment progress**：只能在 done / error 边界报状态，中间过程走 WS log。
- **AI 生成强依赖 MiniMax M3**：无 key 时 AI 生成按钮不可用，但脚本粘贴 / 导入路径独立可用。
- **VoxCPM2 限 Apple Silicon + Python 3.10+**：其他平台只能跑云端引擎。

---

## License

Personal use. No license specified.
