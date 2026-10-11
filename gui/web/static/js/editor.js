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

function bindEvents() {
    // textarea input → dirty + parse debounce
    const editor = $('#script-editor');
    let parseTimer = null;

    editor.addEventListener('input', () => {
        if (!state.dirty) {
            state.dirty = true;
            updateTitle();
        }
        if (parseTimer) clearTimeout(parseTimer);
        // 大脚本（>50KB）改 1000ms 防抖
        const delay = editor.value.length > 50000 ? 1000 : 500;
        parseTimer = setTimeout(refreshParse, delay);
    });

    // 历史下拉 change → load
    const recentSel = $('#recent-scripts-select');
    if (recentSel) {
        recentSel.addEventListener('change', async () => {
            const name = recentSel.value;
            if (!name) return;
            if (state.dirty && !confirm('当前编辑未保存，确定载入其他脚本？')) {
                recentSel.value = '';
                return;
            }
            await loadScriptFromUrl(name);
            recentSel.value = '';
        });
    }

    // dirty 监听 textarea 值变化的兜底（discard / loadScriptFromUrl 也走 input）
    // F5 已在 loadScriptFromUrl 用 dispatchEvent(new Event('input')) 处理
}

let lastSegments = [];   // 缓存给 preview 用

async function refreshParse() {
    const editor = $('#script-editor');
    const text = editor.value;
    const status = $('#parse-status');
    const segList = $('#segment-list');

    if (!text.trim()) {
        status.textContent = 'ⓘ 0 段 · 0 字';
        status.classList.remove('error');
        segList.innerHTML = '<li class="sidebar-empty">暂无段落</li>';
        lastSegments = [];
        return;
    }

    try {
        const res = await fetch('/api/parse', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text}),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        lastSegments = data.segments || [];

        // ⓘ 顶部统计
        const speakersStr = Object.entries(data.speakers || {})
            .map(([sp, n]) => `${sp}×${n}`).join(' · ');
        status.textContent = `ⓘ ${data.count} 段 · ${text.length} 字${speakersStr ? ' · ' + speakersStr : ''}`;
        status.classList.remove('error');

        // sidebar 段列表
        segList.innerHTML = '';
        data.segments.forEach((seg, i) => {
            const li = document.createElement('li');
            li.innerHTML = `<span class="seg-idx">${String(i + 1).padStart(2, '0')}</span><span class="seg-speaker">${escapeHtml(seg.speaker)}</span><span class="seg-chars">${seg.text.length}字</span>`;
            li.addEventListener('click', () => jumpToSegment(i, data.segments));
            segList.appendChild(li);
        });
    } catch (e) {
        status.textContent = `❌ parse 失败: ${e.message}`;
        status.classList.add('error');
        // sidebar 留旧值，不清空
    }
}

function jumpToSegment(index, segments) {
    // 简单实现：在 textarea 里找前 index+1 个 speaker marker，把光标放那儿
    const editor = $('#script-editor');
    const text = editor.value;
    const target = segments[index];
    if (!target) return;

    // 找前 N 个 "【target.speaker】" 的位置
    let pos = -1;
    const marker = `【${target.speaker}】`;
    for (let i = 0; i <= index; i++) {
        pos = text.indexOf(marker, pos + 1);
        if (pos === -1) return;   // 找不到
    }

    editor.focus();
    editor.setSelectionRange(pos, pos + marker.length);
    // 滚动到该位置（粗略：用 textarea 的 scrollTop 比例）
    const ratio = pos / text.length;
    editor.scrollTop = editor.scrollHeight * ratio;
}

function connectWebSocket() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws`;

    if (state.wsGivingUp) return;   // 已放弃，不再尝试

    try {
        state.ws = new WebSocket(url);
    } catch (e) {
        scheduleWsReconnect();
        return;
    }

    state.ws.onopen = () => {
        state.wsAttempts = 0;   // F2: 成功重连后清零
    };

    state.ws.onmessage = (event) => {
        let msg;
        try { msg = JSON.parse(event.data); } catch { return; }
        if (msg.type === 'script_done') {
            handleScriptDone(msg);
        }
    };

    state.ws.onclose = () => {
        scheduleWsReconnect();
    };

    state.ws.onerror = () => {
        // onclose 也会触发，这里不重复提示
    };
}

function scheduleWsReconnect() {
    if (state.wsAttempts >= state.wsMaxAttempts) {
        if (!state.wsGivingUp) {
            state.wsGivingUp = true;
            toast('新脚本通道断开，需手动检查主页', 'warning', 0);
        }
        return;
    }
    const delays = [1000, 2000, 4000];
    const delay = delays[state.wsAttempts] || 4000;
    state.wsAttempts++;
    setTimeout(connectWebSocket, delay);
}

function handleScriptDone(msg) {
    // 缓存 + 弹 toast，让用户决定是否加载
    state.pendingScriptLoad = msg;
    // script_gen.py 的 script_done payload 没有 topic 字段，
    // 但有 saved_to（如 script_20261011_145102_脾硬化.json）→ 从文件名解析
    const topic = extractTopicFromSavedTo(msg.saved_to) || 'AI 生成';
    const t = toast(`主页 AI 生成了新脚本「${topic}」，点击加载？`, 'info', 0);
    t.addEventListener('click', () => loadPendingScript(t));
}

function extractTopicFromSavedTo(savedTo) {
    if (!savedTo) return null;
    // saved_to = ".../script_20261011_145102_脾硬化.json"
    const m = savedTo.match(/script_\d{8}_\d{6}_(.+?)\.json$/);
    return m ? m[1].replace(/_/g, ' ') : null;
}

async function loadPendingScript(toastEl) {
    const msg = state.pendingScriptLoad;
    if (!msg) {
        if (toastEl) toastEl.remove();   // 取消路径也清掉死按钮
        return;
    }

    // F1: dirty 检查，避免静默覆盖 + beforeunload 二次弹窗
    if (state.dirty) {
        if (!confirm('当前编辑未保存，确定加载新脚本？')) {
            // 取消 → 保留 pendingScriptLoad 让用户稍后还能再点；
            // 但这个 toast 已经没意义了（用户刚拒），直接关
            if (toastEl) toastEl.remove();
            return;
        }
        state.dirty = false;   // 先清 dirty，避免 beforeunload 再弹
    }

    // 接受 → 提交所有状态更新
    state.pendingScriptLoad = null;
    if (toastEl) toastEl.remove();

    // 把 AI 生成的脚本灌入编辑器
    const editor = $('#script-editor');
    editor.value = msg.script_text || '';
    editor.dispatchEvent(new Event('input'));
    state.snapshot = editor.value;
    state.topic = extractTopicFromSavedTo(msg.saved_to) || 'AI 生成';
    state.loadedName = null;   // AI 生成不走文件名路径
    updateTitle();
    toast('已加载 AI 生成脚本（记得手动保存）', 'success');
}

// ============================================================
// 启动
// ============================================================

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}