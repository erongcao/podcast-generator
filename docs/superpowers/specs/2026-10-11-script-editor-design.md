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
| `gui/web/api/editor.py` | **同时**持有 export 路由和 export_impl 函数（impl 与 route 同文件，不跨模块跳） |
| `gui/web/api/script_gen.py` | 不动（保持其"脚本生成"单一职责） |

### 不动的文件

- `parse.py`（复用 `/api/parse` 拿 segments）
- `synthesize.py` / `voices.py`（无关）
- `index.html` / `app.js`（主页加一个"📝 打开编辑器"按钮跳转 `/editor`，**target="_blank"**）
- `<textarea>` 不给 `id` 或 `name` 属性（防止 Chrome/Safari 自动填充回旧版本覆盖当前编辑）

### 路由表（新增）

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/editor` | 返回 `editor.html` |
| GET | `/editor?load=<script_name>` | 同上，URL 带 load 参数自动灌入脚本 |
| ~~`POST /api/scripts/preview`~~ | **删除** | 见下节，直接扩展 `/api/parse` |
| GET | `/api/scripts/export?name=<>&format=<raw\|rendered>` | 流式下载 .md 文件 |

> **review 反馈 #1**：删除 `/api/scripts/preview`。预览 HTML 是客户端的事：客户端拿到 segments 后用 template literal 渲染即可，零额外服务。再造一个端点会让 parser 改动需要在两处同步。

---

## 第 2 节：API 详细设计

### `POST /api/scripts/preview` ~~已删除~~

**Review 反馈 #1（已采纳）**：原计划创建 `/api/scripts/preview` 端点返回 `{html, segments, n_segments, speakers, char_count}`。但 95% 内容已在 `/api/parse` 暴露，多余的 `html` 是客户端渲染，`char_count` = `len(text)` 客户端也能算。**删除该端点**。

**替代方案**：
- 编辑器调现有 `/api/parse` 拿 segments（已有）
- HTML 渲染在客户端用 template literal 完成
- 字符数 = `editor.value.length`（零成本）

### 复用 vs 新增端点清单（修订）

| 端点 | 状态 | 说明 |
|------|------|------|
| `GET /api/scripts/recent` | 已有 | 编辑器顶部"载入历史"下拉复用 |
| `GET /api/scripts/get` | 已有（2026-10-11 新加） | 编辑器 URL `?load=` 参数走这个 |
| `POST /api/scripts/save` | 已有 | "保存"按钮复用 |
| `POST /api/scripts/preview` | **删除** | 客户端渲染替代 |
| `GET /api/scripts/export` | **新增** | 下载按钮（在 `editor.py`） |

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

---

## 第 3 节：前端页面布局 + JS 模块

### 页面布局（editor.html）

```
┌─────────────────────────────────────────────────────────────────┐
│ ☰  [返回主页]  │ 脾硬化 · 已修改 ✱ │ [字号 − A+] [全屏] │ │  ← 顶栏 (sticky)
├─────────────────────────────────────────────────────────────────┤
│ [保存]    [放弃]  [下载md ▾]  [预览 ◐]   │ ⓘ 32段 · 5862字 │
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
- **窄屏适配**：`@media (max-width: 900px) { grid-template-columns: 1fr }`，sidebar 变成抽屉式，**默认收起**，顶部按钮切换显隐
- 顶栏 sticky `top: 0; z-index: 100`
- 全屏模式：`<html>` 加 `class="fullscreen"`，编辑器区 `position: fixed; inset: 0; z-index: 9999`
- 字号：用 CSS variable `--editor-font-size`，按钮 ± 改这个变量，**持久化到 `localStorage`**，避免每次开页都重置
- 复用 `theme.css` 的色板变量（`--color-*`），不重复定义
- 所有 `<button>` 加 `aria-label`（无文字按钮必须，可视按钮可选）

### JS 模块（editor.js ~200 行）

```
editor.js
├── State { dirty, snapshot, fontSize, mode: 'edit'|'preview', topic, loadedName, wsAttempts }
├── Init
│   ├── 读 URL ?load=<name> → fetch /api/scripts/get 灌入，loadedName=<name>
│   ├── 读 localStorage.editor.fontSize（try/catch 包裹，Safari 隐私模式抛错时静默 fallback 到 18px）
│   ├── 绑定顶栏按钮（保存/放弃/下载/预览/字号/全屏）
│   ├── 绑定 sidebar 跳转
│   ├── 装 beforeunload dirty 检查
│   └── 装 WebSocket 订阅 → 监听 script_done → toast "主页 AI 生成了新脚本，是否加载？"
│        └── onclose 重连：wsAttempts++，线性 backoff 1s/2s/4s（最多 3 次），失败后 toast "新脚本通道断开，需手动检查主页"
├── Save / Discard / Export
│   ├── Save 成功后更新 loadedName = newSavedName（让下载按钮走正确文件路径）
│   └── Export 原文：if dirty → Blob from editor.value; else → fetch /api/scripts/export?format=raw&name=<loadedName>
├── Font size / Fullscreen（写 localStorage，try/catch）
├── Cmd+F（浏览器原生 find，**不拦截 Cmd+H**）
├── Cmd+S → 触发保存（浏览器默认是"保存网页"，拦截换成我们的）
├── Parse debounce（500ms，文本 >50KB 改 1000ms）
│   └── 触发 /api/parse → 更新右上 ⓘ 文本 + 渲染 sidebar 段列表
└── Preview toggle（客户端渲染，零网络请求）
    ├── mode='preview' → 用 segments + escapeHtml() 渲染 HTML，注入左侧（替换 textarea）
    └── mode='edit' → 恢复 textarea + sidebar
```

**escapeHtml 实现**（防 XSS；**不能用 JS 全局 escape()，那个是 URL 编码**）：

```javascript
function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
  ));
}
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
| Cmd+H 替换 | ❌ | ❌（Review 反馈 #5：不劫持 macOS OS 语义） |
| Cmd+S 保存 | ❌ | ✅ 编辑器独有（拦截浏览器"保存网页"） |

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

### 4.5 预览切换（客户端渲染）

**Review 反馈 #1（已采纳）**：preview 完全在客户端完成，不调服务端端点。

```
[预览 ◐] 按钮
  → mode='preview':
     → 用当前 sidebar 缓存的 segments（或实时调 /api/parse 拿新值）
     → 客户端 template literal: html = segments.map(s => `<hr><h2>${escapeHtml(s.speaker)}</h2><p>${escapeHtml(s.text)}</p>`).join('')
     → 隐藏 textarea + sidebar，注入 <div class="preview">{html}</div> 到左侧
     → **用 escapeHtml 实现 (转义 &<>"') 防 XSS**（用户脚本可能含 `<script>`）
  → mode='edit': 恢复 textarea + sidebar, preview div 隐藏
```

### 4.6 下载 md

```
[下载md ▾] 下拉
  → "原文 .md":
     → if dirty=true → 前端用 Blob 从 editor.value 生成 .md 直接下载（不去服务端，避免下到旧文件）
     → else → fetch GET /api/scripts/export?format=raw&name=<loadedName> 触发浏览器下载
  → "渲染 .md" → 同上但 format=rendered

**loadedName 来源**：
  - Init 阶段：URL ?load=<name> → loadedName = <name>
  - Save 成功后：loadedName = newSavedName（服务端返回的）
  - 从未保存过：loadedName = null，下载走"原文 .md"分支（dirty 必然 true）
```

**Review 反馈 MAJOR-5 + MINOR-3**：原 spec 没区分 dirty，导致用户编辑 20 分钟不存点下载会下到旧文件。**已修复**。

### 4.7 Cmd+F 查找（不劫持 Cmd+H）

**Review 反馈 #5（已采纳）**：原计划劫持 Cmd+H 实现自定义替换。**删除**。理由：

- macOS Cmd+H 是 OS 级 hide window 语义，用户肌肉记忆强，劫持会持续困惑
- 浏览器原生 Cmd+F 在 textarea 内可用，跳到下一个匹配足够日常使用
- 真要全局替换，浏览器 Cmd+F 找不到时给个 toast 提示 "暂不支持全局替换"

**保留**：Cmd+S → 触发保存（拦截浏览器"保存网页"默认行为）。

```javascript
document.addEventListener('keydown', (e) => {
  if (!(e.metaKey || e.ctrlKey)) return;
  if (e.key === 's' || e.key === 'S') {
    e.preventDefault();
    saveScript();  // 调 /api/scripts/save
  }
  // 不拦截 Cmd+H / Ctrl+H
});
```

### 4.8 全屏 + 字号

```
[全屏] 按钮
  → toggle <html class="fullscreen">
  → CSS: .fullscreen .editor-grid { position: fixed; inset: 0; z-index: 9999; background: white }

[字号 − A+]
  → fontSize -= 2 / += 2, **clamp 14..26**（Review 反馈 #4：用户 60+，12pt 太小）
  → <html style="--editor-font-size: {fontSize}pt">
  → CSS: textarea { font-size: var(--editor-font-size); line-height: 1.6 }
  → 写 localStorage.editor.fontSize（避免每次开页重置）
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
| 段数 > 100 | sidebar 始终渲染所有段（无虚拟滚动/懒加载）；点击段时编辑器光标跳到该段开头 —— 100 段无性能问题故不懒加载 |
| 文本 > 50KB | parse debounce 改 1000ms（避免大脚本卡） |
| ~~Safari Cmd+H~~ | **Review 反馈 #5 已删除**，不拦截 |
| 预览时按保存 | 不允许 —— 切回 edit 模式才能保存 |
| **主页 AI 生成（script_done via WS）** | **Review 反馈 #6 + MAJOR-6**：/editor 订阅 WebSocket，收到 `script_done` → toast "主页 AI 生成了新脚本「<topic>」，点击加载？"。**点击时若 dirty=true 先 confirm()** "当前编辑未保存，确定加载新脚本？" 再触发 `?load=` 跳转（避免静默覆盖） |
| **WS 断线** | **Review 反馈 MAJOR-3**：onclose 重连，线性 backoff 1s/2s/4s（最多 3 次），失败后 toast "新脚本通道断开，需手动检查主页" |
| **Safari 隐私模式 localStorage 抛错** | **Review 反馈 MAJOR-4**：localStorage 读 / 写都 try/catch，失败时静默 fallback 到 in-memory（字号偏好本会话内有效，刷新后回到默认 18px） |
| 浏览器窄屏（< 900px） | sidebar 自动改为抽屉式，顶部按钮切换显隐（Review 反馈 #7） |
| 字号 localStorage 持久化 | 关页重开沿用上次值（Review 反馈 #4） |
| Cmd+S | 拦截浏览器"保存网页" → 调保存按钮（Review 反馈 新增） |
| `<textarea>` 无 id/name | **Review 反馈 新增**：防止 Chrome/Safari 自动填充回旧版覆盖当前编辑 |

---

## 风险 & 缓解

| 风险 | 缓解 |
|------|------|
| XSS via 用户脚本含 `<script>` | 客户端预览渲染用 `textContent`/escape 函数（**Review 反馈 #1**：已改为客户端，无服务端 XSS 面） |
| 路径穿越（`?load=../../etc/passwd`） | `load_script_impl` 已有的文件名验证（必须 `script_*.json`、禁 `/`、`..`） |
| 浏览器自动填充覆盖当前编辑 | `<textarea>` 不给 `id` / `name` 属性（Chrome/Safari 不识别 → 不入自动填充库） |
| 主页 AI 生成与 /editor tab 失同步 | /editor 订阅 WebSocket `script_done` → toast 询问加载（Review 6） |
| 浏览器窄屏 sidebar 抢占编辑区 | `@media (max-width:900px)` 切单列 + 抽屉式 sidebar（Review 7） |
| 60+ 用户字号过小 | clamp 14..26 + localStorage 持久化（Review 4） |
| Cmd+H 被劫持导致 macOS hide window 失效 | **删除 Cmd+H 劫持**（Review 5），Cmd+F 浏览器原生足够 |
| 浏览器"保存网页"（Cmd+S）误触发 | 拦截 Cmd+S 调我们的保存逻辑（Review 新增） |
| beforeunload 在 SPA 内失效 | 用 `<a href="/editor">` 而非 SPA 路由，浏览器原生 beforeunload 即可触发 |
| 长时间挂起导致 debounce 不触发 | onbeforeunload 时若有 pending debounce，flush 一次同步 parse |
| textarea 自动换行不一致 | `white-space: pre-wrap` 强制保留换行，`word-wrap: break-word` 长破折号分行 |
| 崩溃恢复（用户改 30 分钟没存浏览器死） | **YAGNI**：不实现。用户明确要"不自动存"，超出本 spec 范围 |

---

## 测试要点

| 场景 | 验收 |
|------|------|
| 主页点跳转按钮 | 打开新 tab 显示 `/editor` |
| 编辑器 `?load=<name>` | URL 带 name → 自动灌入内容 + snapshot 同步 |
| 编辑 + 关闭 tab | 弹原生确认（beforeunload） |
| 点保存 | toast 成功，dirty 变 false，snapshot 更新 |
| 点放弃 | 编辑器内容回退到入页快照 |
| 切预览 | 左侧 textarea 换为客户端渲染 HTML（无网络请求） |
| 下载 .md | 浏览器下载对应文件，原文/渲染两种都试 |
| 改字号 + 全屏 | CSS variable 立即更新，刷新页面后字号沿用 |
| Cmd+F | 浏览器原生 find 正常 |
| Cmd+S | 不保存网页，调我们的保存逻辑 |
| Cmd+H | 不拦截，正常 hide window |
| 主页 AI 生成时 /editor tab | 收到 toast "新脚本已生成，加载？"，点 → 自动 load |
| 浏览器窄屏（拖到 <900px） | sidebar 自动收起为抽屉 |
| parse 失败时编辑 | 顶栏 ⓘ 显示 "❌", sidebar 留旧值，但编辑仍允许 |
| XSS 注入测试 | 脚本含 `<script>alert(1)</script>` 不执行，预览里看到原文转义 |

---

## 不在本 spec

- 主页面编辑器的字号/全屏（**只在 `/editor` 提供**，主页保持 rows=8）
- 多人实时协作
- 脚本模板 / 片段库（snippet）
- 自动补全（基于历史脚本）
- 编辑历史 / redo-undo 多级（浏览器 textarea 内置 undo 够用）