// editor.js — 全屏脚本编辑器逻辑（/editor 页）
//
// 模块结构（按子任务逐步搭建）：
//   1. State (中心化)
//   2. Util (escapeHtml, toast, $)
//   3. Init (URL ?load=, localStorage 字号)
//   4. WebSocket 订阅        — Task 6
//   5. Parse debounce + sidebar 渲染 — Task 7
//   6. Save / Discard / Export — Tasks 8-9
//   7. Preview toggle         — Task 10
//   8. Font size / Fullscreen / 抽屉 — Task 11
//
// Task 5 (本文件) 只交付 1+2+3。bindEvents / connectWebSocket 是占位。
//
// 设计原则：
// - 无 SPA 框架，原生 JS
// - State 是单一事实源，所有 DOM 更新都从 state 派生
// - 网络请求失败不破坏本地状态，toast 提示即可

'use strict';

// ============================================================
// State（单一事实源）
// ============================================================

const state = {
    dirty: false,                 // 当前编辑器内容与 snapshot 不一致
    snapshot: '',                 // 入页时的内容（discard 回滚目标）
    topic: '未命名脚本',          // 顶部标题
    loadedName: null,             // 当前载入的脚本文件名（URL ?load= 或 save 后）
    fontSize: 18,                 // px
    mode: 'edit',                 // 'edit' | 'preview'
    ws: null,                     // WebSocket 实例（Task 6 填充）
    wsAttempts: 0,
    wsMaxAttempts: 3,
    wsGivingUp: false,
    pendingScriptLoad: null,      // WS script_done 缓存，等待用户点击 toast
};

// ============================================================
// Util
// ============================================================

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, c => (
        {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
    ));
}

function toast(message, level = 'info', durationMs = 3000) {
    const container = $('#toast-container');
    if (!container) return null;
    const div = document.createElement('div');
    div.className = `toast toast-${level}`;
    div.textContent = message;
    container.appendChild(div);
    if (durationMs > 0) {
        setTimeout(() => div.remove(), durationMs);
    }
    return div;
}

// localStorage 读写（try/catch 包裹，Safari 隐私模式抛 QuotaExceededError）
function lsGet(key) {
    try { return localStorage.getItem(key); } catch { return null; }
}
function lsSet(key, value) {
    try { localStorage.setItem(key, value); return true; }
    catch { return false; }
}

// ============================================================
// Init
// ============================================================

async function init() {
    // 1. 字号恢复（lsGet 内部已 try/catch）
    const savedFont = parseInt(lsGet('editor.fontSize') || '18', 10);
    if (Number.isFinite(savedFont) && savedFont >= 14 && savedFont <= 26) {
        state.fontSize = savedFont;
    }
    applyFontSize();

    // 2. URL ?load=<name> → fetch /api/scripts/get 灌入
    const params = new URLSearchParams(location.search);
    const loadName = params.get('load');
    if (loadName) {
        await loadScriptFromUrl(loadName);
    }

    // 3. 渲染历史下拉
    await populateRecentScripts();

    // 4. 绑定事件（占位 — Task 6/7/8 替换）
    bindEvents();

    // 5. WebSocket 订阅（占位 — Task 6 替换）
    connectWebSocket();
}

async function loadScriptFromUrl(name) {
    try {
        const res = await fetch(`/api/scripts/get?name=${encodeURIComponent(name)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const editor = $('#script-editor');
        editor.value = data.editor_text || '';
        // F5：.value= 不触发 'input'，手动 dispatch 让 dirty/parse 逻辑跟得上
        editor.dispatchEvent(new Event('input'));
        state.snapshot = editor.value;
        state.dirty = false;
        state.loadedName = name;
        state.topic = data.topic || name.replace(/\.json$/, '');
        updateTitle();
        toast(`已加载 ${state.topic}`, 'success');
    } catch (e) {
        toast(`加载失败: ${e.message}`, 'error');
    }
}

async function populateRecentScripts() {
    const sel = $('#recent-scripts-select');
    if (!sel) return;
    try {
        const res = await fetch('/api/scripts/recent?limit=20');
        const data = await res.json();
        sel.innerHTML = '<option value="">载入历史脚本…</option>';
        for (const s of (data.scripts || [])) {
            const opt = document.createElement('option');
            opt.value = s.name;
            const date = s.created_at ? s.created_at.replace('T', ' ') : '';
            const preview = s.preview ? ` — ${s.preview.slice(0, 30)}` : '';
            opt.textContent = `[${date}] ${s.topic || '(无主题)'} (${s.n_segments}段)${preview}`;
            sel.appendChild(opt);
        }
    } catch (e) {
        sel.innerHTML = `<option>加载失败: ${e.message}</option>`;
    }
}

function updateTitle() {
    const titleEl = $('#editor-title');
    if (titleEl) titleEl.textContent = state.topic;
    const dirtyEl = $('#dirty-indicator');
    if (dirtyEl) dirtyEl.hidden = !state.dirty;
}

function applyFontSize() {
    document.documentElement.style.setProperty('--editor-font-size', `${state.fontSize}px`);
}

// 占位 — 后续 task 替换
function bindEvents() { /* Task 6/7/8 填充 */ }
function connectWebSocket() { /* Task 6 填充 */ }

// ============================================================
// 启动
// ============================================================

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}