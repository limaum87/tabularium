from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os

from app.core.database import engine, Base, SessionLocal, User
from app.core.config import settings
from app.core.security import hash_password
from app.api import auth, users, hosts, reports, settings as settings_api, discovery


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Cria tabelas, faz seed do admin e aplica DNS na primeira execução."""
    Base.metadata.create_all(bind=engine)
    _seed_admin()
    _apply_dns_from_db()
    yield


def _seed_admin():
    db = SessionLocal()
    try:
        exists = db.query(User).filter(User.email == settings.ADMIN_EMAIL).first()
        if not exists:
            admin = User(
                name="Administrador",
                email=settings.ADMIN_EMAIL,
                password=hash_password(settings.ADMIN_PASSWORD),
                role="admin",
                is_active=True,
            )
            db.add(admin)
            db.commit()
            print(f"[seed] Admin criado: {settings.ADMIN_EMAIL}")
    finally:
        db.close()


def _apply_dns_from_db():
    """Lê DNS das settings do banco e aplica no /etc/resolv.conf."""
    import re as re_mod

    db = SessionLocal()
    try:
        from app.core.database import Setting
        dns_servers = db.query(Setting).filter(Setting.key == "servers", Setting.category == "dns").first()
        search_domain = db.query(Setting).filter(Setting.key == "search_domain", Setting.category == "dns").first()

        if not dns_servers or not dns_servers.value or not dns_servers.value.strip():
            print("[dns] Nenhum DNS configurado no banco. Usando DNS padrão do container.")
            return

        dns_ips = [ip.strip() for ip in dns_servers.value.split(",") if ip.strip()]
        if not dns_ips:
            return

        search = search_domain.value.strip() if search_domain and search_domain.value else ""

        lines = []
        if search:
            lines.append(f"search {search}")
        for ip in dns_ips:
            lines.append(f"nameserver {ip}")

        resolv_path = "/etc/resolv.conf"

        try:
            # Remove symlink se existir
            if os.path.islink(resolv_path):
                os.unlink(resolv_path)

            with open(resolv_path, "w") as f:
                f.write("\n".join(lines) + "\n")

            print(f"[dns] DNS aplicado do banco: {', '.join(dns_ips)}" + (f" | search: {search}" if search else ""))
        except PermissionError:
            print("[dns] Sem permissão para escrever /etc/resolv.conf")
        except Exception as e:
            print(f"[dns] Erro ao aplicar DNS: {e}")
    finally:
        db.close()


app = FastAPI(
    title="Tabularium",
    description="Inventário de máquinas Windows em domínio",
    version="0.1.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

# Rotas da API
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(hosts.router)
app.include_router(reports.router)
app.include_router(settings_api.router)
app.include_router(discovery.router)


# Health check
@app.get("/api/health")
def health():
    return {"status": "ok"}


# Frontend estático (em produção serve pelo Nginx, mas facilita dev)
frontend_path = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.isdir(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
