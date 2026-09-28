/**
 * Tabularium — Layout (sidebar + header)
 */

function loadLayout(activePage) {
    const app = document.getElementById('app');
    if (!app) return;

    // Detecta role do usuário para mostrar/esconder menus
    const token = getToken();
    let userRole = 'viewer';
    if (token) {
        try {
            const payload = JSON.parse(atob(token.split('.')[1]));
            userRole = payload.role || 'viewer';
        } catch(e) {}
    }

    const isAdmin = userRole === 'admin';
    const isOperator = userRole === 'operator' || isAdmin;

    const navItems = [
        { section: 'Geral' },
        { id: 'dashboard', icon: '◫', label: 'Dashboard', href: '/dashboard.html' },
        { id: 'hosts', icon: '⊡', label: 'Hosts', href: '/hosts.html' },
        { id: 'activity', icon: '◈', label: 'Atividades', href: '/activity.html' },
        { id: 'tasks', icon: '⚙', label: 'Tasks', href: '/tasks.html' },
        { id: 'legacy', icon: '⊘', label: 'Micros Legados', href: '/legacy.html', badge: true },
        { section: 'Inventário' },
        { id: 'logged-users', icon: '⊙', label: 'Usuários Logados', href: '/logged-users.html' },
        { id: 'software', icon: '◈', label: 'Softwares', href: '/software.html' },
        { id: 'licenses', icon: '◎', label: 'Licenças', href: '/licenses.html' },
        { id: 'windows-versions', icon: '⊞', label: 'Versões Windows', href: '/windows-versions.html' },
        { section: 'Segurança' },
        { id: 'patches', icon: '🩹', label: 'Patch Compliance', href: '/patches.html' },
        { id: 'vulnerabilities', icon: '🛡', label: 'Vulnerabilidades', href: '/vulnerabilities.html' },
    ];

    if (isAdmin) {
        navItems.push(
            { section: 'Administração' },
            { id: 'users', icon: '⊞', label: 'Usuários', href: '/users.html' },
            { id: 'settings', icon: '⚙', label: 'Configurações', href: '/settings.html' },
        );
    }

    const navHTML = navItems.map(item => {
        if (item.section) {
            return `<div class="nav-section">${item.section}</div>`;
        }
        const active = item.id === activePage ? ' active' : '';
        const badgeId = item.badge ? ` id="nav-badge-${item.id}"` : '';
        const badgeHTML = item.badge ? ` <span class="nav-badge" style="display:none;"></span>` : '';
        return `<a href="${item.href}" class="nav-link${active}"${badgeId}><span class="nav-icon">${item.icon}</span>${item.label}${badgeHTML}</a>`;
    }).join('');

    app.innerHTML = `
    <div class="app-layout">
        <aside class="sidebar">
            <div class="sidebar-brand">
                <img src="/assets/logo.png" alt="Tabularium" style="height:36px;width:auto;border-radius:6px;">
                <div class="brand-text">
                    <span class="brand-name">Tabularium</span>
                    <span class="brand-sub">Inventário de Ativos</span>
                </div>
            </div>
            <nav class="sidebar-nav">
                ${navHTML}
            </nav>
            <div class="sidebar-footer">
                <div class="sidebar-user">
                    <div class="user-avatar" id="user-initials">—</div>
                    <div class="user-info">
                        <div class="user-name" id="user-name">Carregando...</div>
                        <div class="user-role" id="user-role-label">—</div>
                    </div>
                    <button class="btn-logout" onclick="logout()" title="Sair">⏻</button>
                </div>
            </div>
        </aside>
        <main class="main-content">
            ${app.innerHTML}
        </main>
    </div>
    `;

    // Load user info
    (async () => {
        const res = await apiFetch('/api/auth/me');
        if (res) {
            const user = await res.json();
            const name = user.name || user.email;
            document.getElementById('user-name').textContent = name;
            document.getElementById('user-role-label').textContent = user.role;
            const initials = name.split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();
            document.getElementById('user-initials').textContent = initials;
        }
    })();

    // Load legacy badge count
    (async () => {
        const badge = document.getElementById('nav-badge-legacy');
        if (!badge) return;
        try {
            const res = await apiFetch('/api/hosts/legacy');
            if (res && res.ok) {
                const legacyHosts = await res.json();
                const count = legacyHosts.length;
                if (count > 0) {
                    const span = badge.querySelector('.nav-badge');
                    if (span) {
                        span.style.display = 'inline-flex';
                        span.textContent = count;
                    }
                }
            }
        } catch(e) {}
    })();
}
