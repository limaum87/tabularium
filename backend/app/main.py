from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os
import asyncio
import subprocess

from app.core.database import engine, Base, SessionLocal, User, Host
from app.core.config import settings
from app.core.security import hash_password
from app.api import auth, users, hosts, reports, settings as settings_api, discovery

# ---- Background Ping Task ----

PING_INTERVAL = 300  # 5 minutos
_ping_task = None


async def _ping_loop():
    """Background task: faz ping em todos os hosts a cada 5 minutos."""
    while True:
        try:
            await asyncio.sleep(PING_INTERVAL)
            _run_ping_sweep()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[ping-sweep] Erro: {e}")


def _run_ping_sweep():
    """Pinga todos os hosts ativos e atualiza ping_status."""
    from datetime import datetime
    db = SessionLocal()
    try:
        active_hosts = db.query(Host).filter(Host.is_legacy == False).all()
        if not active_hosts:
            return

        now = datetime.utcnow()
        online_count = 0
        offline_count = 0

        for host in active_hosts:
            try:
                result = subprocess.run(
                    ["ping", "-c", "1", "-W", "2", host.hostname],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    host.ping_status = "online"
                    online_count += 1
                else:
                    host.ping_status = "offline"
                    offline_count += 1
            except Exception:
                host.ping_status = "offline"
                offline_count += 1
            host.last_ping = now

        db.commit()
        print(f"[ping-sweep] {len(active_hosts)} hosts verificados — online: {online_count} | offline: {offline_count}")
    except Exception as e:
        print(f"[ping-sweep] Erro geral: {e}")
    finally:
        db.close()


# ---- Lifespan ----

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Cria tabelas, faz seed do admin, aplica DNS e inicia ping sweep."""
    Base.metadata.create_all(bind=engine)
    _migrate_db()
    _seed_admin()
    _apply_dns_from_db()

    # Inicia background ping task
    global _ping_task
    _ping_task = asyncio.create_task(_ping_loop())
    print(f"[ping-sweep] Iniciado — intervalo: {PING_INTERVAL}s")

    yield

    # Cancela task ao desligar
    if _ping_task:
        _ping_task.cancel()


def _migrate_db():
    """Aplica migrations pendentes (ALTER TABLE para novas colunas)."""
    import sqlalchemy

    conn = engine.connect()
    try:
        # Migration 1: adicionar ping_status e last_ping na tabela hosts
        inspector = sqlalchemy.inspect(engine)
        columns = [col['name'] for col in inspector.get_columns('hosts')]

        if 'ping_status' not in columns:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE hosts ADD COLUMN ping_status ENUM('online','offline','unknown') DEFAULT 'unknown'"
            ))
            print("[migration] Adicionado ping_status na tabela hosts")

        if 'last_ping' not in columns:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE hosts ADD COLUMN last_ping DATETIME DEFAULT NULL"
            ))
            print("[migration] Adicionado last_ping na tabela hosts")

        conn.commit()
    except Exception as e:
        print(f"[migration] Erro: {e}")
    finally:
        conn.close()


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
    """Lê DNS das settings do banco e aplica no /etc/resolv.conf.
    Preserva o DNS interno do Docker (127.0.0.11) para resolução de containers."""
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

        # Monta resolv.conf: preserva Docker DNS + adiciona DNS customizado
        lines = []
        if search:
            lines.append(f"search {search}")

        # Mantém DNS interno do Docker (resolver de containers)
        lines.append("nameserver 127.0.0.11")

        # Adiciona DNS customizado da rede
        for ip in dns_ips:
            lines.append(f"nameserver {ip}")

        # Adiciona options do Docker
        lines.append("options edns0 trust-ad ndots:0")

        resolv_path = "/etc/resolv.conf"

        try:
            # Remove symlink se existir
            if os.path.islink(resolv_path):
                os.unlink(resolv_path)

            with open(resolv_path, "w") as f:
                f.write("\n".join(lines) + "\n")

            print(f"[dns] DNS aplicado do banco (preservando Docker DNS): {', '.join(dns_ips)}" + (f" | search: {search}" if search else ""))
        except PermissionError:
            print("[dns] Sem permissão para escrever /etc/resolv.conf")
        except Exception as e:
            print(f"[dns] Erro ao aplicar DNS: {e}")
    finally:
        db.close()


app = FastAPI(
    title="Tabularium",
    description="Inventário de máquinas Windows em domínio",
    version="0.2.0",
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
