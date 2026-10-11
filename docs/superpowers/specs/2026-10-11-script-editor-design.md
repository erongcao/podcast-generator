# 脚本编辑器（/editor 全屏页）设计

**日期**：2026-10-11
**作者**：Claude + 用户
**状态**：草案 v1
**关联背景**：用户在做医学科普播客，主页（PySide6 旧版 → 2026-10-10 Web 看板）的脚本编辑区 `<textarea rows="8">` 太窄，长脚本（典型 32 段 ~5KB）调字号、找跳段、Markdown 导出都不友好。**兴趣驱动场景**，不是商业 SaaS。

---

## 目标

点击主页脚本区（或主页新按钮），打开 `/editor` 全屏路由，独占整屏，做专业编辑器体验：

- 调字号、全屏切换
- 段列表侧栏，点击跳转
- 实时 parse 计数（段数 / 字数 / 主播分布）
- Markdown 预览 + 下载 .md 文件
- 手动保存 + 离开提示

## 非目标（YAGNI）

- ❌ 多人协作 / 锁 / 版本控制 —— 单人单机
- ❌ 自动保存草稿 —— 怕偷偷存盘污染主列表
- ❌ CodeMirror / Monaco —— 5KB 文本用原生 textarea 已足够
- ❌ 富文本所见即所得 —— 用户偏好 markdown 纯文本

---

## 第 1 节：架构 & 文件布局

### 新增文件

```
gui/web/
├── static/
│   ├── editor.html                 # /editor 路由服务
│   ├── js/
│   │   ├── editor.js               # 编辑器全部逻辑（~200 行）
│   └── css/
│       └── editor.css              # 编辑器专属样式（~50 行）
└── api/
    └── editor.py                   # preview 渲染 + export 路由（~120 行）
```

### 修改文件

| 文件 | 改动 |
|------|------|
| `gui/web/app.py` | 加 `GET /editor` 路由 + 注册 editor router（FastAPI APIRouter） |
| `gui/web/api/script_gen.py` | 加 `preview_impl(text)` 和 `export_impl(name, format)` 两个函数 |

### 不动的文件

- `parse.py`（复用 `/api/parse` 拿 segments）
- `synthesize.py` / `voices.py`（无关）
- `index.html` / `app.js`（主页加一个"📝 打开编辑器"按钮跳转 `/editor`，**target="_blank"**）

### 路由表（新增）

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/editor` | 返回 `editor.html` |
| GET | `/editor?load=<script_name>` | 同上，URL 带 load 参数自动灌入脚本 |
| POST | `/api/scripts/preview` | body `{text}` → `{html, segments, n_segments, speakers, char_count}` |
| GET | `/api/scripts/export?name=<>&format=<raw\|rendered>` | 流式下载 .md 文件 |

---

## 第 2 节：API 详细设计

### `POST /api/scripts/preview`

**Request**
```json
{ "text": "---\n【主播甲】\ntext..." }
```

**Response 200**
```json
{
  "html": "<hr>\n<h2>主播甲</h2>\n<p>text...</p>\n<hr>...",
  "segments": [
    {"speaker": "主播甲", "text": "..."},
    {"speaker": "主播乙", "text": "..."}
  ],
  "n_segments": 32,
  "speakers": {"主播甲": 16, "主播乙": 16},
  "char_count": 5862
}
```

**Response 400**
```json
{ "detail": "parse 失败: 找不到 speaker marker" }
```

**实现要点**：
- 调现有 `gui.web.api.parse.parse_script_lines(text)` 拿 segments
- 渲染：每段输出 `<hr>\n<h2>{speaker}</h2>\n<p>{text}</p>`（第一段的 `<hr>` 也输出，方便预览里看段分隔）
- 用 Python `html.escape()` 防 XSS（用户脚本是医生写的医学内容，可能含 `<` `>`）
- 字符统计用 `len(text)`

### `GET /api/scripts/export?name=...&format=...`

**用途**：从已保存的脚本下载 .md 文件。

**Query 参数**
- `name`：`script_20261011_145102_脾硬化.json`（与 `load_script_impl` 同套验证：必须 `script_*.json`、无 `/`、无 `..`）
- `format`：`raw` | `rendered`
  - `raw` → 直接下 `editor_text` 原内容
  - `rendered` → 把 `segments` 转成渲染后的 markdown 文本再下

**Response 200** — `Content-Disposition: attachment; filename="<topic>.md"`
- `Content-Type: text/markdown; charset=utf-8`
- Body 直接是 .md 文件内容

**Response 404**：`name` 不存在或 `format` 非法

**实现要点**：
- 文件名用 `topic`（去掉非法字符 `<>:"/\\|?*`），fallback 到 `<name>.md`
- 复用 `load_script_impl`读 JSON
- 渲染格式：
  ```markdown
  ---
  
  ## 主播甲
  
  body text...
  
  ---
  
  ## 主播乙
  
  body text...
  ```

### 复用 vs 新增端点清单

| 端点 | 状态 | 说明 |
|------|------|------|
| `GET /api/scripts/recent` | 已有 | 编辑器顶部"载入历史"下拉复用 |
| `GET /api/scripts/get` | 已有（2026-10-11 新加） | 编辑器 URL `?load=` 参数走这个 |
| `POST /api/scripts/save` | 已有 | "保存"按钮复用 |
| `POST /api/scripts/preview` | **新增** | 预览面板 |
| `GET /api/scripts/export` | **新增** | 下载按钮 |

---

## 第 3 节：前端页面布局 + JS 模块

### 页面布局（editor.html）

```
┌─────────────────────────────────────────────────────────────────┐
│ ☰  [返回主页]  │ 脾硬化 · 已修改 ✱ │ [字号 − A+] [全屏] │ │  ← 顶栏 (sticky)
├─────────────────────────────────────────────────────────────────┤
│ [保存]    [放弃]  [下载md ▾]  [预览 ◐]  [替换]    │ ⓘ 32段 · 5862字 │
├──────────────────────────────────────┬──────────────────────────┤
│                                      │ 段列表 (sidebar)          │
│  ┌────────────────────────────────┐  │ ────────                 │
│  │                                │  │ [01] 主播甲  128字        │
│  │   <textarea>                    │  │ [02] 主播乙  132字        │
│  │   原始脚本编辑区                 │  │ [03] 主播甲 115字        │
│  │                                │  │ ...                       │
│  │                                │  │                          │
│  │                                │  │ 点击段 → 光标跳到该行    │
│  │                                │  │                          │
│  │                                │  │                          │
│  │                                │  │                          │
│  │                                │  │                          │
│  │                                │  │                          │
│  └────────────────────────────────┘  │                          │
│                                      │                          │
│   （预览模式：textarea 隐藏，左侧或  │                          │
│    替换为渲染后的 HTML）              │                          │
└──────────────────────────────────────┴──────────────────────────┘
```

### CSS 布局

- **grid-template-columns: 1fr 280px** —— 左编辑区 + 右段列表
- 顶栏 sticky `top: 0; z-index: 100`
- 全屏模式：`<html>` 加 `class="fullscreen"`，编辑器区 `position: fixed; inset: 0; z-index: 9999`
- 字号：用 CSS variable `--editor-font-size`，按钮 ± 改这个变量
- 复用 `theme.css` 的色板变量（`--color-*`），不重复定义

### JS 模块（editor.js ~200 行）

```
editor.js
├── State { dirty, snapshot, fontSize, mode: 'edit'|'preview', topic }
├── Init
│   ├── 读 URL ?load=<name> → fetch /api/scripts/get 灌入
│   ├── 绑定顶栏按钮（保存/放弃/下载/预览/字号/全屏/替换）
│   ├── 绑定 sidebar 跳转
│   └── 装 beforeunload dirty 检查
├── Save / Discard / Export
├── Font size / Fullscreen
├── Cmd+H 自定义替换（覆盖浏览器 hide window 行为，仅在编辑器内）
├── Parse debounce（500ms，复用主页模式）
│   └── 触发 /api/parse → 更新右上 ⓘ 文本 + 渲染 sidebar 段列表
└── Preview toggle
    ├── mode='preview' → fetch /api/scripts/preview → 注入左侧（替换 textarea）
    └── mode='edit' → 恢复 textarea + sidebar
```

### 与主页的衔接

主页 `index.html` 加一个小跳转按钮：

```html
<a href="/editor" target="_blank" class="btn btn-secondary">📝 打开编辑器</a>
```

- `target="_blank"` 用户并排对照参考主页
- 不在主页点"加载历史"后自动跳编辑器 —— 编辑器自己也能从下拉载入（用 `?load=` URL 参数）

### 复用 vs 重写

| 功能 | 主页 | 编辑器 |
|------|------|--------|
| parse debounce (500ms) | ✅ | ✅ 复用同款 |
| 段数统计 | ✅ | ✅ 顶栏右侧 ⓘ |
| 历史下拉 | ✅ | ✅ 编辑器自己带下拉（不依赖主页） |
| 实时预览 | ❌ | ✅ 编辑器独有 |
| 全屏 | ❌ | ✅ 编辑器独有 |
| Cmd+H 替换 | ❌ | ✅ 编辑器独有 |

---

## 第 4 节：交互时序 + 边界情况

### 4.1 初始加载

```
URL ＝ /editor
  → editor.html 加载
  → State.dirty = false, snapshot = ''（空编辑器）
  → 右侧段列表："暂无段落"
  → 顶栏标题："未命名脚本"

URL ＝ ?load=script_xxx_脾硬化.json
  → fetch /api/scripts/get?name=...
  → 200 → editor.value = json.editor_text; snapshot = editor.value; topic = json.topic
  → parse debounce 500ms 后段列表填满
  → 顶栏标题："脾硬化"
  → 404 → toast "加载失败: <name>", 编辑器仍空
```

### 4.2 编辑循环

```
input事件
  → if dirty=false → 设 dirty=true, 顶栏标题加 " ✱" 后缀
  → clearTimeout(debounce); debounce = setTimeout(refreshParse, 500)
  → 500ms 后：fetch /api/parse
     → 200 → 更新 ⓘ（段数/字数/speaker分布）+ sidebar 段列表
     → 400 → ⓘ 显示 "❌ parse 失败", sidebar 留旧值
     → 500 → 同 400
```

### 4.3 保存

```
[保存] 按钮
  → if dirty=false → toast "没有改动"
  → 按钮变灰 → fetch POST /api/scripts/save {script_text, topic}
     ← 200 → toast "✅ 已保存"; dirty=false; snapshot = editor.value
     ← 400 → toast "保存失败: ..."; dirty 不变
  → 保存后顶栏标题去 ✱

不阻塞 UI：保存请求飞行中，按钮变灰禁用，"保存中..."，结束后恢复。
```

### 4.4 放弃

```
[放弃] 按钮
  → if dirty=false → toast "没有改动"
  → else: confirm("放弃当前修改？")
     → cancel → return
     → ok → editor.value = snapshot; 触发 input 事件重 parse → dirty=false
```

### 4.5 预览切换

```
[预览 ◐] 按钮
  → mode='preview':
     → fetch POST /api/scripts/preview {text: editor.value}
       ← 200 → 隐藏 textarea + sidebar，注入 <div class="preview">{html}</div> 到左侧
       ← 400 → toast "预览失败: parse 错误"
  → mode='edit': 恢复 textarea + sidebar, preview div 隐藏
```

### 4.6 下载 md

```
[下载md ▾] 下拉
  → "原文 .md":
     → if 当前是已载入的脚本 → fetch GET /api/scripts/export?format=raw&name=<name> 触发浏览器下载
     → if 是用户从未保存过的临时内容 → 前端用 Blob 直接生成 .md，触发下载 "未命名.md"
  → "渲染 .md" → 同上但 format=rendered
```

**重要**：用户从未保存过的纯文本也能下载 —— 不强制先存。

### 4.7 Cmd+H 替换

```
textarea 内按 Cmd+H
  → e.preventDefault() 阻止浏览器 hide window（macOS Cmd+H 在 textarea 内默认行为是隐藏应用，需拦截）
  → 在编辑器右下角浮一个 inline replace bar:
    [查找: _________] [替换为: _________] [替换] [全部] [关闭]
  → 替换：从光标向下找下一个匹配，选中 → 点替换改写 → 自动找下一个
  → Cmd+G 在该区域找下一个匹配
```

### 4.8 全屏 + 字号

```
[全屏] 按钮
  → toggle <html class="fullscreen">
  → CSS: .fullscreen .editor-grid { position: fixed; inset: 0; z-index: 9999; background: white }

[字号 − A+]
  → fontSize -= 2 / += 2, clamp 12..24
  → <html style="--editor-font-size: {fontSize}pt">
  → CSS: textarea { font-size: var(--editor-font-size); line-height: 1.6 }
```

### 4.9 离开提示

```javascript
window.addEventListener('beforeunload', (e) => {
  if (state.dirty) {
    e.preventDefault();
    e.returnValue = ''; // Chrome/Edge 需要 returnValue 触发原生弹窗
    return '';
  }
});
```

注意：浏览器只显示通用提示文案（不能自定义中文），但能拦住就够了。

### 4.10 边界情况清单

| 情况 | 行为 |
|------|------|
| URL `?load=<不存在>` | toast 失败，编辑器空，不阻断 |
| parse 失败时按保存 | 仍允许保存（保存的是原文），toast 提示 "⚠ parse 不通过，保存原文？"，二次确认后存 |
| 空编辑器按保存 | 允许存（topic="未命名脚本"+空文本），不拦 |
| 同一脚本改两次保存 | 视为手动 save，每次生成新文件名（不覆盖旧文件）—— 与现有 save 行为一致 |
| 网络断开（fetch 失败） | catch → toast "网络错误:<msg>", 状态保留 |
| 段数 > 100 | sidebar 自动滚动定位，不懒加载 |
| 文本 > 50KB | parse debounce 改 1000ms（避免大脚本卡） |
| Safari Cmd+H | macOS Cmd+H 默认 hide window，textarea 内用 `e.metaKey && e.key === 'h'` 拦截 |
| 预览时按保存 | 不允许 —— 切回 edit 模式才能保存（编辑预览不可编辑是常态） |

---

## 风险 & 缓解

| 风险 | 缓解 |
|------|------|
| XSS via 用户脚本含 `<script>` | `html.escape()` 转义所有用户输入 |
| 路径穿越（`?load=../../etc/passwd`） | `load_script_impl` 已有的文件名验证（必须 `script_*.json`、禁 `/`、`..`） |
| 预览请求大 payload（50KB+） | 限文本 1MB（413 拒绝），不缓存 |
| beforeunload 在 SPA 内失效 | 用 `<a href="/editor">` 而非 SPA 路由，浏览器原生 beforeunload 即可触发 |
| 长时间挂起导致 debounce 不触发 | onbeforeunload 时若有 pending debounce，flush 一次同步 parse |
| textarea 自动换行不一致（用户偏好） | `white-space: pre-wrap` 强制保留换行，`word-wrap: break-word` 长破折号分行 |

---

## 测试要点

| 场景 | 验收 |
|------|------|
| 主页点跳转按钮 | 打开新 tab 显示 `/editor` |
| 编辑器 `?load=<name>` | URL 带 name → 自动灌入内容 + snapshot 同步 |
| 编辑 + 关闭 tab | 弹原生确认 |
| 点保存 | toast 成功，dirty 变 false，snapshot 更新 |
| 点放弃 | 编辑器内容回退到入页快照 |
| 切预览 | 左侧 textarea 换为渲染 HTML |
| 下载 .md | 浏览器下载对应文件，原文/渲染两种都试 |
| 改字号 + 全屏 | CSS variable 立即更新 |
| Cmd+H | 浮替换栏，替换+全部按钮可用 |
| parse 失败时编辑 | 顶栏 ⓘ 显示 "❌", sidebar 留旧值，但编辑仍允许 |
| XSS 注入测试 | 脚本含 `<script>alert(1)</script>` 不执行，预览里看到原文转义 |

---

## 不在本 spec

- 主页面编辑器的字号/全屏（**只在 `/editor` 提供**，主页保持 rows=8）
- 多人实时协作
- 脚本模板 / 片段库（snippet）
- 自动补全（基于历史脚本）
- 编辑历史 / redo-undo 多级（浏览器 textarea 内置 undo 够用）