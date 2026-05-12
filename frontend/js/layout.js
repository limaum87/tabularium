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
        { section: 'Inventário' },
        { id: 'software', icon: '◈', label: 'Softwares', href: '/software.html' },
        { id: 'licenses', icon: '◎', label: 'Licenças', href: '/licenses.html' },
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
        return `<a href="${item.href}" class="nav-link${active}"><span class="nav-icon">${item.icon}</span>${item.label}</a>`;
    }).join('');

    app.innerHTML = `
    <div class="app-layout">
        <aside class="sidebar">
            <div class="sidebar-brand">
                <div class="brand-icon">T</div>
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
}
