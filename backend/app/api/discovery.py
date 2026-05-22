import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.database import get_db, Host, ScanHistory, Setting
from app.core.security import require_role
from app.api.activity import log_activity

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


@router.post("/run")
def run_discovery(db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    """Executa LDAP scan e retorna máquinas encontradas no AD."""
    # Lê configs do banco
    settings = {}
    rows = db.query(Setting).all()
    for r in rows:
        settings[f"{r.category}.{r.key}"] = r.value

    server = settings.get("ad.server", "")
    bind_dn = settings.get("ad.bind_dn", "")
    password = settings.get("ad.password", "")
    base_dn = settings.get("ad.base_dn", "")
    search_filter = settings.get("ad.search_filter", "(objectClass=computer)")
    ou_list_raw = settings.get("ad.ou_list", "[]")

    try:
        ou_list = json.loads(ou_list_raw) if ou_list_raw else []
    except Exception:
        ou_list = []

    if not all([server, bind_dn, password, base_dn]):
        raise HTTPException(status_code=400, detail="Configure o AD em Configurações antes de executar o discovery")

    # Faz LDAP scan
    try:
        from ldap3 import Server, Connection, ALL, SUBTREE
    except ImportError:
        raise HTTPException(status_code=500, detail="Biblioteca ldap3 não instalada")

    try:
        srv = Server(server, get_info=ALL, connect_timeout=10)
        conn = Connection(srv, user=bind_dn, password=password, auto_bind=True, read_only=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Falha ao conectar ao AD: {str(e)[:200]}")

    # Busca computadores
    search_bases = ou_list if ou_list else [base_dn]
    ad_hosts = []

    for base in search_bases:
        try:
            conn.search(
                search_base=base,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=["cn", "dNSHostName", "name", "operatingSystem", "lastLogonTimeStamp"],
            )
            for entry in conn.entries:
                hostname = ""
                if hasattr(entry, "dNSHostName") and entry.dNSHostName.value:
                    hostname = entry.dNSHostName.value.split(".")[0]
                elif hasattr(entry, "cn") and entry.cn.value:
                    hostname = entry.cn.value
                elif hasattr(entry, "name") and entry.name.value:
                    hostname = entry.name.value

                if hostname:
                    ad_hosts.append({
                        "hostname": hostname.upper(),
                        "os": getattr(entry, "operatingSystem", None) and entry.operatingSystem.value or "",
                        "dn": entry.entry_dn,
                    })
        except Exception:
            pass

    conn.unbind()

    # Deduplica
    seen = set()
    unique = []
    for h in ad_hosts:
        if h["hostname"] not in seen:
            seen.add(h["hostname"])
            unique.append(h)

    # Verifica quais já existem no banco
    existing = {h.hostname.upper() for h in db.query(Host).all()}

    result = []
    for h in unique:
        in_db = h["hostname"] in existing
        result.append({
            "hostname": h["hostname"],
            "os": h["os"],
            "dn": h["dn"],
            "in_database": in_db,
            "status": "existing" if in_db else "new",
        })

    # Activity log
    log_activity(db,
        activity_type="discovery_run",
        status="success",
        message=f"Discovery: {len(result)} máquinas ({sum(1 for h in result if h['status'] == 'new')} novas)",
        details={"total": len(result), "new": sum(1 for h in result if h["status"] == "new"), "existing": sum(1 for h in result if h["status"] == "existing")},
        source="manual",
    )
    db.commit()

    return {
        "total": len(result),
        "new_count": sum(1 for h in result if h["status"] == "new"),
        "existing_count": sum(1 for h in result if h["status"] == "existing"),
        "hosts": result,
    }


@router.post("/import")
def import_hosts(body: dict, db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    """Importa uma lista de hostnames para o banco (status discovered)."""
    hostnames = body.get("hostnames", [])
    if not hostnames:
        raise HTTPException(status_code=400, detail="Lista de hostnames vazia")

    now = datetime.utcnow()
    imported = 0
    skipped = 0

    for hostname in hostnames:
        hn = hostname.strip().upper()
        if not hn:
            continue
        existing = db.query(Host).filter(Host.hostname == hn).first()
        if existing:
            skipped += 1
            continue
        host = Host(hostname=hn, status="unknown", last_seen=None)
        db.add(host)
        imported += 1

    db.commit()

    # Activity log
    log_activity(db,
        activity_type="discovery_import",
        status="success",
        message=f"Importação: {imported} novos, {skipped} já existiam",
        details={"imported": imported, "skipped": skipped, "hostnames": hostnames[:20]},
        source="manual",
    )
    db.commit()

    return {
        "detail": f"{imported} importados, {skipped} já existiam",
        "imported": imported,
        "skipped": skipped,
    }
