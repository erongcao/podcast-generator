// Podcast Generator — Web 看板前端
// Phase 1: 启动检查 + 静态布局 + 音色行动态 add/remove
// Phase 2 起接 parse / synthesize API

(() => {
  'use strict';

  // ========== 工具 ==========
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const log = (msg, level = 'info') => {
    const box = $('#log-box');
    if (!box) return;
    const p = document.createElement('p');
    p.className = `log-line log-${level}`;
    const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    p.textContent = `[${ts}] ${msg}`;
    box.appendChild(p);
    // Phase 7: cap log to last 200 lines to prevent unbounded growth
    while (box.children.length > 200) box.removeChild(box.firstChild);
    box.scrollTop = box.scrollHeight;
  };

  const setStatus = (text, kind = 'ok') => {
    const el = $('#status-text');
    el.textContent = text;
    el.className = `status-${kind}`;
  };

  // ========== 启动检查 ==========
  async function bootCheck() {
    try {
      const res = await fetch('/api/version');
      const data = await res.json();
      $('#version-tag').textContent = `v${data.version} · Phase ${data.phase}`;
      log(`✅ 后端连接成功 (${data.version}, Phase ${data.phase})`, 'success');
      setStatus('✅ 准备就绪');
    } catch (e) {
      log(`❌ 后端连接失败: ${e.message}`, 'error');
      setStatus('❌ 后端无响应', 'err');
    }
    // 拉凭证状态：显示 "已从 config.json 加载 sk-api-..."
    try {
      const r = await fetch('/api/credentials');
      const c = await r.json();
      window._creds = c;  // Phase 6: AI 表单用
      const mmx = c.minimax;
      const ve = c.volcengine;
      const status = $('#cred-status');
      if (mmx.present) {
        status.innerHTML = `<span style="color:var(--color-forest);">✅ MiniMax key 已从 ${mmx.source} 加载 (${mmx.prefix}) — 留空即可</span>`;
        $('#cred-api-key').placeholder = `已加载 ${mmx.prefix} — 留空用默认`;
      } else {
        status.innerHTML = `<span style="color:var(--color-clay);">⚠ MiniMax key 未配置 — 在此填，或写到 config.json</span>`;
      }
      const veLine = (ve.appid_present && ve.token_present)
        ? '<span style="color:var(--color-forest);">✅ 豆包凭证已加载</span>'
        : (ve.appid_present || ve.token_present)
          ? '<span style="color:var(--color-clay);">⚠ 豆包凭证不完整</span>'
          : '<span style="color:var(--color-text-light);">豆包凭证未配置</span>';
      status.innerHTML += '<br>' + veLine;
    } catch (e) {
      // 静默
    }
  }

  // ========== 音色行（动态 add/remove） ==========
  // Phase 5: voice 选项从 /api/voices 真实拉
  const ROLE_LABELS = ['主播甲', '主播乙', '主播丙', '主播丁', '主播戊',
                       '主播己', '主播庚', '主播辛', '主播壬', '主播癸'];

  const voiceRows = [];  // 内存中的 rows
  let VOICE_OPTIONS = [{ id: '', name: '（未选择）' }];  // 会被 fetchVoices 填充

  async function fetchVoices() {
    try {
      const res = await fetch('/api/voices');
      const data = await res.json();
      VOICE_OPTIONS = [{ id: '', name: '（未选择）' }];
      for (const v of data.voices) {
        const tagHint = v.tags.length ? ` [${v.tags.join(',')}]` : '';
        const wavHint = v.has_wav ? '🔊' : (v.has_preview ? '▶' : '∅');
        VOICE_OPTIONS.push({
          id: v.voice_id,
          name: `${wavHint} ${v.voice_id} — ${v.display_name || ''}${tagHint}`,
        });
      }
      log(`✅ 音色库: ${data.total} 个 voice 加载完成`, 'success');
      // 刷新所有下拉
      refreshAllVoiceSelects();
      // 刷新音色库列表
      renderVoiceLibrary(data.voices);
    } catch (e) {
      log(`❌ 拉音色库失败: ${e.message}`, 'error');
    }
  }

  function renderVoiceLibrary(voices) {
    $('#voice-count').textContent = voices.length;
    if (voices.length === 0) {
      $('#voice-library-list').innerHTML =
        '<div class="placeholder"><div>音色库为空</div></div>';
      return;
    }
    const items = voices.map(v => {
      const tagStr = v.tags.length ? `<span class="vtags">[${v.tags.join(', ')}]</span>` : '';
      const audio = v.has_wav || v.has_preview
        ? `<button class="vbtn" onclick="window.previewVoice('${v.voice_id}')">▶ 试听</button>`
        : '<span style="color:var(--color-text-light); font-size:11px;">无</span>';
      const del = `<button class="vbtn danger" onclick="window.deleteVoice('${v.voice_id}')">🗑</button>`;
      return `
        <div class="voice-lib-item">
          <span class="vname">${escapeHtml(v.display_name || v.voice_id)}</span>
          ${tagStr}
          ${audio} ${del}
        </div>
      `;
    }).join('');
    $('#voice-library-list').innerHTML = items;
  }

  window.previewVoice = function(vid) {
    const audio = new Audio(`/api/voices/${encodeURIComponent(vid)}/audio`);
    audio.play().catch(e => log(`试听失败: ${e.message}`, 'error'));
  };

  window.deleteVoice = async function(vid) {
    if (!confirm(`确定删除 voice "${vid}" 吗？此操作不可撤销。`)) return;
    try {
      const res = await fetch(`/api/voices/${encodeURIComponent(vid)}`, { method: 'DELETE' });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      log(`✅ 已删除 voice: ${vid}`, 'success');
      fetchVoices();  // 刷新
    } catch (e) {
      log(`❌ 删除失败: ${e.message}`, 'error');
    }
  };

  function makeVoiceRow(index) {
    const row = document.createElement('div');
    row.className = 'voice-row';
    row.dataset.index = index;
    const isFixed = index < 2;
    row.innerHTML = `
      <span class="voice-label">${ROLE_LABELS[index] || `主播${index+1}`}</span>
      <select class="form-select voice-select voice-selector" data-index="${index}">
        ${VOICE_OPTIONS.map(v =>
          `<option value="${escapeHtml(v.id)}">${escapeHtml(v.name)}</option>`
        ).join('')}
      </select>
      <div class="voice-speed" title="速度倍率 (0.5-1.5)">
        <input type="number" class="voice-speed-input" data-index="${index}"
               min="0.5" max="1.5" step="0.05" value="1.0"
               style="font-size:11px; padding:2px 4px;">
      </div>
      <button class="voice-remove remove-voice-btn"
              data-index="${index}"
              ${isFixed ? 'disabled style="opacity:0.3;cursor:not-allowed;"' : ''}>×</button>
    `;
    return row;
  }

  function refreshAllVoiceSelects() {
    voiceRows.forEach((row) => {
      const sel = row.querySelector('.voice-select');
      const cur = sel.value;
      sel.innerHTML = VOICE_OPTIONS.map(v =>
        `<option value="${escapeHtml(v.id)}">${escapeHtml(v.name)}</option>`
      ).join('');
      if (VOICE_OPTIONS.some(v => v.id === cur)) sel.value = cur;
    });
  }

  function addVoiceRow() {
    if (voiceRows.length >= ROLE_LABELS.length) {
      log('已达到最大音色数（10）', 'warning');
      return;
    }
    const idx = voiceRows.length;
    const row = makeVoiceRow(idx);
    $('#voice-rows').appendChild(row);
    voiceRows.push(row);
  }

  function removeVoiceRow(index) {
    if (index < 2) return;
    const row = voiceRows[index];
    if (!row) return;
    row.remove();
    voiceRows.splice(index, 1);
    voiceRows.forEach((r, i) => {
      r.dataset.index = i;
      r.querySelector('.form-label').textContent = `🎭 ${ROLE_LABELS[i] || `主播${i+1}`}`;
      const sel = r.querySelector('.voice-select');
      sel.dataset.index = i;
      const btn = r.querySelector('.remove-voice-btn');
      btn.dataset.index = i;
      btn.disabled = i < 2;
    });
  }

  $('#add-voice-btn').addEventListener('click', addVoiceRow);
  $('#voice-rows').addEventListener('click', (e) => {
    if (e.target.classList.contains('remove-voice-btn')) {
      const idx = parseInt(e.target.dataset.index, 10);
      removeVoiceRow(idx);
    }
  });
  $('#reload-voices-btn').addEventListener('click', () => fetchVoices());

  // ========== 上传 / 录制 voice ==========
  $('#upload-voice-btn').addEventListener('click', () => {
    const vid = prompt('voice_id（字母数字下划线连字符，1-64 字符）：');
    if (!vid) return;
    if (!VOICE_ID_PATTERN.test(vid)) {
      log('voice_id 格式非法', 'error');
      return;
    }
    const dname = prompt('显示名称（可空）：', '') || vid;
    // 选完 id/name 后触发文件选择
    pendingUpload = { voice_id: vid, display_name: dname };
    $('#voice-file-input').click();
  });

  const VOICE_ID_PATTERN = /^[a-zA-Z0-9_-]{1,64}$/;
  let pendingUpload = null;

  $('#voice-file-input').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file || !pendingUpload) return;
    const form = new FormData();
    form.append('voice_id', pendingUpload.voice_id);
    form.append('display_name', pendingUpload.display_name);
    form.append('file', file);
    log(`📤 上传 voice: ${pendingUpload.voice_id} (${(file.size/1024).toFixed(1)} KB)`, 'info');
    try {
      const res = await fetch('/api/voices', { method: 'POST', body: form });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      log(`✅ 上传成功: ${data.voice_id} (${data.display_name})`, 'success');
      fetchVoices();
    } catch (e) {
      log(`❌ 上传失败: ${e.message}`, 'error');
    } finally {
      pendingUpload = null;
      e.target.value = '';  // 重置
    }
  });

  // 浏览器录音
  let mediaRecorder = null;
  let recordedBlob = null;
  $('#record-voice-btn').addEventListener('click', () => {
    $('#record-panel').style.display = $('#record-panel').style.display === 'none' ? 'block' : 'none';
  });
  $('#record-toggle-btn').addEventListener('click', async () => {
    if (!mediaRecorder) {
      // 开始
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream);
        const chunks = [];
        mediaRecorder.ondataavailable = (e) => chunks.push(e.data);
        mediaRecorder.onstop = () => {
          recordedBlob = new Blob(chunks, { type: 'audio/webm' });
          $('#record-status').textContent = `已录 ${(recordedBlob.size/1024).toFixed(1)} KB`;
          // 显示试听 + 上传
          const url = URL.createObjectURL(recordedBlob);
          $('#record-result').innerHTML = `
            <audio controls src="${url}" style="width:100%; margin-bottom:6px;"></audio>
            <div style="display:flex; gap:4px;">
              <button class="btn btn-primary" id="record-upload-btn" style="font-size:12px; padding:4px 10px;">📤 上传为新音色</button>
              <button class="btn btn-ghost" id="record-redo-btn" style="font-size:12px; padding:4px 10px;">🔄 重录</button>
            </div>
          `;
          $('#record-upload-btn').addEventListener('click', uploadRecording);
          $('#record-redo-btn').addEventListener('click', () => {
            recordedBlob = null;
            $('#record-result').innerHTML = '';
            $('#record-status').textContent = '未录音';
            mediaRecorder = null;
            $('#record-toggle-btn').textContent = '🎤 开始';
          });
          // 释放麦克风
          stream.getTracks().forEach(t => t.stop());
        };
        mediaRecorder.start();
        $('#record-status').textContent = '🔴 录音中…';
        $('#record-toggle-btn').textContent = '⏹ 停止';
      } catch (e) {
        log(`❌ 无法访问麦克风: ${e.message}`, 'error');
        return;
      }
    } else {
      // 停止
      mediaRecorder.stop();
      mediaRecorder = null;
      $('#record-toggle-btn').textContent = '🎤 开始';
    }
  });

  async function uploadRecording() {
    if (!recordedBlob) return;
    const vid = prompt('voice_id：');
    if (!vid || !VOICE_ID_PATTERN.test(vid)) {
      log('voice_id 格式非法', 'error');
      return;
    }
    const dname = prompt('显示名称：', '') || vid;
    const form = new FormData();
    form.append('voice_id', vid);
    form.append('display_name', dname);
    form.append('file', recordedBlob, `${vid}.webm`);
    log(`📤 上传录音: ${vid} (${(recordedBlob.size/1024).toFixed(1)} KB)`, 'info');
    try {
      const res = await fetch('/api/voices', { method: 'POST', body: form });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      log(`✅ 上传成功: ${data.voice_id}`, 'success');
      fetchVoices();
      // 清掉录音
      recordedBlob = null;
      $('#record-result').innerHTML = '';
      $('#record-status').textContent = '未录音';
    } catch (e) {
      log(`❌ 上传失败: ${e.message}`, 'error');
    }
  }

  // 初始化 2 行（voice 还没拉到，先建框架）
  addVoiceRow();
  addVoiceRow();
  // 拉真实 voice 列表
  fetchVoices();

  // ========== 脚本编辑器：实时字数 / 段数提示 ==========
  $('#script-editor').addEventListener('input', () => {
    const text = $('#script-editor').value;
    const charCount = text.length;
    // 简易段数预估：`---` 出现次数
    const sepHint = (text.match(/^---\s*$/gm) || []).length;
    $('#progress-text').textContent =
      `脚本 ${charCount} 字 · 预估 ${sepHint} 段`;
  });

  // ========== 脚本来源切换（paste / import / ai） ==========
  function switchScriptSource() {
    const src = $('#script-source').value;
    $('#source-paste').style.display = src === 'paste' ? 'block' : 'none';
    $('#source-import').style.display = src === 'import' ? 'block' : 'none';
    $('#source-ai').style.display = src === 'ai' ? 'block' : 'none';
    if (src === 'import') fetchRecentScripts();
    if (src === 'ai') {
      // 显示 / 隐藏 key 提示
      const mmx = window._creds?.minimax;
      if (mmx?.present) {
        $('#ai-hint').innerHTML =
          `<span style="color:var(--color-forest);">✅ MiniMax key 已就绪 (${mmx.prefix})</span>`;
      } else {
        $('#ai-hint').innerHTML =
          `<span style="color:var(--color-clay);">⚠ MiniMax key 未配置，生成会失败</span>`;
      }
    }
  }
  $('#script-source').addEventListener('change', switchScriptSource);

  // ========== 导入历史脚本 ==========
  async function fetchRecentScripts() {
    const sel = $('#recent-scripts-select');
    sel.innerHTML = '<option>加载中…</option>';
    try {
      const res = await fetch('/api/scripts/recent?limit=20');
      const data = await res.json();
      sel.innerHTML = '';
      if (!data.scripts || data.scripts.length === 0) {
        sel.innerHTML = '<option>暂无历史脚本</option>';
        return;
      }
      for (const s of data.scripts) {
        const opt = document.createElement('option');
        opt.value = s.path;
        const date = s.created_at ? s.created_at.replace('T', ' ') : '';
        const topic = s.topic || '(无主题)';
        const preview = s.preview ? ` — ${s.preview}` : '';
        opt.textContent = `[${date}] ${topic} (${s.n_segments}段)${preview}`;
        opt.dataset.editorText = null;  // 占位，懒加载
        sel.appendChild(opt);
      }
    } catch (e) {
      sel.innerHTML = `<option>加载失败: ${e.message}</option>`;
    }
  }
  $('#refresh-recent-btn').addEventListener('click', fetchRecentScripts);

  $('#load-recent-btn').addEventListener('click', async () => {
    const sel = $('#recent-scripts-select');
    const path = sel.value;
    if (!path || path === '加载中…' || path === '暂无历史脚本') {
      log('请先选一个脚本', 'warning');
      return;
    }
    try {
      // 拉 json 文件，读 editor_text
      const res = await fetch('/output/' + encodeURIComponent(path.split('/').pop()));
      // 实际上 scripts 不在 output 目录，要走另一个 endpoint
      // 简单做法：直接 fetch file://... 不可行，浏览器禁用 file://
      // 改用：先 fetch 整个 json 再 parse
      const txt = await fetch(path);
      const json = await txt.json();
      const text = json.editor_text || (
        (json.segments || []).map(s => `---\n【${s.speaker}】\n${s.text}`).join('\n\n')
      );
      $('#script-editor').value = text;
      // 切回 paste 让用户看到内容
      $('#script-source').value = 'paste';
      switchScriptSource();
      log(`✅ 已加载历史脚本 (${(json.segments || []).length} 段)`, 'success');
      parseScript();
    } catch (e) {
      log(`❌ 加载失败: ${e.message}`, 'error');
    }
  });

  // ========== AI 生成（Phase 6） ==========
  $('#ai-generate-btn').addEventListener('click', async () => {
    const topic = $('#ai-topic').value.trim();
    if (!topic) {
      log('主题必填', 'warning');
      return;
    }
    const context = $('#ai-context').value.trim();
    const custom = $('#ai-custom').value.trim();
    const n = parseInt($('#ai-n-speakers').value, 10);
    const apiKey = $('#cred-api-key').value.trim();  // 留空用 config.json

    const btn = $('#ai-generate-btn');
    btn.disabled = true;
    btn.textContent = '⏳ 生成中…';
    window._aiInFlight = true;  // 区分合成错误和生成错误
    log(`🤖 提交生成：主题「${topic}」`, 'info');

    try {
      const res = await fetch('/api/script/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          topic, context, custom,
          n_speakers: n,
          api_key: apiKey,
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      log('⏳ 已提交，后台跑 MiniMax M3…最长 4 分钟', 'info');
    } catch (e) {
      log(`❌ 提交失败: ${e.message}`, 'error');
      btn.disabled = false;
      btn.textContent = '🤖 生成脚本';
      window._aiInFlight = false;
    }
  });

  function onScriptGenError(msg) {
    log(`❌ AI 生成失败: ${msg.message}`, 'error');
    const btn = $('#ai-generate-btn');
    btn.disabled = false;
    btn.textContent = '🤖 生成脚本';
    window._aiInFlight = false;
  }

  // WebSocket 收到 script_done 时自动填到编辑器
  function onScriptDone(data) {
    $('#script-editor').value = data.script_text;
    // 切回 paste 视图
    $('#script-source').value = 'paste';
    switchScriptSource();
    log(`✅ AI 已生成 ${data.count} 段（${data.elapsed_sec}s）— 已填到编辑器`, 'success');
    // 解析一下确认 OK
    parseScript();
    // 按钮复位
    const btn = $('#ai-generate-btn');
    btn.disabled = false;
    btn.textContent = '🤖 生成脚本';
    window._aiInFlight = false;
  }

  // ========== 保存到历史 ==========
  $('#save-script-btn').addEventListener('click', async () => {
    const text = $('#script-editor').value.trim();
    if (!text) {
      log('编辑器是空的', 'warning');
      return;
    }
    const topic = prompt('给这版脚本起个名字（30 字内）：', '手动保存');
    if (topic === null) return;
    try {
      const res = await fetch('/api/scripts/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ script_text: text, topic }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      log(`💾 已保存：${data.name}`, 'success');
    } catch (e) {
      log(`❌ 保存失败: ${e.message}`, 'error');
    }
  });

  // ========== 脚本解析（Phase 2 接通） ==========
  async function parseScript() {
    const text = $('#script-editor').value.trim();
    const preview = $('#parse-preview');

    if (!text) {
      preview.innerHTML = '<div class="placeholder"><div>脚本为空，先在左侧编辑器粘贴脚本</div></div>';
      return;
    }

    preview.innerHTML = '<div class="placeholder"><div>⏳ 解析中…</div></div>';

    try {
      const res = await fetch('/api/parse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      renderParseResult(data);
      log(`✅ parse 成功: ${data.count} 段`, 'success');
    } catch (e) {
      preview.innerHTML =
        `<div class="placeholder" style="color:var(--color-error);">
          <div>❌ parse 失败</div>
          <div style="font-size:12px; margin-top:6px; text-align:left;
                      white-space:pre-wrap;">${escapeHtml(e.message)}</div>
        </div>`;
      log(`❌ parse 失败: ${e.message}`, 'error');
    }
  }

  function renderParseResult(data) {
    const preview = $('#parse-preview');
    // speaker 分布（按行展示）
    const speakerLines = Object.entries(data.speakers)
      .map(([sp, cnt]) => `<div>· <b>${escapeHtml(sp)}</b>: ${cnt} 段</div>`)
      .join('');

    // 段列表（前 8 段 + "..." 如果超过 8）
    const maxShow = 8;
    const segLines = data.segments.slice(0, maxShow).map((s, i) => {
      const preview = s.text.length > 50
        ? s.text.slice(0, 50) + '…'
        : s.text;
      // 主播甲/乙/丙 用不同色
      const speakerClass = s.speaker.replace(/[主播]/g, '').slice(0, 1) || '甲';
      return `<div class="seg ${speakerClass}">
        <span class="seg-speaker">${i+1}. [${escapeHtml(s.speaker)}]</span>
        <span class="seg-text">${escapeHtml(preview)}</span>
      </div>`;
    }).join('');
    const more = data.segments.length > maxShow
      ? `<div style="color:var(--color-text-light); font-size:11px;">
          …还有 ${data.segments.length - maxShow} 段（合成时会全部用对应音色）</div>`
      : '';

    preview.innerHTML = `
      <div class="parse-result">
        <div style="font-size:13px; color:var(--color-success); font-weight:bold; margin-bottom:6px;">
          ✅ 解析出 ${data.count} 段
        </div>
        <div style="margin-bottom:8px; padding:6px; background:var(--color-cream);
                    border-radius:4px; font-size:12px;">
          ${speakerLines}
        </div>
        <div>
          ${segLines}
        </div>
        ${more}
      </div>
    `;
  }

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  $('#parse-btn').addEventListener('click', parseScript);

  // 编辑器内容变化时自动 parse（debounce 500ms）
  let parseDebounce = null;
  $('#script-editor').addEventListener('input', () => {
    if (parseDebounce) clearTimeout(parseDebounce);
    parseDebounce = setTimeout(parseScript, 500);
  });

  // 页面加载后若有初始内容，自动 parse 一次
  window.addEventListener('load', () => {
    if ($('#script-editor').value.trim()) parseScript();
  });

  // ========== WebSocket 实时日志 / 进度 ==========
  let ws = null;
  let wsReconnectTimer = null;

  function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws`;
    log(`🔌 连接 WebSocket: ${url}`, 'info');
    ws = new WebSocket(url);

    ws.onopen = () => {
      log('✅ WebSocket 已连接', 'success');
      $('#ws-status').className = 'connected';
      setStatus('✅ 准备就绪');
    };

    ws.onclose = () => {
      $('#ws-status').className = 'disconnected';
    };

    ws.onerror = () => {
      $('#ws-status').className = 'disconnected';
    };

    ws.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      handleWSMessage(msg);
    };

    ws.onerror = () => {
      log('❌ WebSocket 错误', 'error');
    };

    ws.onclose = () => {
      log('🔌 WebSocket 已断开，3s 后重连…', 'warning');
      wsReconnectTimer = setTimeout(connectWS, 3000);
    };
  }

  function handleWSMessage(msg) {
    switch (msg.type) {
      case 'hello':
        // 已经在 onopen log 过了，不重复
        break;
      case 'log':
        log(msg.message, msg.level || 'info');
        break;
      case 'progress':
        updateProgress(msg.done, msg.total);
        break;
      case 'done':
        onSynthesisDone(msg);
        break;
      case 'script_done':
        onScriptDone(msg);
        break;
      case 'error':
        // 区分合成错误和脚本生成错误
        if (window._aiInFlight) {
          onScriptGenError(msg);
        } else {
          onSynthesisError(msg);
        }
        break;
      case 'pong':
        // 心跳，忽略
        break;
      default:
        console.warn('未知 WS 消息:', msg);
    }
  }

  function updateProgress(done, total) {
    const pct = total > 0 ? Math.round(done / total * 100) : 0;
    const bar = $('#progress-bar');
    bar.style.width = `${pct}%`;
    bar.textContent = `${pct}%`;
    $('#progress-text').textContent = `合成进度：${done} / ${total} 段`;
  }

  // ========== 音频历史（最近 5 个） ==========
  const audioHistory = [];

  function pushAudioHistory(output, order) {
    const audioUrl = '/' + output.replace(/^\.\//, '').replace(/^output\//, 'output/');
    const name = output.split('/').pop();
    const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    audioHistory.unshift({ url: audioUrl, name, ts, order });
    if (audioHistory.length > 5) audioHistory.pop();
    renderAudioHistory();
  }

  function renderAudioHistory() {
    const playerArea = $('#audio-player-area');
    if (audioHistory.length === 0) {
      playerArea.innerHTML = '<div class="placeholder"><div>生成的 mp3 会在这里显示（最近 5 个）</div></div>';
      $('#clear-audio-btn').style.display = 'none';
      return;
    }
    const items = audioHistory.map((a, i) => `
      <div class="audio-history-item">
        <span class="a-name">${escapeHtml(a.name)}<br><span style="font-size:9px;">${a.ts}</span></span>
        <audio controls src="${a.url}" preload="none"></audio>
      </div>
    `).join('');
    playerArea.innerHTML = items;
    $('#clear-audio-btn').style.display = 'inline-block';
  }

  $('#clear-audio-btn').addEventListener('click', () => {
    audioHistory.length = 0;
    renderAudioHistory();
  });

  function onSynthesisDone(msg) {
    const output = msg.output;
    log(`✅ 合成完成: ${output}`, 'success');
    log(`📋 合成顺序: ${msg.order}`, 'info');
    setStatus('✅ 已生成', 'ok');
    $('#generate-btn').disabled = false;
    $('#generate-btn').textContent = '🎵 生成音频';

    // Phase 7: 加到历史而不是直接覆盖
    pushAudioHistory(output, msg.order);
  }

  function onSynthesisError(msg) {
    log(`❌ 合成失败: ${msg.message}`, 'error');
    if (msg.traceback) {
      log('Traceback (最后 500 字符):', 'error');
      log(msg.traceback, 'error');
    }
    setStatus('❌ 合成失败', 'err');
    $('#generate-btn').disabled = false;
    $('#generate-btn').textContent = '🎵 重试生成';
  }

  // 心跳保活
  setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send('ping');
    }
  }, 30000);

  // ========== 生成按钮 ==========
  let lastParseResult = null;

  // 把 parse 完的结果存下来
  const _origRender = renderParseResult;
  renderParseResult = function(data) {
    lastParseResult = data;
    _origRender(data);
    updateGenerateBtn();
  };

  function updateGenerateBtn() {
    const btn = $('#generate-btn');
    const hasSegments = lastParseResult && lastParseResult.count > 0;
    const hasVoices = voiceRows.length >= 2;
    btn.disabled = !(hasSegments && hasVoices);
    if (hasSegments && hasVoices) {
      btn.textContent = `🎵 生成音频（${lastParseResult.count} 段）`;
    } else {
      btn.textContent = '🎵 生成音频（需先 parse + 选 2 个 voice）';
    }
  }

  // 监听 voice rows 变化
  const _origAdd = addVoiceRow;
  addVoiceRow = function() { _origAdd(); updateGenerateBtn(); };
  const _origRemove = removeVoiceRow;
  removeVoiceRow = function(i) { _origRemove(i); updateGenerateBtn(); };
  $('#voice-select, .voice-select').forEach && $('#voice-select, .voice-select').forEach(s => {
    s.addEventListener && s.addEventListener('change', updateGenerateBtn);
  });

  $('#generate-btn').addEventListener('click', async () => {
    if (!lastParseResult || lastParseResult.count === 0) {
      log('❌ 没有 parse 结果，先 parse 脚本', 'error');
      return;
    }
    // 收集 voice_map / speed_map
    const voiceMap = {};
    const speedMap = {};
    voiceRows.forEach((row, i) => {
      const sel = row.querySelector('.voice-selector');
      const speedInput = row.querySelector('.voice-speed-input');
      const vid = sel.value;
      if (vid) {
        const label = ROLE_LABELS[i] || `主播${i+1}`;
        voiceMap[label] = vid;
        // 读速度滑块（默认 1.0，clamp 到 0.5-1.5）
        let s = parseFloat(speedInput.value);
        if (isNaN(s)) s = 1.0;
        s = Math.max(0.5, Math.min(1.5, s));
        speedInput.value = s;
        speedMap[label] = s;
      }
    });
    if (Object.keys(voiceMap).length < 2) {
      log(`❌ voice_map 只有 ${Object.keys(voiceMap).length} 个 voice，至少需要 2 个（甲 + 乙）`, 'error');
      return;
    }

    // 根据引擎选 API endpoint
    const engine = $('#engine-select').value;
    let endpoint, body;
    if (engine === 'voxcpm') {
      endpoint = '/api/synthesize/voxcpm';
      body = { segments: lastParseResult.segments, voice_map: voiceMap, speed_map: speedMap };
    } else if (engine === 'minimax') {
      const apiKey = $('#cred-api-key').value.trim();  // 留空 → config.json fallback
      endpoint = '/api/synthesize/minimax';
      body = {
        segments: lastParseResult.segments,
        voice_map: voiceMap,
        speed_map: speedMap,
        api_key: apiKey,
        model: 'speech-2.6-hd',
      };
    } else if (engine === 'doubao') {
      const appid = $('#cred-app-id').value.trim();
      const token = $('#cred-token').value.trim();
      if (!appid || !token) {
        log('❌ 豆包需要 App ID + Token（在凭证栏填）', 'error');
        return;
      }
      // 豆包只支持 2 个 speaker；取 voiceMap 前 2 个 value
      const speakers = Object.values(voiceMap).slice(0, 2);
      endpoint = '/api/synthesize/doubao';
      body = {
        segments: lastParseResult.segments,
        speakers,
        appid,
        token,
      };
    }

    log(`🚀 启动合成 [${engine}]：${lastParseResult.count} 段, voices=${JSON.stringify(voiceMap)}`, 'info');
    setStatus('🎙️ 合成中…', 'warn');
    $('#generate-btn').disabled = true;
    $('#generate-btn').textContent = '⏳ 合成中…';
    $('#audio-player-area').innerHTML = '<div class="placeholder"><div>⏳ 合成中…</div></div>';

    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      log(`📋 任务已启动: ${data.task_id} → ${data.output}`, 'info');
    } catch (e) {
      log(`❌ 启动合成失败: ${e.message}`, 'error');
      setStatus('❌ 启动失败', 'err');
      $('#generate-btn').disabled = false;
      $('#generate-btn').textContent = '🎵 重试生成';
    }
  });

  // 引擎切换时更新凭证提示
  $('#engine-select').addEventListener('change', () => {
    const e = $('#engine-select').value;
    const hint = $('#cred-hint');
    if (e === 'voxcpm') hint.textContent = 'VoxCPM 引擎不需要凭证（本地推理）';
    else if (e === 'minimax') hint.textContent = 'MiniMax 引擎需要 API Key';
    else if (e === 'doubao') hint.textContent = '豆包引擎需要 App ID + Token';
  });

  // ========== 启动 ==========
  connectWS();
  bootCheck();
  log('前端 JS 加载完成', 'info');

  // ========== 启动 ==========
  bootCheck();
  log('前端 JS 加载完成', 'info');
})();
