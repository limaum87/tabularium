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


class DnsApplyInput(BaseModel):
    dns_servers: str  # IPs separados por vírgula, ex: "10.0.0.1,10.0.0.2"
    search_domain: str = ""  # ex: "empresa.local"


class DnsTestInput(BaseModel):
    hostname: str  # nome para testar resolução, ex: "wir-adm-01"


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


@router.post("/apply-dns")
def apply_dns(body: DnsApplyInput, _=Depends(require_role("admin"))):
    """Aplica DNS customizado no /etc/resolv.conf do container."""
    import os

    dns_ips = [ip.strip() for ip in body.dns_servers.split(",") if ip.strip()]
    if not dns_ips:
        return {"success": False, "message": "Nenhum DNS válido informado"}

    # Valida formato IP básico
    import re
    ip_pattern = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,}$")
    for ip in dns_ips:
        if not ip_pattern.match(ip):
            return {"success": False, "message": f"IP inválido: {ip}"}

    resolv_path = "/etc/resolv.conf"

    # Monta o conteúdo do resolv.conf
    lines = []

    # Search domain
    search = body.search_domain.strip()
    if search:
        lines.append(f"search {search}")

    # Adiciona os nameservers
    for ip in dns_ips:
        lines.append(f"nameserver {ip}")

    try:
        # Se for symlink para arquivo read-only, remove e recria como arquivo normal
        if os.path.islink(resolv_path):
            os.unlink(resolv_path)

        with open(resolv_path, "w") as f:
            f.write("\n".join(lines) + "\n")

        # Força flush do resolver do glibc
        try:
            with open("/proc/net/pnp", "r"):
                pass
        except Exception:
            pass

        return {
            "success": True,
            "message": f"DNS aplicado: {', '.join(dns_ips)}" + (f" | Search: {search}" if search else ""),
            "dns_servers": dns_ips,
            "search_domain": search,
        }
    except PermissionError:
        return {
            "success": False,
            "message": "Sem permissão para escrever /etc/resolv.conf. Execute o container como root.",
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Erro ao aplicar DNS: {str(e)[:200]}",
        }


@router.post("/test-dns")
def test_dns(body: DnsTestInput, _=Depends(require_role("admin"))):
    """Testa resolução DNS de um hostname dentro do container."""
    import socket

    hostname = body.hostname.strip()
    if not hostname:
        return {"success": False, "message": "Hostname vazio"}

    # Mostra o resolv.conf atual
    resolv_content = ""
    try:
        with open("/etc/resolv.conf", "r") as f:
            resolv_content = f.read().strip()
    except Exception:
        pass

    try:
        results = socket.getaddrinfo(hostname, None)
        ips = list(set(addr[4][0] for addr in results))
        return {
            "success": True,
            "message": f"{hostname} resolveu para: {', '.join(ips)}",
            "hostname": hostname,
            "ips": ips,
            "resolv_conf": resolv_content,
        }
    except socket.gaierror as e:
        return {
            "success": False,
            "message": f"Falha ao resolver '{hostname}': {str(e)}",
            "hostname": hostname,
            "ips": [],
            "resolv_conf": resolv_content,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Erro: {str(e)[:200]}",
            "hostname": hostname,
            "ips": [],
            "resolv_conf": resolv_content,
        }


@router.delete("/{key}")
def delete_setting(key: str, db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    """Remove uma configuração."""
    s = db.query(Setting).filter(Setting.key == key).first()
    if not s:
        raise HTTPException(status_code=404, detail="Configuração não encontrada")
    db.delete(s)
    db.commit()
    return {"detail": "Configuração removida"}
