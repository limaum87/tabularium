/**
 * Tabularium — Utilitários de autenticação
 */

const API_BASE = '';

function getToken() {
    return localStorage.getItem('token');
}

function authHeaders() {
    const token = getToken();
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

async function apiFetch(url, options = {}) {
    const headers = {
        ...options.headers,
        ...authHeaders(),
    };
    if (options.body && typeof options.body === 'object') {
        headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(options.body);
    }
    const res = await fetch(API_BASE + url, { ...options, headers });
    if (res.status === 401) {
        localStorage.removeItem('token');
        window.location.href = '/login.html';
        return;
    }
    return res;
}

function checkAuth() {
    if (!getToken()) {
        window.location.href = '/login.html';
    }
}

function logout() {
    localStorage.removeItem('token');
    window.location.href = '/login.html';
}
