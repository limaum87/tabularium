/**
 * Tabularium — Layout (navbar + sidebar)
 */

function loadNavbar() {
    const placeholder = document.getElementById('navbar-placeholder');
    if (!placeholder) return;

    placeholder.innerHTML = `
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <div class="container-fluid">
            <a class="navbar-brand" href="/dashboard.html">🖥️ Tabularium</a>
            <div class="d-flex align-items-center text-white">
                <span id="user-name" class="me-3">—</span>
                <button class="btn btn-outline-light btn-sm" onclick="logout()">Sair</button>
            </div>
        </div>
    </nav>
    `;

    // Busca nome do usuário
    (async () => {
        const res = await apiFetch('/api/auth/me');
        if (res) {
            const user = await res.json();
            document.getElementById('user-name').textContent = user.name || user.email;
        }
    })();
}
