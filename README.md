# 患者科普播客生成器 · Patient Podcast Generator

> **v0.4.0-web** · 把文档/URL/粘贴文本转成甲乙双主播科普音频。
> 底层接 3 个 TTS 引擎：**豆包火山**（云端）/ **MiniMax**（云端）/ **VoxCPM2**（本地 Apple Silicon）。
> 配套 FastAPI Web 看板（替代旧 PySide6 GUI）+ 全屏 `/editor` 脚本编辑器。
> **v0.4.0 新增**：SQLite + 用户系统（bcrypt + session cookie）+ 遗留 JSON 自动迁移 + 孤儿合成任务修复。

Originally built for a doctor producing patient-education audio content.

---

## 目录

- [快速上手](#快速上手)
- [平台支持矩阵](#平台支持矩阵)
- [详细安装](#详细安装)
- [配置 API 凭证](#配置-api-凭证)
- [首次使用 4 步走](#首次使用-4-步走)
- [核心功能](#核心功能)
- [脚本格式规范](#脚本格式规范)
- [音色库管理](#音色库管理)
- [全屏 /editor 编辑器](#全屏-editor-编辑器-v031-web-新增)
- [架构总览](#架构总览)
- [HTTP API 速查](#http-api-速查)
- [测试](#测试)
- [v0.4.0-web 新增 数据库 + 用户系统](#v040-web-新增数据库--用户系统)
- [CHANGELOG](#changelog)
- [故障排查](#故障排查)
- [已知局限](#已知局限)
- [License](#license)

---

## 快速上手

```bash
# 1. 克隆
git clone https://github.com/erongcao/podcast-generator.git
cd podcast-generator

# 2. 装 Python 依赖（launcher 会自动用 ~/.podcast-automation/.venv/）
~/.podcast-automation/.venv/bin/pip install -r requirements.txt  # 或 bin/podcast-gui.sh --install-missing

# 3. 填凭证
cp config.example.json config.json
# 编辑 config.json：填 minimax.api_key 和 volcengine.app_id / access_token
# （没填也能跑看板；只是 TTS 引擎报"凭证缺失"。本地 VoxCPM2 不需要凭证。）

# 4. 启动
bin/podcast-gui.sh   # macOS 用户也可双击 bin/podcast-gui.command
```

浏览器自动开 <http://127.0.0.1:8765/>。看到主页就算成功。

**第一次启动会自检：**
- ✅ fastapi / uvicorn / requests（必需）
- ⚠️ VoxCPM2（仅 Apple Silicon）
- ⚠️ MiniMax / 豆包 / 音色库（按需）

---

## 平台支持矩阵

| 平台 | 看板 UI | 豆包云端 | MiniMax 云端 | VoxCPM2 本地 |
|------|--------|---------|-------------|------------|
| **macOS Apple Silicon (M1+)** | ✅ | ✅ | ✅ | ✅ |
| **macOS Intel** | ✅ | ✅ | ✅ | ❌（不支持 mlx-audio）|
| **Linux x86_64** | ✅ | ✅ | ✅ | ❌ |
| **Windows 10/11** | ✅ | ✅ | ✅ | ❌ |

> **VoxCPM2 本地引擎**绑死 `mlx-audio` 框架，只在 Apple Silicon + Python 3.10+ 编译通过。
> 其他平台想跑本地 TTS 暂无替代方案（roadmap：Qwen3-TTS MLX 适配 — 待评估）。

---

## 详细安装

### 前置依赖

- **Python ≥ 3.10**（macOS 用 `brew install python@3.11`，Linux 用 `python3.11`）
- **ffmpeg**（macOS: `brew install ffmpeg`；Linux: `apt install ffmpeg`；Windows: 官网下载）
- **git + 现代浏览器**（Chrome / Safari / Firefox 都行）

### 推荐：使用 launcher 自带的管理 venv

`bin/podcast-gui.sh` 默认锁定 `~/.podcast-automation/.venv/`（`PODCAST_VENV` 环境变量可覆盖）。原因：mlx-audio 不能装在 anaconda base / 系统 python 里，会编译失败。

```bash
# 创建 venv
uv venv ~/.podcast-automation/.venv --python 3.11
# 或：python3.11 -m venv ~/.podcast-automation/.venv

# 装核心依赖
~/.podcast-automation/.venv/bin/pip install -r requirements.txt

# 装 Web 看板需要的额外包（launcher --install-missing 会自动装）
~/.podcast-automation/.venv/bin/pip install fastapi uvicorn python-multipart requests

# 可选：装 VoxCPM2 本地引擎（仅 macOS Apple Silicon）
# 1. 克隆 mlx-audio 源码（必须 editable install，PyPI 包没 voxcpm2）
git clone https://github.com/Blaizzy/mlx-audio.git ~/.podcast-automation/mlx-audio
# 2. 把 mlx-audio 的 voxcpm2 子模块软链到 site-packages（绕过 site-packages 同名冲突）
ln -sf ~/.podcast-automation/mlx-audio/mlx_audio \
    ~/.podcast-automation/.venv/lib/python3.11/site-packages/mlx_audio
# 3. pip install -e
~/.podcast-automation/.venv/bin/pip install -e ~/.podcast-automation/mlx-audio
# 4. 首次启动从 ModelScope 下载 VoxCPM2 bf16 权重（≈ 4.6 GB）

# 验证：launcher 自检
bin/podcast-gui.sh --doctor
```

### 不用 launcher，直接 uvicorn

```bash
cd podcast-generator
uvicorn gui.web.app:app --host 127.0.0.1 --port 8765
```

浏览器开 http://127.0.0.1:8765/。

---

## 配置 API 凭证

### 1. MiniMax（云端 chat + TTS）

注册 [MiniMax Open Platform](https://api.minimaxi.com) 控制台：

1. 注册账号 → 完成实名认证
2. 「账户管理 → API Keys」创建一个新 key
3. **关键：region 必须是 `cn`**（global 端点拒签 MiniMax key，会报"invalid api key"）
4. 把 key 填进 `config.json`：
   ```json
   "minimax": {
     "api_key": "eyJhbGc...",
     "api_base": "https://api.minimaxi.com"
   }
   ```
5. 「套餐管理」充值（Token Plan ≠ Open Platform 余额，充值对应平台）

**MiniMax 收费点：**
- M3 chat completion（脚本生成）
- voice_clone（克隆你自己的音色）
- T2A / T2A v2（语音合成）

### 2. 火山引擎 / 豆包（云端对话 TTS）

注册 [火山引擎控制台](https://console.volcengine.com)：

1. 注册 → 实名认证
2. 「语音技术 → 语音合成」开通 **PodcastTTS（豆包对话式）** 服务
3. 创建应用，拿到 `APP ID` + `Access Token`
4. 把这两个填进 `config.json`：
   ```json
   "volcengine": {
     "app_id": "1234567890",
     "access_token": "abcd...",
     "resource_id": "volc.service_type.10050",
     "app_key": "...",
     "wss_url": "wss://openspeech.bytedance.com/api/v3/sami/podcasttts"
   }
   ```
5. 充值（按字符数计费）

**豆包的优势：**
- 中文播客腔调最自然，甲乙对话流式合成
- 内置 BGM（可在 config 调）
- 无需自备音色

### 3. 不配凭证也能做的事

- 看板 UI 全功能可用（parse / save / 音色库 CRUD / 预览 / 下载 md）
- VoxCPM2 本地合成（如果装好的话）
- 选 MiniMax / 豆包 引擎会直接报"凭证未配置" — 这是正常的，按需补

### 4. 环境变量 fallback

如果不想写 `config.json`，可以直接 export（launcher 自动读）：

```bash
export MINIMAX_API_KEY="eyJhbGc..."
export MINIMAX_APP_ID="..."        # 豆包 APP ID
export MINIMAX_TOKEN="..."          # 豆包 Access Token
```

后端查找顺序：**HTTP body > `config.json` > 环境变量**。

---

## 首次使用 4 步走

### 第 1 步：解析一段脚本

打开 http://127.0.0.1:8765/editor ，贴入下方文本：

```
---

【主播甲】
大家好，欢迎收听本期播客。

---

【主播乙】
今天我们聊一个常见但容易被忽视的健康话题。
```

等 500ms → 顶部状态栏显示"ⓘ 2 段 · 36 字" → 右侧 sidebar 列出 2 行。✅ 解析通路通。

### 第 2 步：选/录一个音色

看板主页左侧「音色库」：

- **方案 A — 用 MiniMax voice_design 在线造**：
  在主页输入主题 + 人数 → 引擎选「minimax」→ 生成时会自动造甲乙两个音色。
- **方案 B — 录自己的参考音**：
  点「麦克风录制」按钮 → 念 30 秒普通话 → 上传。voice_id 自取（如 `my-voice-001`）。
- **方案 C — 用豆包内置发音人**：
  不上传任何音色，选豆包引擎时自动用其内置音色（无需 voice_id）。

### 第 3 步：合成

选引擎 → 点「生成」：

| 引擎 | 何时选 |
|------|--------|
| **豆包** | 中文播客腔最自然 / 内置音色 / 最便宜 |
| **MiniMax** | 想要克隆你自己的声音 / 多语种 |
| **VoxCPM2** | 完全离线 / 数据不出本机 / Mac Apple Silicon |

后台异步跑，进度通过 WebSocket 推。完成后弹出播放器。

### 第 4 步：保存 + 重播

音频自动落到 `output/` 目录；同时进「最近 5 个生成结果」列表。

---

## 核心功能

### TTS 引擎

| 引擎 | 网络 | 中文质量 | 离线 | 自带音色 | 自定义音色 |
|------|------|---------|------|---------|-----------|
| 豆包 | 必需 | ⭐⭐⭐⭐⭐ | ❌ | ✅ 内置 | ❌ |
| MiniMax | 必需 | ⭐⭐⭐⭐ | ❌ | ✅ 多套 | ✅ voice_clone |
| VoxCPM2 | 不需要 | ⭐⭐⭐⭐ | ✅ | ✅ 12 套 | ✅ 零样本（ref_audio + demo_text）|

### AI 脚本生成（MiniMax M3 chat）

3 个入口：
1. 主页 AI 生成（输入主题 → LLM 写脚本）
2. /editor 顶部"AI 生成脚本"
3. 直接粘贴/导入历史脚本（不走 LLM）

### 全屏 /editor 脚本编辑器（v0.3.1-web 新增）

新版完整功能：[跳到 /editor 章节](#全屏-editor-编辑器-v031-web-新增)

### 其他

- **每主播速度滑块**（0.5–1.5×，foreign 音色 0.85，native 中文 1.0）
- **跨语言音色自动调速**：native 英文 ref 音合中文需 speed≈0.85
- **响应式布局**：< 900px 自动堆叠，< 500px 紧凑模式
- **音频历史**：最近 5 个生成结果，可重播 / 删除
- **WebSocket 实时日志**：进度 / 完成 / 错误事件流

---

## 脚本格式规范

### 推荐格式（v0.3.1-web 默认）

```
---

【主播甲】
大家好，欢迎收听。

---

【主播乙】
今天聊脾脏硬度。
```

**3 个关键约定：**
1. `---` 一行（半角三个减号）作段间分隔
2. speaker marker 用全角 `【主播甲】`，**marker 独立成行**
3. 段正文紧随 marker 后（可换行）

### 向后兼容格式（旧 PySide6 时代）

```
[甲]: 大家好
主播乙：今天聊脾脏硬度
A: hello
```

后端 parser 先尝试鲁棒格式（`---` + 全角 `【】`），失败再 fallback 到正则（覆盖 `甲/乙/A/B/1-9` + 全/半角冒号）。

**注意：** 解析失败 → HTTP 400（不再静默 fallback 把全部塞给主播甲独白）。

### 编辑器里的格式提示

点 /editor 后自动提示：
- 顶部状态栏 `ⓘ N 段 · M 字` 实时更新
- sidebar 列出每段 speaker + 字数，点击跳转到 textarea 对应位置

---

## 音色库管理

### 存储位置

```
~/Library/Application Support/PodcastGenerator/voices/
├── voices.json         # 音色元数据（voice_id, tags, ref_text, created_at）
└── audio/              # 参考音频 wav 文件
    ├── my-voice-001.wav
    └── talarico-clone-001.wav
```

> ⚠️ **Linux/Windows 用户**：当前路径 hardcode macOS 风格。
> 临时方案：自己 `mkdir -p ~/Library/Application\ Support/PodcastGenerator/voices` 软链到 `$XDG_DATA_HOME`。
> 永久方案在 roadmap。

### 创建音色

**方式 A — 麦克风录制**：
看板 → 「麦克风录制」按钮 → 选 30 秒普通话稿 → 录 → 自动上传 + 注册。

**方式 B — 上传文件**：
多部分表单上传：voice_id + display_name + tags + ref_text + wav 文件。

**方式 C — MiniMax voice_clone**：
用 MiniMax API 克隆你自己的声音（已有 voice_id 后，看板自动同步）。

### 标签系统（推荐显式 tag）

```json
{
  "voice_id": "my-voice-001",
  "display_name": "我的主播音",
  "tags": ["female", "voice_design"],
  "ref_text": "大家好，欢迎收听本期健康科普..."
}
```

引擎选择时按 tag 路由：
- `female` → 优先匹配女性音色
- `foreign` → 合中文时自动降速 0.85×
- `voice_design` → 启用 MiniMax voice_design 后处理

---

## 全屏 /editor 编辑器（v0.3.1-web 新增）

打开方式：主页 → 顶部脚本区上方 → 「📝 在全屏编辑器中打开」。
或直接访问 <http://127.0.0.1:8765/editor>（也可 `?load=<script_name>.json` 直接载入历史脚本）。

### 功能矩阵

| 区域 | 控件 | 行为 |
|------|------|------|
| 顶栏 | 字号 A+ / A− | 调 textarea 字号（14–26px，localStorage 持久化）|
| 顶栏 | 全屏 ⛶ | CSS class 切换（不依赖浏览器 Fullscreen API）|
| 顶栏 | 载入历史下拉 | 显示最近 20 个脚本，按 mtime 倒序 |
| 工具栏 | 保存 / 放弃 | dirty 调 + Cmd+S → `/api/scripts/save` |
| 工具栏 | 下载md ▾ | 原文 / 渲染 md（dirty 时直接 Blob，干净时走 server endpoint）|
| 工具栏 | 预览 ◐ | 切到客户端渲染的 HTML 预览（XSS-safe escape）|
| 工具栏 | ⓘ 状态 | 段数 / 字数 / 各 speaker 段数 |
| 侧栏 | 段列表 | 点击 → 跳到对应 `【speaker】` marker |
| 整页 | Cmd+W / 关闭 | dirty 时弹原生确认 |

### WebSocket 实时同步

主页 AI 生成脚本时，/editor 标签页 3-4 秒后弹 toast：
> 「主页 AI 生成了新脚本「脾硬化」，点击加载？」

点 toast → 内容灌入 → dirty=false（dirty 时会 confirm 是否覆盖）。

### 离开拦截

```javascript
window.addEventListener('beforeunload', e => {
    if (state.dirty) {
        e.preventDefault();
        e.returnValue = '';
    }
});
```

### 详细规格

`docs/superpowers/specs/2026-10-11-script-editor-design.md`

---

## 架构总览

```
bin/
  podcast-gui.sh                # 唯一入口：venv 卡口 + 自检 + 启 uvicorn
  podcast-gui.command           # macOS Finder 双击包装

gui/web/                        # 当前默认（FastAPI + HTML 看板，v0.3.x）
  app.py                        # FastAPI app + 路由注册（13 endpoints + WS）
  ws.py                         # WebSocketManager（线程 → 事件循环）
  api/
    parse.py                    # 鲁棒脚本解析（--- + 全角【】 + 老格式）
    synthesize.py               # 3 引擎合成（VoxCPM / MiniMax / 豆包）
    voices.py                   # 音色库 CRUD
    script_gen.py               # MiniMax M3 AI 脚本生成
    editor.py                   # /editor 导出 .md 端点 + 安全文件名
  static/
    index.html                  # 两栏看板
    editor.html                 # /editor 全屏编辑器
    css/theme.css               # GHIBLI 配色 + 响应式
    css/editor.css              # /editor 专属样式（顶栏/textarea/sidebar/drawer）
    js/app.js                   # WS 重连 / 速度滑块 / 音频历史
    js/editor.js                # /editor 完整逻辑（state/WS/parse/save/preview）

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

## HTTP API 速查

所有 API 都挂在 <http://127.0.0.1:8765/> 下：

| Method | Path | 用途 |
|---|---|---|
| GET  | `/`                          | 主页（index.html）|
| GET  | `/editor`                     | 全屏脚本编辑器（v0.3.1-web 新增）|
| GET  | `/health`                     | 健康检查 |
| GET  | `/api/version`                | 后端版本 + features |
| GET  | `/api/credentials`            | 凭证加载状态（prefix only，不返明文）|
| POST | `/api/parse`                  | 脚本解析 |
| POST | `/api/synthesize/voxcpm`      | VoxCPM2 本地合成（异步 + WebSocket）|
| POST | `/api/synthesize/minimax`     | MiniMax 云端合成 |
| POST | `/api/synthesize/doubao`      | 豆包云端合成 |
| POST | `/api/script/generate`        | AI 脚本生成（MiniMax M3）|
| GET  | `/api/scripts/recent`         | 历史脚本列表 |
| GET  | `/api/scripts/get`            | 单个历史脚本（/editor 载入用）|
| POST | `/api/scripts/save`           | 手动存盘 |
| GET  | `/api/scripts/export`         | 导出 .md（raw / rendered）|
| GET  | `/api/voices`                 | 音色库列表 |
| POST | `/api/voices`                 | 上传新音色（multipart）|
| GET  | `/api/voices/{vid}/audio`     | 试听 ref audio |
| DELETE | `/api/voices/{vid}`         | 删除音色 |
| WS   | `/ws`                         | 实时 log / progress / done / script_done / error |

---

## 测试

```bash
# 单测
~/.podcast-automation/.venv/bin/python -m gui.web.tests.test_parse
~/.podcast-automation/.venv/bin/python -m gui.web.tests.test_export

# pytest 全套
cd ~/Documents/podcast-generator
~/.podcast-automation/.venv/bin/python -m pytest gui/web/tests/

# 看板稳定性（PySide6 时代留下，web 看板暂未适配）
~/.podcast-automation/.venv/bin/python tests/stability_burn.py --duration 300
```

---

## 故障排查

### 启动期"❌ env 无法导出" / `ModuleNotFoundError: No module named 'fastapi'`

launcher 默认锁定 `~/.podcast-automation/.venv/`。如果你有别的 venv，用：

```bash
PODCAST_VENV=/path/to/your/venv bin/podcast-gui.sh
```

或一键补依赖：

```bash
bin/podcast-gui.sh --install-missing
```

### "invalid api key"（MiniMax）

- 99% 是 region 错。`api_base` 必须是 `https://api.minimaxi.com`（不是 `api.MiniMax.io`）。
- 检查 key 是否过期：`MiniMax 控制台 → 账户管理 → API Keys`

### VoxCPM2 "mlx_audio.tts.models.voxcpm2 not found"

PyPI 包没 voxcpm2 子模块。必须 editable install：

```bash
git clone https://github.com/Blaizzy/mlx-audio.git ~/.podcast-automation/mlx-audio
ln -sf ~/.podcast-automation/mlx-audio/mlx_audio \
    ~/.podcast-automation/.venv/lib/python3.11/site-packages/mlx_audio
~/.podcast-automation/.venv/bin/pip install -e ~/.podcast-automation/mlx-audio
```

### /editor 解析失败 → 整段塞给主播甲

说明脚本里没有 `---` 分隔符 + 全角 `【主播甲】` marker。后端 parser 先鲁棒尝试，失败 fallback。

**修法**：把脚本改成 v0.3.1-web 推荐格式（见「脚本格式规范」章节）。

### 豆包 WSS 连接超时

- 检查 `volcengine.wss_url` 是否被公司代理拦截
- 检查火山引擎账户是否欠费（控制台 → 余额）

### 看板能开，但所有引擎报"凭证未配置"

- 检查 `config.json` 路径：`bin/podcast-gui.sh --doctor` 会打「config.json: ✓」或「❌」
- 确认 JSON 格式合法（不能有注释尾逗号）
- 字段名大小写：`api_key` vs `API_KEY`（必须前者）

### 端口 8765 被占用

```bash
bin/podcast-gui.sh --port 9000
```

### 浏览器没自动打开

```bash
bin/podcast-gui.sh --no-browser   # 关掉自动开
# 然后手动开 http://127.0.0.1:8765/
```

---

## 已知局限

- **macOS-only voice library 路径**：`voice_library.py` hardcode `~/Library/Application Support/...`。Linux/Windows 需 XDG/AppData 支持。
- **API key 在 config.json 明文**：任何有文件读权限的进程都能拿到。OS keyring 集成在 roadmap。
- **豆包无 per-segment progress**：只能在 done / error 边界报状态，中间过程走 WS log。
- **AI 生成强依赖 MiniMax M3**：无 key 时 AI 生成按钮不可用，但脚本粘贴 / 导入路径独立可用。
- **VoxCPM2 限 Apple Silicon + Python 3.10+**：其他平台只能跑云端引擎。
- **/editor 单文件 6xx 行 — 单视图**：未拆 CodeMirror/Monaco（YAGNI：原生 textarea 够用）

---

## v0.4.0-web 新增（数据库 + 用户系统）

v0.3.x 仅在内存里跑（脚本和音色库都是 JSON 文件）。v0.4.0-web 引入了 SQLite + SQLAlchemy 2.x，
所有数据持久化到 `~/.podcast-automation/podcast.db`，配套 bcrypt 认证 + cookie 会话。

### 新增能力

- **用户系统**：注册 / 登录 / 退出 / 当前用户（`/api/auth/*`）
  - 第一个注册的用户自动获得管理员权限（bootstrap）
  - bcrypt cost=12，session cookie 自动滚动 7 天
  - 所有认证事件写入 `audit_log` 表（带 IP / UA）

- **数据库表**（8 张）：`User` / `Article` / `Script` / `Synthesis` / `Output` / `VoiceClone` / `ApiKey` / `AuditLog`
  - 多租户隔离：每个 domain 表都有 `owner_id` 索引
  - 级联：删除 owner → 删除其 articles + voices + syntheses；删除 article → 保留 script 但清空 article_id
  - 软删除：`deleted_at` 字段
  - 迁移用 Alembic：自动生成 + 安全降级

- **遗留 JSON 自动迁移**：运行一次 `python -m gui.web.migrate_legacy`
  - 从 `~/Library/Application Support/PodcastGenerator/voices/voices.json` 导入音色
  - 从 `~/Library/Application Support/PodcastGenerator/scripts/script_*.json` 导入脚本
  - 幂等：可重复运行
  - 原始 JSON 文件不会被删除（保留作为备份）

- **孤儿自动修复**：进程被 OOM 杀掉时，Synthesis 表可能留 `status="running"` 行。启动时会扫描 `started_at` 超过 1 小时未结束的，标记为 `failed` 配 `error_message="orphaned by process restart"`

### 安全注意事项

- **会话密钥**：默认自动生成并存到 `~/.podcast-automation/session-secret`（mode 0600）。
  生产环境请通过 `SESSION_SECRET` 环境变量显式注入。
- **HTTPS**：当前 `https_only=False` 仅适用于本地开发。生产环境需要反向代理（nginx/caddy）
  + 设 `https_only=True`。
- **首个管理员**：部署新机器后第一个注册的人 = admin。如果不想这么干，先用
  `PODCAST_BOOTSTRAP_TOKEN` 启用一次性的注册令牌（v0.5+ 添加）。

### 新增 API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/auth/register` | 注册新用户（首个自动 admin） |
| `POST` | `/api/auth/login` | 登录（设置 session cookie） |
| `POST` | `/api/auth/logout` | 退出（清 session） |
| `GET` | `/api/auth/me` | 获取当前登录用户（401 if not logged in） |

### DB 路径自定义

```bash
export PODCAST_DATABASE_URL="postgresql://user:pass@host/db"  # 也支持 PG / MySQL
```

注意：用 Postgres 时 SQLite 才支持的 `check_same_thread=False` 不再需要，但需要单独建 PG 实例 + 修改 DSN。

### 升级到 v0.4.0-web

```bash
cd ~/Documents/podcast-generator
git pull
pip install -r requirements.txt  # 新增 sqlalchemy / alembic / bcrypt / itsdangerous
alembic upgrade head             # 应用迁移到现有 DB
python -m gui.web.migrate_legacy # 从 JSON 导入历史数据
python -m pytest gui/web/tests/  # 跑测试
```

---

## CHANGELOG

### v0.4.0-web (2026-10-12)

- 新增 SQLite + SQLAlchemy 2.x + Alembic 数据库
- 新增用户系统：注册 / 登录 / bcrypt + session cookie
- 新增遗留 JSON 自动迁移（幂等）
- 新增启动时孤儿合成任务自动修复
- 新增顶层 audit_log 表 + 4 个事件类型（register / login / login.failed / logout）

### v0.3.1-web (2026-10-10)

- 全屏脚本编辑器 `/editor`
- 段列表、字号、预览、下载 md
- 完整 README 重写

---

## License

Personal use. No license specified.

---

**版本：v0.3.1-web（2026-10-11）· git tag `v0.3.1-web` · commit `22005d6`**