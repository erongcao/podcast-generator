// login.js — 登录/注册页交互

'use strict';

const tabLogin = document.getElementById('tab-login-btn');
const tabRegister = document.getElementById('tab-register-btn');
const loginForm = document.getElementById('login-form');
const registerForm = document.getElementById('register-form');
const loginErr = document.getElementById('login-error');
const registerErr = document.getElementById('register-error');

function showError(el, msg) {
    el.textContent = msg;
    el.hidden = false;
    setTimeout(() => { el.hidden = true; }, 5000);
}

function hideError(el) {
    el.hidden = true;
}

tabLogin.addEventListener('click', () => {
    tabLogin.classList.add('active');
    tabRegister.classList.remove('active');
    loginForm.hidden = false;
    registerForm.hidden = true;
    hideError(loginErr);
    hideError(registerErr);
});

tabRegister.addEventListener('click', () => {
    tabRegister.classList.add('active');
    tabLogin.classList.remove('active');
    registerForm.hidden = false;
    loginForm.hidden = true;
    hideError(loginErr);
    hideError(registerErr);
});

loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideError(loginErr);
    const formData = new FormData(loginForm);
    const body = {
        username: formData.get('username').trim(),
        password: formData.get('password'),
    };
    try {
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const detail = await res.json().catch(() => ({}));
            showError(loginErr, detail.detail || `登录失败 (${res.status})`);
            return;
        }
        // 登录成功 → 跳主页
        window.location.href = '/';
    } catch (e) {
        showError(loginErr, `网络错误: ${e.message}`);
    }
});

registerForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideError(registerErr);
    const formData = new FormData(registerForm);
    const body = {
        username: formData.get('username').trim(),
        password: formData.get('password'),
        email: formData.get('email') || null,
        display_name: formData.get('display_name') || null,
    };
    try {
        const res = await fetch('/api/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const detail = await res.json().catch(() => ({}));
            showError(registerErr, detail.detail || `注册失败 (${res.status})`);
            return;
        }
        // 注册成功 → 直接登录态（session cookie 自动 set）
        window.location.href = '/';
    } catch (e) {
        showError(registerErr, `网络错误: ${e.message}`);
    }
});

// 如果已登录，跳主页
(async () => {
    try {
        const res = await fetch('/api/auth/me');
        if (res.ok) window.location.href = '/';
    } catch { /* 未登录，正常 */ }
})();