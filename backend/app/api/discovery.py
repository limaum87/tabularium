import json
import socket
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed
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


# ============================================================
# Network Discovery (Ping Sweep + SSH Banner) — Pure Python
# ============================================================

NET_SSH_PORT = 22
NET_DEFAULT_TIMEOUT = 2
NET_MAX_WORKERS = 100


def _net_grab_ssh_banner(ip: str, port: int = NET_SSH_PORT, timeout: float = NET_DEFAULT_TIMEOUT):
    """Tenta conectar na porta SSH e ler o banner. Retorna banner ou None."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        banner = sock.recv(256).decode("utf-8", errors="ignore").strip()
        sock.close()
        if banner.startswith("SSH-") or banner:
            return banner
        return None
    except (socket.timeout, socket.error, ConnectionRefusedError, OSError):
        return None


def _net_resolve_hostname(ip: str):
    """Tenta DNS reverso para descobrir o hostname do IP."""
    try:
        hostname, _, _ = socket.gethostbyaddr(ip)
        return hostname.split(".")[0].upper()
    except (socket.herror, socket.gaierror, OSError):
        return None


def _net_scan_ip(ip: str, port: int = NET_SSH_PORT, timeout: float = NET_DEFAULT_TIMEOUT):
    """Escaneia um único IP: tenta conectar na porta SSH e ler o banner."""
    banner = _net_grab_ssh_banner(ip, port, timeout)
    if banner:
        hostname = _net_resolve_hostname(ip)
        return {"ip": ip, "hostname": hostname, "ssh_banner": banner, "port": port}
    return None


@router.post("/network-run")
def run_network_discovery(db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    """Executa network scan (ping sweep + SSH banner) e retorna hosts Linux encontrados."""
    # Lê configs do banco
    settings = {}
    rows = db.query(Setting).all()
    for r in rows:
        settings[f"{r.category}.{r.key}"] = r.value

    subnets_raw = settings.get("network.subnets", "[]")
    ssh_port = int(settings.get("network.ssh_port", str(NET_SSH_PORT)))
    timeout = float(settings.get("network.timeout", str(NET_DEFAULT_TIMEOUT)))
    max_workers = int(settings.get("network.max_workers", str(NET_MAX_WORKERS)))
    exclude_raw = settings.get("network.exclude_ips", "[]")

    try:
        subnets = json.loads(subnets_raw) if subnets_raw else []
    except Exception:
        subnets = []

    try:
        exclude_ips = set(json.loads(exclude_raw)) if exclude_raw else set()
    except Exception:
        exclude_ips = set()

    if not subnets:
        raise HTTPException(status_code=400, detail="Configure as sub-redes em Configurações → Rede antes de executar o network discovery")

    # Gera lista de IPs
    all_ips = []
    for subnet in subnets:
        try:
            network = ipaddress.ip_network(subnet.strip(), strict=False)
            for ip in network.hosts():
                ip_str = str(ip)
                if ip_str not in exclude_ips:
                    all_ips.append(ip_str)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Sub-rede inválida '{subnet}': {e}")

    if not all_ips:
        raise HTTPException(status_code=400, detail="Nenhum IP para escanear. Verifique as sub-redes configuradas.")

    # Scan paralelo
    found = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_net_scan_ip, ip, ssh_port, timeout): ip for ip in all_ips}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    found.append(result)
            except Exception:
                pass

    # Ordena por IP
    found.sort(key=lambda x: tuple(int(p) for p in x["ip"].split(".")))

    # Verifica quais já existem no banco (por hostname ou IP)
    existing_hostnames = {h.hostname.upper() for h in db.query(Host).all()}

    # Busca também IPs já conhecidos via HostNetwork
    from app.core.database import HostNetwork
    existing_ips = set()
    net_rows = db.query(HostNetwork.ip).filter(HostNetwork.ip.isnot(None)).all()
    for r in net_rows:
        if r.ip:
            existing_ips.add(r.ip)

    result = []
    for h in found:
        hostname = h.get("hostname") or ""
        ip = h["ip"]
        in_db = hostname.upper() in existing_hostnames or ip in existing_ips
        result.append({
            "ip": ip,
            "hostname": hostname,
            "ssh_banner": h["ssh_banner"],
            "port": h["port"],
            "in_database": in_db,
            "status": "existing" if in_db else "new",
        })

    # Activity log
    log_activity(db,
        activity_type="discovery_run",
        status="success",
        message=f"Network Discovery: {len(result)} hosts com SSH ({sum(1 for h in result if h['status'] == 'new')} novos)",
        details={"total": len(result), "new": sum(1 for h in result if h["status"] == "new"), "existing": sum(1 for h in result if h["status"] == "existing"), "subnets": subnets, "scanned": len(all_ips)},
        source="manual",
    )
    db.commit()

    return {
        "total": len(result),
        "new_count": sum(1 for h in result if h["status"] == "new"),
        "existing_count": sum(1 for h in result if h["status"] == "existing"),
        "scanned": len(all_ips),
        "hosts": result,
    }


@router.post("/network-import")
def import_network_hosts(body: dict, db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    """Importa hosts encontrados via network discovery para o banco.

    body: {"hosts": [{"ip": "192.168.1.50", "hostname": "WEB01", "ssh_banner": "...", "so_type": "linux"}]}
    """
    hosts_data = body.get("hosts", [])
    if not hosts_data:
        raise HTTPException(status_code=400, detail="Lista de hosts vazia")

    now = datetime.utcnow()
    imported = 0
    skipped = 0

    for host_info in hosts_data:
        hostname = (host_info.get("hostname") or host_info.get("ip") or "").strip().upper()
        ip = host_info.get("ip", "").strip()

        if not hostname:
            continue

        existing = db.query(Host).filter(Host.hostname == hostname).first()
        if existing:
            skipped += 1
            continue

        host = Host(
            hostname=hostname,
            status="unknown",
            so_type="linux",
            last_seen=None,
        )
        db.add(host)
        db.flush()  # Pega o ID

        # Salva IP na tabela HostNetwork
        if ip:
            from app.core.database import HostNetwork
            net = HostNetwork(host_id=host.id, ip=ip, updated_at=now)
            db.add(net)

        imported += 1

    db.commit()

    # Activity log
    log_activity(db,
        activity_type="discovery_import",
        status="success",
        message=f"Network Import: {imported} novos, {skipped} já existiam",
        details={"imported": imported, "skipped": skipped},
        source="manual",
    )
    db.commit()

    return {
        "detail": f"{imported} importados, {skipped} já existiam",
        "imported": imported,
        "skipped": skipped,
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
