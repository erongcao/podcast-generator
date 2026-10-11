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

    // ---- Save ----
    $('#save-btn')?.addEventListener('click', saveScript);

    // ---- Discard ----
    $('#discard-btn')?.addEventListener('click', () => {
        if (!state.dirty) {
            toast('没有改动', 'info', 1500);
            return;
        }
        if (!confirm('放弃当前修改？')) return;
        const editor = $('#script-editor');
        editor.value = state.snapshot;
        editor.dispatchEvent(new Event('input'));   // F5
        state.dirty = false;
        updateTitle();
        toast('已放弃', 'info');
    });

    // ---- Cmd+S 拦截 ----
    document.addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && (e.key === 's' || e.key === 'S')) {
            e.preventDefault();
            saveScript();
        }
    });

    // ---- beforeunload ----
    window.addEventListener('beforeunload', (e) => {
        if (state.dirty) {
            e.preventDefault();
            e.returnValue = '';
            return '';
        }
    });

    // ---- 下载 md ----
    const dlBtn = $('#download-btn');
    const dlMenu = $('#download-menu');
    if (dlBtn && dlMenu) {
        dlBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            dlMenu.hidden = !dlMenu.hidden;
        });
        document.addEventListener('click', () => { dlMenu.hidden = true; });
        dlMenu.querySelectorAll('a').forEach(a => {
            a.addEventListener('click', async (e) => {
                e.preventDefault();
                dlMenu.hidden = true;
                const format = a.dataset.format;
                await downloadMd(format);
            });
        });
    }

    // ---- Preview toggle ----
    $('#preview-btn')?.addEventListener('click', togglePreview);

    // ---- 字号 + 全屏 ----
    $('#font-decrease-btn')?.addEventListener('click', () => changeFontSize(-2));
    $('#font-increase-btn')?.addEventListener('click', () => changeFontSize(+2));
    $('#fullscreen-btn')?.addEventListener('click', toggleFullscreen);

    // ---- 窄屏抽屉（顶栏按钮：动态注入，因为窄屏才需要） ----
    injectDrawerToggle();
}

function changeFontSize(delta) {
    const next = Math.max(14, Math.min(26, state.fontSize + delta));
    if (next === state.fontSize) return;   // F4: 到边界就不弹 toast
    state.fontSize = next;
    applyFontSize();
    if (!lsSet('editor.fontSize', String(next))) {
        // F4 (Review MAJOR-4): Safari 隐私模式 → 静默 fallback，toast 不刷屏
        toast('字号偏好无法持久化（浏览器隐私模式？）', 'warning', 2000);
    }
}

function toggleFullscreen() {
    document.documentElement.classList.toggle('fullscreen');
    const btn = $('#fullscreen-btn');
    if (btn) btn.textContent = document.documentElement.classList.contains('fullscreen') ? '⛶ 退出全屏' : '⛶ 全屏';
}

function injectDrawerToggle() {
    const topbarRight = $('.topbar-right');
    if (!topbarRight) return;   // 防御性：万一 HTML 改了没有 target

    // 顶栏右区插入一个"☰ 段列表"按钮，仅在 ≤900px 时显示
    const btn = document.createElement('button');
    btn.id = 'sidebar-toggle-btn';
    btn.className = 'btn btn-small';
    btn.textContent = '☰ 段列表';
    btn.setAttribute('aria-label', '切换段列表');
    btn.hidden = window.innerWidth > 900;   // 默认隐藏
    btn.addEventListener('click', () => {
        document.getElementById('editor-sidebar').classList.toggle('open');
    });
    topbarRight.appendChild(btn);

    // 窗口 resize 时切换显隐（只改 hidden，不重建按钮）
    window.addEventListener('resize', () => {
        btn.hidden = window.innerWidth > 900;
        // 宽屏时确保 sidebar 不带 open class（避免桌面布局卡在抽屉状态）
        if (window.innerWidth > 900) {
            document.getElementById('editor-sidebar').classList.remove('open');
        }
    });
}

function togglePreview() {
    const editor = $('#script-editor');
    const previewPanel = $('#preview-panel');
    const btn = $('#preview-btn');

    if (state.mode === 'edit') {
        // 切到 preview
        if (lastSegments.length === 0) {
            toast('当前文本 parse 失败或为空，无可预览内容', 'warning');
            return;
        }
        const html = lastSegments.map(s =>
            `<hr><h2>${escapeHtml(s.speaker)}</h2><p>${escapeHtml(s.text)}</p>`
        ).join('');
        previewPanel.innerHTML = html;
        editor.hidden = true;
        previewPanel.hidden = false;
        btn.textContent = '编辑 ◑';
        state.mode = 'preview';
    } else {
        // 切回 edit
        editor.hidden = false;
        previewPanel.hidden = true;
        btn.textContent = '预览 ◐';
        state.mode = 'edit';
    }
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

async function saveScript() {
    const btn = $('#save-btn');
    if (!state.dirty) {
        toast('没有改动', 'info', 1500);
        return;
    }

    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '保存中...';

    try {
        const editor = $('#script-editor');
        const text = editor.value;
        // parse 失败也要能存（用户主动存原文）
        let topic = state.topic;
        if (lastSegments.length === 0 && text.trim()) {
            if (!confirm('⚠ 文本 parse 不通过，保存的是原文，确认？')) {
                btn.disabled = false;
                btn.textContent = originalText;
                return;
            }
            topic = 'parse失败原文';   // 提示后续看文件名就知道
        }

        const res = await fetch('/api/scripts/save', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({script_text: text, topic: topic}),
        });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();

        state.snapshot = text;
        state.dirty = false;
        state.loadedName = data.name;   // MAJOR-5: save 成功后更新
        updateTitle();

        toast(`✅ 已保存 (${data.name})`, 'success');
    } catch (e) {
        toast(`保存失败: ${e.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

// ============================================================
// 下载导出（Task 9）
// ============================================================

async function downloadMd(format) {
    const editor = $('#script-editor');
    const text = editor.value;
    const topic = state.topic || '未命名脚本';

    // MINOR-3 / MAJOR-5: dirty 时用 Blob，干净时才走 export endpoint
    if (state.dirty) {
        // Blob 路径：直接用 editor.value
        let blob;
        let filename;
        if (format === 'raw') {
            blob = new Blob([text], {type: 'text/markdown;charset=utf-8'});
            filename = `${sanitizeFilenameForDownload(topic)}.md`;
        } else {
            // rendered：用 lastSegments
            const md = lastSegments.map(s =>
                `---\n\n## ${escapeHtml(s.speaker)}\n\n${escapeHtml(s.text)}\n`
            ).join('\n').trim() + '\n';
            blob = new Blob([md], {type: 'text/markdown;charset=utf-8'});
            filename = `${sanitizeFilenameForDownload(topic)}.md`;
        }
        triggerBlobDownload(blob, filename);
        toast('已下载（dirty 状态，源是当前编辑器）', 'info');
        return;
    }

    // 非 dirty：走服务端 export endpoint（确保拿到的是磁盘上的最新版）
    if (!state.loadedName) {
        toast('没有已保存的脚本可导出，请先保存', 'warning');
        return;
    }
    try {
        const url = `/api/scripts/export?name=${encodeURIComponent(state.loadedName)}&format=${format}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        // 从 Content-Disposition 拿服务端给的 filename
        const cd = res.headers.get('Content-Disposition') || '';
        const m = cd.match(/filename="([^"]+)"/);
        const filename = m ? m[1] : `${sanitizeFilenameForDownload(topic)}.md`;
        triggerBlobDownload(blob, filename);
        toast('已下载 ✅', 'success');
    } catch (e) {
        toast(`下载失败: ${e.message}`, 'error');
    }
}

function sanitizeFilenameForDownload(name) {
    // F4: Blob 路径要自己起名字
    return name.replace(/[<>:"/\\|?*]/g, '_').trim() || '未命名脚本';
}

function triggerBlobDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;   // F4
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ============================================================
// 启动
// ============================================================

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}