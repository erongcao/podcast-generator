// auth-bar.js — 顶栏登录状态管理（共享）
//
// 行为：
//   1. fetchMe(force?)  拉一次 /api/auth/me，缓存 60s
//   2. injectAuthBar(opts?)  在指定容器里渲染登录态 UI
//        - 未登录：单一 "登录" 链接到 /login
//        - 已登录：头像 + 显示名 + 下拉菜单（账号信息 / 切换 / 退出）
//   3. logout()  POST /api/auth/logout → 刷新页面
//
// 设计：IIFE + window.PodcastAuth，无 ES 模块依赖，兼容老浏览器
//       也不会和已有的 IIFE 风格的 app.js / editor.js 冲突。
//
// 调用方约定：
//   - 页面 HTML 中放一个 <div class="auth-bar-slot" id="auth-bar-slot"></div>
//   - 页面底部 <script src="/static/js/auth-bar.js"></script> 先于 app.js / editor.js
//   - DOMContentLoaded 后，调用 window.PodcastAuth.injectAuthBar()

(function () {
    'use strict';

    const CACHE_TTL_MS = 60 * 1000;  // 1 分钟内复用 /me 结果
    const ME_ENDPOINT = '/api/auth/me';
    const LOGOUT_ENDPOINT = '/api/auth/logout';

    let _me = null;
    let _fetchedAt = 0;
    let _pending = null;

    async function fetchMe(force) {
        const now = Date.now();
        if (!force && _me !== null && (now - _fetchedAt) < CACHE_TTL_MS) {
            return _me;
        }
        if (_pending) return _pending;
        _pending = (async () => {
            try {
                const res = await fetch(ME_ENDPOINT, {
                    credentials: 'same-origin',
                    headers: { 'Accept': 'application/json' },
                });
                if (res.status === 401) {
                    _me = null;
                } else if (res.ok) {
                    _me = await res.json();
                } else {
                    _me = null;
                }
            } catch (e) {
                console.warn('[PodcastAuth] /me fetch failed:', e);
                _me = null;
            } finally {
                _fetchedAt = Date.now();
                _pending = null;
            }
            return _me;
        })();
        return _pending;
    }

    function clearCache() {
        _me = null;
        _fetchedAt = 0;
        _pending = null;
    }

    async function logout() {
        try {
            await fetch(LOGOUT_ENDPOINT, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json' },
            });
        } catch (e) {
            console.error('[PodcastAuth] Logout failed:', e);
        }
        clearCache();
        // 刷页面，让 /me 重新返回 401，UI 回到"登录"按钮
        window.location.reload();
    }

    function escapeHtml(s) {
        if (s == null) return '';
        const div = document.createElement('div');
        div.textContent = String(s);
        return div.innerHTML;
    }

    function renderLoggedOut(container) {
        container.innerHTML = '';
        const link = document.createElement('a');
        link.href = '/login';
        link.className = 'btn btn-primary btn-small auth-login-btn';
        link.textContent = '登录';
        link.setAttribute('aria-label', '登录或注册');
        container.appendChild(link);
    }

    function renderLoggedIn(container, me) {
        container.innerHTML = '';
        const wrap = document.createElement('div');
        wrap.className = 'auth-logged-in';

        const displayName = me.display_name || me.username || '用户';
        const initial = displayName.charAt(0).toUpperCase();

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-small auth-user-btn';
        btn.setAttribute('aria-haspopup', 'menu');
        btn.setAttribute('aria-expanded', 'false');
        btn.innerHTML =
            '<span class="auth-avatar">' + escapeHtml(initial) + '</span>' +
            '<span class="auth-name">' + escapeHtml(displayName) + '</span>' +
            '<span class="auth-caret" aria-hidden="true">▾</span>';

        const menu = document.createElement('div');
        menu.className = 'auth-dropdown';
        menu.hidden = true;
        menu.setAttribute('role', 'menu');

        const info = document.createElement('div');
        info.className = 'auth-info';
        info.setAttribute('role', 'presentation');
        info.innerHTML =
            '<div class="auth-name-bold">' + escapeHtml(displayName) + '</div>' +
            (me.email
                ? '<div class="auth-email">' + escapeHtml(me.email) + '</div>'
                : '') +
            (me.is_admin
                ? '<div class="auth-admin-badge">管理员</div>'
                : '');

        const sep = document.createElement('hr');
        sep.setAttribute('role', 'separator');

        const switchLink = document.createElement('a');
        switchLink.href = '/login';
        switchLink.setAttribute('role', 'menuitem');
        switchLink.textContent = '切换账号';

        const logoutBtn = document.createElement('button');
        logoutBtn.type = 'button';
        logoutBtn.className = 'auth-logout';
        logoutBtn.setAttribute('role', 'menuitem');
        logoutBtn.textContent = '退出';

        menu.appendChild(info);
        menu.appendChild(sep);
        menu.appendChild(switchLink);
        menu.appendChild(logoutBtn);

        wrap.appendChild(btn);
        wrap.appendChild(menu);
        container.appendChild(wrap);

        // 切换下拉显示
        const closeMenu = () => {
            menu.hidden = true;
            btn.setAttribute('aria-expanded', 'false');
        };
        const toggleMenu = (e) => {
            e.stopPropagation();
            const willOpen = menu.hidden;
            menu.hidden = !willOpen;
            btn.setAttribute('aria-expanded', String(willOpen));
        };
        btn.addEventListener('click', toggleMenu);
        logoutBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            logout();
        });

        // 点击外部关闭
        document.addEventListener('click', closeMenu);
        // Esc 关闭（无障碍）
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !menu.hidden) {
                closeMenu();
                btn.focus();
            }
        });
        // 菜单内部点击不冒泡触发外部关闭
        menu.addEventListener('click', (e) => e.stopPropagation());
    }

    function renderPending(container) {
        // 避免 FOUC 的占位
        container.innerHTML = '';
        const span = document.createElement('span');
        span.className = 'auth-placeholder';
        span.textContent = '···';
        span.setAttribute('aria-hidden', 'true');
        container.appendChild(span);
    }

    function injectAuthBar(opts) {
        opts = opts || {};
        const container = opts.container
            || document.querySelector('.auth-bar-slot')
            || document.getElementById('auth-bar-slot');
        if (!container) {
            console.warn('[PodcastAuth] No auth-bar slot found, skipping inject.');
            return;
        }
        renderPending(container);
        fetchMe(false).then((me) => {
            if (me) {
                renderLoggedIn(container, me);
            } else {
                renderLoggedOut(container);
            }
        });
    }

    window.PodcastAuth = {
        fetchMe: fetchMe,
        logout: logout,
        injectAuthBar: injectAuthBar,
        clearCache: clearCache,
    };
})();
