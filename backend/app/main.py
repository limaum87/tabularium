from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os

from app.core.database import engine, Base, SessionLocal, User
from app.core.config import settings
from app.core.security import hash_password
from app.api import auth, users, hosts, reports, settings as settings_api


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Cria tabelas e faz seed do admin na primeira execução."""
    Base.metadata.create_all(bind=engine)
    _seed_admin()
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


# Health check
@app.get("/api/health")
def health():
    return {"status": "ok"}


# Frontend estático (em produção serve pelo Nginx, mas facilita dev)
frontend_path = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.isdir(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
