import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.core.database import get_db, Setting
from app.core.security import require_role

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ---- Schemas ----

class SettingInput(BaseModel):
    key: str
    value: str
    category: str = "general"


class SettingsBatch(BaseModel):
    settings: list[SettingInput]


class LdapTestInput(BaseModel):
    server: str
    bind_dn: str
    password: str
    base_dn: str
    search_filter: str = "(objectClass=computer)"


class WinrmTestInput(BaseModel):
    hostname: str
    username: str
    password: str
    scheme: str = "http"
    port: int = 5985


# ---- CRUD ----

@router.get("")
def list_settings(db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    """Lista todas as configurações agrupadas por categoria."""
    rows = db.query(Setting).order_by(Setting.category, Setting.key).all()
    result = {}
    for r in rows:
        cat = r.category
        if cat not in result:
            result[cat] = {}
        # Tenta fazer parse de JSON, senão retorna string
        try:
            result[cat][r.key] = json.loads(r.value)
        except (json.JSONDecodeError, TypeError):
            result[cat][r.key] = r.value
    return result


@router.put("")
def save_settings(body: SettingsBatch, db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    """Salva um lote de configurações (upsert)."""
    now = datetime.utcnow()
    for item in body.settings:
        existing = db.query(Setting).filter(Setting.key == item.key).first()
        if existing:
            existing.value = item.value
            existing.category = item.category
            existing.updated_at = now
        else:
            db.add(Setting(key=item.key, value=item.value, category=item.category, updated_at=now))
    db.commit()
    return {"detail": "Configurações salvas"}


@router.delete("/{key}")
def delete_setting(key: str, db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    """Remove uma configuração."""
    s = db.query(Setting).filter(Setting.key == key).first()
    if not s:
        raise HTTPException(status_code=404, detail="Configuração não encontrada")
    db.delete(s)
    db.commit()
    return {"detail": "Configuração removida"}


# ---- Testes de Conexão ----

@router.post("/test-ldap")
def test_ldap(body: LdapTestInput, _=Depends(require_role("admin"))):
    """Testa conexão LDAP e retorna quantidade de computadores encontrados."""
    try:
        from ldap3 import Server, Connection, ALL, SUBTREE
    except ImportError:
        raise HTTPException(status_code=500, detail="Biblioteca ldap3 não instalada no backend")

    try:
        server = Server(body.server, get_info=ALL, connect_timeout=10)
        conn = Connection(server, user=body.bind_dn, password=body.password, auto_bind=True, read_only=True)

        conn.search(
            search_base=body.base_dn,
            search_filter=body.search_filter,
            search_scope=SUBTREE,
            attributes=["cn", "name"],
        )

        count = len(conn.entries)
        hosts = []
        for entry in conn.entries[:20]:  # Limita a 20 para preview
            name = ""
            if hasattr(entry, "cn") and entry.cn.value:
                name = entry.cn.value
            elif hasattr(entry, "name") and entry.name.value:
                name = entry.name.value
            if name:
                hosts.append(name)

        conn.unbind()

        return {
            "success": True,
            "message": f"Conexão OK — {count} computadores encontrados",
            "total": count,
            "preview": hosts,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Falha na conexão: {str(e)[:200]}",
            "total": 0,
            "preview": [],
        }


@router.post("/test-winrm")
def test_winrm(body: WinrmTestInput, _=Depends(require_role("admin"))):
    """Testa conexão WinRM com um host específico."""
    try:
        import winrm
    except ImportError:
        raise HTTPException(status_code=500, detail="Biblioteca pywinrm não instalada no backend")

    try:
        endpoint = f"{body.scheme}://{body.hostname}:{body.port}"
        session = winrm.Session(
            endpoint,
            auth=(body.username, body.password),
            transport="ntlm",
            server_cert_validation="ignore",
        )

        # Executa comando simples
        result = session.run_ps("Write-Output 'OK'")
        stdout = result.std_out.decode("utf-8", errors="replace").strip()

        return {
            "success": True,
            "message": f"WinRM OK — resposta: {stdout}",
            "hostname": body.hostname,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Falha WinRM: {str(e)[:200]}",
            "hostname": body.hostname,
        }
