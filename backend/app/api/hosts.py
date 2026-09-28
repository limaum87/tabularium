from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import (
    get_db, Host, HostHardware, HostDisk, HostNetwork,
    HostLicense, HostSoftware, HostRemoteAccess, ScanHistory, HostDistro,
    HostPatch, HostPendingUpdate, HostPatchStatus,
)
from app.core.security import get_current_user
from app.schemas.schemas import CheckinPayload, HostResponse
from app.api.activity import log_activity

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


# ---- Constantes ----
LEGACY_DAYS = 90  # Dias sem contato para considerar legado


def _auto_legacy_check(db: Session):
    """Marca como legado hosts sem contato há 90+ dias."""
    threshold = datetime.utcnow() - timedelta(days=LEGACY_DAYS)
    candidates = db.query(Host).filter(
        Host.is_legacy == False,
        Host.last_seen != None,
        Host.last_seen < threshold,
    ).all()
    now = datetime.utcnow()
    for host in candidates:
        host.is_legacy = True
        host.legacy_since = now
        host.updated_at = now
    if candidates:
        db.commit()


# ---- Checkin (collector) ----

@router.post("/checkin")
def checkin(body: CheckinPayload, db: Session = Depends(get_db)):
    """Recebe dados do collector e atualiza o banco."""
    # Busca ou cria host
    host = db.query(Host).filter(Host.hostname == body.hostname).first()
    now = datetime.utcnow()

    # ---- Determina status (conectividade) e so_type (tipo de SO) ----
    # Regras de mapeamento a partir do que o collector envia:
    #   - body.so_type presente        → usar diretamente
    #   - body.hardware presente        → Windows confirmado (status=online, so_type=windows)
    #   - body.status == "linux"        → Linux detectado via SSH (status=online, so_type=linux)
    #   - body.status == "offline"      → offline, so_type mantém anterior
    #   - body.status == "winrm_unavailable" → online mas SO desconhecido
    #   - padrão                         → online, so_type mantém anterior

    if body.so_type:
        effective_so_type = body.so_type
    elif body.hardware:
        effective_so_type = "windows"
    elif body.status == "linux":
        effective_so_type = "linux"
    else:
        effective_so_type = None  # mantém o que já existe

    if body.hardware:
        effective_status = "online"
    elif body.status == "linux":
        effective_status = "online"  # está online (SSH respondeu)
    elif body.status == "winrm_unavailable":
        effective_status = "unknown"
    elif body.status == "offline":
        effective_status = "offline"
    else:
        effective_status = body.status if body.status else "online"

    if host:
        host.domain = body.domain or host.domain
        host.status = effective_status
        host.last_seen = now
        host.updated_at = now
        # Atualiza so_type se detectado
        if effective_so_type:
            host.so_type = effective_so_type
        # Se era legado, restaurar automaticamente ao fazer checkin
        if host.is_legacy:
            host.is_legacy = False
            host.legacy_since = None
    else:
        host = Host(
            hostname=body.hostname,
            domain=body.domain,
            status=effective_status,
            so_type=effective_so_type or "unknown",
            last_seen=now,
        )
        db.add(host)
        db.flush()

    host_id = host.id

    # Hardware
    if body.hardware:
        hw = body.hardware
        existing = db.query(HostHardware).filter(HostHardware.host_id == host_id).first()
        data = {
            "manufacturer": hw.manufacturer,
            "model": hw.model,
            "serial": hw.serial,
            "cpu": hw.cpu,
            "ram_gb": hw.ram_gb,
            "bios_version": hw.bios_version,
            "last_boot": hw.last_boot,
            "last_user": hw.last_user,
            "updated_at": now,
        }
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
        else:
            db.add(HostHardware(host_id=host_id, **data))

    # Distro (Linux)
    if body.distro:
        distro_data = body.distro
        existing_distro = db.query(HostDistro).filter(HostDistro.host_id == host_id).first()
        d_data = {
            "name": distro_data.get("name"),
            "version": distro_data.get("version"),
            "distro_id": distro_data.get("id"),
            "id_like": distro_data.get("id_like"),
            "pretty_name": distro_data.get("pretty_name"),
            "kernel": distro_data.get("kernel"),
            "arch": distro_data.get("arch"),
            "updated_at": now,
        }
        if existing_distro:
            for k, v in d_data.items():
                setattr(existing_distro, k, v)
        else:
            db.add(HostDistro(host_id=host_id, **d_data))

    # Discos (apaga e recria)
    if body.disks is not None:
        db.query(HostDisk).filter(HostDisk.host_id == host_id).delete()
        for d in body.disks:
            db.add(HostDisk(
                host_id=host_id,
                drive=d.drive,
                total_gb=d.total_gb,
                free_gb=d.free_gb,
                filesystem=d.filesystem,
                updated_at=now,
            ))

    # Rede (apaga e recria)
    if body.network is not None:
        db.query(HostNetwork).filter(HostNetwork.host_id == host_id).delete()
        for n in body.network:
            db.add(HostNetwork(
                host_id=host_id,
                ip=n.ip,
                mac=n.mac,
                gateway=n.gateway,
                dns=n.dns,
                adapter_name=n.adapter_name,
                updated_at=now,
            ))

    # Licenças (apaga e recria)
    if body.licenses is not None:
        db.query(HostLicense).filter(HostLicense.host_id == host_id).delete()
        for lic in body.licenses:
            db.add(HostLicense(
                host_id=host_id,
                product=lic.product,
                edition=lic.edition,
                version=lic.version,
                channel=lic.channel,
                license_status=lic.license_status,
                partial_product_key=lic.partial_product_key,
                oem_key_found=lic.oem_key_found,
                click_to_run=lic.click_to_run,
                detection_method=lic.detection_method,
                updated_at=now,
            ))

    # Software (apaga e recria)
    if body.software is not None:
        db.query(HostSoftware).filter(HostSoftware.host_id == host_id).delete()
        for sw in body.software:
            db.add(HostSoftware(
                host_id=host_id,
                name=sw.name,
                version=sw.version,
                publisher=sw.publisher,
                install_date=sw.install_date,
                install_location=sw.install_location,
                updated_at=now,
            ))

    # Patches — status geral (1 linha por host)
    if body.patch_status is not None:
        ps_data = body.patch_status or {}
        pending_count = len(body.pending_updates) if body.pending_updates else 0
        critical = sum(
            1 for p in (body.pending_updates or [])
            if (p.severity or "").lower() == "critical"
        )
        existing_ps = db.query(HostPatchStatus).filter(HostPatchStatus.host_id == host_id).first()
        ps_values = {
            "os_edition": ps_data.get("os_edition"),
            "display_version": ps_data.get("display_version"),
            "build": ps_data.get("build"),
            "wu_last_success": ps_data.get("wu_last_success"),
            "pending_count": pending_count,
            "critical_pending": critical,
            "last_error": ps_data.get("last_error"),
            "last_scan": now,
            "updated_at": now,
        }
        if existing_ps:
            for k, v in ps_values.items():
                setattr(existing_ps, k, v)
        else:
            db.add(HostPatchStatus(host_id=host_id, **ps_values))

    # KBs instalados (apaga e recria)
    if body.patches is not None:
        db.query(HostPatch).filter(HostPatch.host_id == host_id).delete()
        for p in body.patches:
            db.add(HostPatch(
                host_id=host_id,
                kb=p.kb,
                description=p.description,
                installed_on=p.installed_on,
                updated_at=now,
            ))

    # Updates pendentes (apaga e recria)
    if body.pending_updates is not None:
        db.query(HostPendingUpdate).filter(HostPendingUpdate.host_id == host_id).delete()
        for p in body.pending_updates:
            db.add(HostPendingUpdate(
                host_id=host_id,
                kb=p.kb,
                title=p.title,
                severity=p.severity,
                reboot_required=p.reboot or False,
                updated_at=now,
            ))

    # Scan history
    db.add(ScanHistory(
        host_id=host_id,
        hostname=body.hostname,
        status="success",
        started_at=now,
        finished_at=now,
    ))

    # Activity log
    log_activity(db,
        activity_type="collector_checkin",
        hostname=body.hostname,
        host_id=host_id,
        status="success",
        message=f"Coleta automática: {body.hostname}",
        source="collector",
    )

    db.commit()
    return {"detail": "Check-in recebido", "host_id": host_id}


# ---- Leitura ----

@router.get("", response_model=list[HostResponse])
def list_hosts(db: Session = Depends(get_db), _=Depends(get_current_user)):
    _auto_legacy_check(db)
    return db.query(Host).filter(Host.is_legacy == False).order_by(Host.hostname).all()


# ---- Legados ----

@router.get("/legacy", response_model=list[HostResponse])
def list_legacy(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Lista hosts legados (90+ dias sem contato)."""
    _auto_legacy_check(db)
    return (
        db.query(Host)
        .filter(Host.is_legacy == True)
        .order_by(Host.last_seen.asc())
        .all()
    )


@router.post("/{host_id}/restore")
def restore_host(host_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Restaura um host legado para ativo."""
    host = db.query(Host).filter(Host.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host não encontrado")
    if not host.is_legacy:
        raise HTTPException(status_code=400, detail="Host não é legado")
    host.is_legacy = False
    host.legacy_since = None
    host.last_seen = datetime.utcnow()  # Reseta para evitar re-marcação imediata
    host.updated_at = datetime.utcnow()
    db.commit()
    return {"detail": f"Host {host.hostname} restaurado para ativo"}


@router.post("/{host_id}/move-to-legacy")
def move_to_legacy(host_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Move manualmente um host ativo para legados."""
    host = db.query(Host).filter(Host.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host não encontrado")
    if host.is_legacy:
        raise HTTPException(status_code=400, detail="Host já é legado")
    host.is_legacy = True
    host.legacy_since = datetime.utcnow()
    host.updated_at = datetime.utcnow()
    db.commit()
    return {"detail": f"Host {host.hostname} movido para legados"}


@router.post("/ping-sweep")
def ping_sweep(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Força ping em todos os hosts ativos e retorna resultado."""
    import subprocess

    active_hosts = db.query(Host).filter(Host.is_legacy == False).all()
    if not active_hosts:
        return {"detail": "Nenhum host ativo", "results": []}

    now = datetime.utcnow()
    results = []
    online = 0
    offline = 0

    for host in active_hosts:
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "2", host.hostname],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                host.ping_status = "online"
                online += 1
                results.append({"hostname": host.hostname, "ping_status": "online"})
            else:
                host.ping_status = "offline"
                offline += 1
                results.append({"hostname": host.hostname, "ping_status": "offline"})
        except Exception:
            host.ping_status = "offline"
            offline += 1
            results.append({"hostname": host.hostname, "ping_status": "offline"})
        host.last_ping = now

    db.commit()

    # Activity log
    log_activity(db,
        activity_type="ping_sweep",
        status="success",
        message=f"Ping sweep: {online} online, {offline} offline de {len(active_hosts)} hosts",
        details={"online": online, "offline": offline, "total": len(active_hosts)},
        source="manual",
    )
    db.commit()

    return {
        "detail": f"Ping sweep concluído: {online} online, {offline} offline",
        "total": len(active_hosts),
        "online": online,
        "offline": offline,
        "results": results,
    }


@router.get("/logged-users")
def logged_users(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Retorna todos os usuários coletados e a última máquina em que logaram."""
    from sqlalchemy import func

    # Busca todos os registros de hardware que possuem last_user
    # Para cada last_user distinto, pega o registro mais recente
    subquery = (
        db.query(
            HostHardware.last_user,
            func.max(HostHardware.updated_at).label("max_updated")
        )
        .filter(HostHardware.last_user != None, HostHardware.last_user != "")
        .group_by(HostHardware.last_user)
        .subquery()
    )

    # Join com hardware para pegar o host_id e com Host para pegar o hostname
    results = (
        db.query(
            HostHardware.last_user,
            HostHardware.updated_at,
            Host.hostname,
            Host.id,
            HostHardware.cpu,
            HostHardware.ram_gb,
        )
        .join(subquery, (HostHardware.last_user == subquery.c.last_user) & (HostHardware.updated_at == subquery.c.max_updated))
        .join(Host, HostHardware.host_id == Host.id)
        .order_by(HostHardware.last_user)
        .all()
    )

    # Deduplica por last_user (caso haja empate no updated_at)
    seen = set()
    users = []
    for row in results:
        user_key = row.last_user.upper() if row.last_user else None
        if user_key and user_key not in seen:
            seen.add(user_key)
            users.append({
                "last_user": row.last_user,
                "hostname": row.hostname,
                "host_id": row.id,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "cpu": row.cpu,
                "ram_gb": row.ram_gb,
            })

    return {"users": users, "total": len(users)}


@router.get("/{host_id}/action/collect-stream")
def action_collect_stream(host_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Coleta manual via SSE — detecta so_type e usa WinRM (Windows) ou SSH (Linux)."""
    host = db.query(Host).filter(Host.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host não encontrado")

    # Detecta método de coleta pelo so_type
    is_linux = host.so_type == "linux"

    if is_linux:
        return _collect_stream_linux(host_id, host, db)
    else:
        return _collect_stream_winrm(host_id, host, db)


def _collect_stream_linux(host_id: int, host, db: Session):
    """Coleta manual via SSH (Linux) com SSE."""
    import json as json_mod
    from app.collector.ssh_collect import (
        get_ssh_settings, make_fqdn, ssh_connect, ssh_run,
        bash_hostname, bash_hardware, bash_disks, bash_network, bash_distro_fixed,
        bash_software, safe_json_parse,
    )

    cfg = get_ssh_settings(db)
    if not cfg:
        raise HTTPException(status_code=400, detail="Credenciais SSH não configuradas. Configure em Configurações → SSH.")

    host_id_val = host.id

    def sse(data):
        return f"data: {json_mod.dumps(data, ensure_ascii=False)}\n\n"

    def event_stream():
        yield sse({"type": "start", "hostname": host.hostname})

        # Conecta SSH
        target = make_fqdn(host.hostname, cfg.get("search", ""))
        try:
            yield sse({"type": "step", "step": "connect", "message": f"Conectando SSH em {target}..."})
            client = ssh_connect(target, cfg)
            # Teste rápido
            uname = ssh_run(client, "uname -s", timeout=10)
            if not uname:
                raise RuntimeError("SSH não respondeu ao teste")
            yield sse({"type": "step_ok", "step": "connect", "message": f"Conectado em {target} ({uname})"})
        except Exception as e:
            yield sse({"type": "error", "step": "connect", "message": f"Falha na conexão SSH: {str(e)[:200]}"})
            yield sse({"type": "done", "success": False, "message": "Falha na conexão SSH"})
            return

        # ---- HOSTNAME ----
        # Resolve o hostname real do host Linux e atualiza no banco se diferente
        resolved_hostname = None
        yield sse({"type": "step", "step": "hostname", "message": "🔍 Resolvendo hostname..."})
        try:
            raw = ssh_run(client, bash_hostname(), timeout=10)
            hn_data = safe_json_parse(raw, "hostname")
            if hn_data and hn_data.get("hostname"):
                resolved_hostname = hn_data["hostname"].strip().upper()
                fqdn = hn_data.get("fqdn", "").strip()
                if resolved_hostname and resolved_hostname != host.hostname.upper():
                    # Verifica se o novo hostname já não existe no banco
                    existing = db.query(Host).filter(Host.hostname == resolved_hostname).first()
                    if not existing:
                        old_name = host.hostname
                        host_obj = db.query(Host).filter(Host.id == host_id_val).first()
                        host_obj.hostname = resolved_hostname
                        if fqdn and fqdn.upper() != resolved_hostname:
                            host_obj.domain = fqdn.split(".", 1)[1] if "." in fqdn else None
                        db.commit()
                        yield sse({"type": "step_ok", "step": "hostname", "message": f"✓ Hostname atualizado: {old_name} → {resolved_hostname}"})
                    else:
                        yield sse({"type": "step_warn", "step": "hostname", "message": f"⚠ Hostname resolvido '{resolved_hostname}' já existe no banco (ID {existing.id})"})
                else:
                    yield sse({"type": "step_ok", "step": "hostname", "message": f"✓ Hostname OK: {resolved_hostname}"})
            else:
                yield sse({"type": "step_warn", "step": "hostname", "message": "⚠ Não foi possível resolver o hostname"})
        except Exception as e:
            db.rollback()
            yield sse({"type": "step_warn", "step": "hostname", "message": f"⚠ Falha ao resolver hostname: {str(e)[:80]}"})

        # Atualiza status
        host_obj = db.query(Host).filter(Host.id == host_id_val).first()
        host_obj.status = "online"
        host_obj.so_type = "linux"
        host_obj.last_seen = datetime.utcnow()
        host_obj.updated_at = datetime.utcnow()
        if host_obj.is_legacy:
            host_obj.is_legacy = False
            host_obj.legacy_since = None
        db.commit()

        now = datetime.utcnow()
        total_success = 0
        total_fail = 0

        # ---- DISTRO ----
        yield sse({"type": "step", "step": "distro", "message": "🐧 Coletando distribuição..."})
        try:
            raw = ssh_run(client, bash_distro_fixed(), timeout=15)
            distro = safe_json_parse(raw, "distro")
            if distro:
                existing_d = db.query(HostDistro).filter(HostDistro.host_id == host_id_val).first()
                d_data = {
                    "name": distro.get("name"),
                    "version": distro.get("version"),
                    "distro_id": distro.get("distro_id"),
                    "id_like": distro.get("id_like"),
                    "pretty_name": distro.get("pretty_name"),
                    "kernel": distro.get("kernel"),
                    "arch": distro.get("arch"),
                    "updated_at": now,
                }
                if existing_d:
                    for k, v in d_data.items():
                        setattr(existing_d, k, v)
                else:
                    db.add(HostDistro(host_id=host_id_val, **d_data))
                db.commit()
                pretty = distro.get("pretty_name") or distro.get("name") or "?"
                yield sse({"type": "step_ok", "step": "distro", "message": f"✓ {pretty} ({distro.get('arch','?')})"})
                total_success += 1
            else:
                yield sse({"type": "step_warn", "step": "distro", "message": "⚠ Distro retornou vazio"})
        except Exception as e:
            db.rollback()
            yield sse({"type": "step_fail", "step": "distro", "message": f"✗ Falha: {str(e)[:100]}"})
            total_fail += 1

        # ---- HARDWARE ----
        yield sse({"type": "step", "step": "hardware", "message": "📡 Coletando hardware..."})
        try:
            raw = ssh_run(client, bash_hardware(), timeout=15)
            hw = safe_json_parse(raw, "hardware")
            if hw:
                existing = db.query(HostHardware).filter(HostHardware.host_id == host_id_val).first()
                hw_data = {
                    "manufacturer": hw.get("manufacturer"),
                    "model": hw.get("model"),
                    "serial": hw.get("serial"),
                    "cpu": hw.get("cpu"),
                    "ram_gb": hw.get("ram_gb"),
                    "bios_version": hw.get("bios_version"),
                    "last_boot": hw.get("last_boot"),
                    "last_user": hw.get("last_user"),
                    "updated_at": now,
                }
                if existing:
                    for k, v in hw_data.items():
                        setattr(existing, k, v)
                else:
                    db.add(HostHardware(host_id=host_id_val, **hw_data))
                db.commit()
                cpu = hw.get("cpu", "?")[:40]
                ram = hw.get("ram_gb", "?")
                yield sse({"type": "step_ok", "step": "hardware", "message": f"✓ CPU: {cpu} | RAM: {ram} GB"})
                total_success += 1
            else:
                yield sse({"type": "step_warn", "step": "hardware", "message": "⚠ Hardware retornou vazio"})
        except Exception as e:
            db.rollback()
            yield sse({"type": "step_fail", "step": "hardware", "message": f"✗ Falha: {str(e)[:100]}"})
            total_fail += 1

        # ---- DISCOS ----
        yield sse({"type": "step", "step": "disks", "message": "💾 Coletando discos..."})
        try:
            raw = ssh_run(client, bash_disks(), timeout=15)
            parsed = safe_json_parse(raw, "disks")
            disks = parsed if isinstance(parsed, list) else [parsed] if parsed else []
            db.query(HostDisk).filter(HostDisk.host_id == host_id_val).delete()
            for d in disks:
                db.add(HostDisk(host_id=host_id_val, drive=d.get("drive"), total_gb=d.get("total_gb"), free_gb=d.get("free_gb"), filesystem=d.get("filesystem"), updated_at=now))
            db.commit()
            drives = ", ".join(d.get("drive", "?") for d in disks)
            yield sse({"type": "step_ok", "step": "disks", "message": f"✓ {len(disks)} disco(s): {drives}"})
            total_success += 1
        except Exception as e:
            db.rollback()
            yield sse({"type": "step_fail", "step": "disks", "message": f"✗ Falha: {str(e)[:100]}"})
            total_fail += 1

        # ---- REDE ----
        yield sse({"type": "step", "step": "network", "message": "🌐 Coletando rede..."})
        try:
            raw = ssh_run(client, bash_network(), timeout=15)
            parsed = safe_json_parse(raw, "network")
            network = parsed if isinstance(parsed, list) else [parsed] if parsed else []
            db.query(HostNetwork).filter(HostNetwork.host_id == host_id_val).delete()
            for n in network:
                db.add(HostNetwork(host_id=host_id_val, ip=n.get("ip"), mac=n.get("mac"), gateway=n.get("gateway"), dns=n.get("dns"), adapter_name=n.get("adapter_name"), updated_at=now))
            db.commit()
            ips = ", ".join(n.get("ip", "?") for n in network if n.get("ip"))
            yield sse({"type": "step_ok", "step": "network", "message": f"✓ {len(network)} adaptador(es): {ips}"})
            total_success += 1
        except Exception as e:
            db.rollback()
            yield sse({"type": "step_fail", "step": "network", "message": f"✗ Falha: {str(e)[:100]}"})
            total_fail += 1

        # ---- SOFTWARE ----
        yield sse({"type": "step", "step": "software", "message": "📦 Coletando softwares (pode demorar)..."})
        try:
            raw = ssh_run(client, bash_software(), timeout=60)
            parsed = safe_json_parse(raw, "software")
            software = parsed if isinstance(parsed, list) else [parsed] if parsed else []
            db.query(HostSoftware).filter(HostSoftware.host_id == host_id_val).delete()
            for sw in software:
                db.add(HostSoftware(host_id=host_id_val, name=sw.get("name"), version=sw.get("version"), publisher=sw.get("publisher"), install_date=sw.get("install_date"), install_location=sw.get("install_location"), updated_at=now))
            db.commit()
            yield sse({"type": "step_ok", "step": "software", "message": f"✓ {len(software)} software(s) instalados"})
            total_success += 1
        except Exception as e:
            db.rollback()
            yield sse({"type": "step_fail", "step": "software", "message": f"✗ Falha: {str(e)[:100]}"})
            total_fail += 1

        # ---- DONE ----
        client.close()

        db.add(ScanHistory(host_id=host_id_val, hostname=host.hostname, status="success", started_at=now, finished_at=datetime.utcnow()))
        log_activity(db,
            activity_type="manual_collect",
            hostname=host.hostname,
            host_id=host_id_val,
            status="success" if total_fail == 0 else "partial",
            message=f"Coleta SSH manual: {total_success} OK, {total_fail} falha(s)",
            details={"success": total_success, "fail": total_fail, "method": "ssh"},
            source="manual",
        )
        db.commit()

        yield sse({
            "type": "done",
            "success": total_fail == 0,
            "message": f"Coleta concluída: {total_success} OK, {total_fail} falha(s)",
            "stats": {"success": total_success, "fail": total_fail},
        })

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _collect_stream_winrm(host_id: int, host, db: Session):
    """Coleta manual via WinRM (Windows) com SSE."""
    import json as json_mod
    import winrm
    from app.collector.winrm_collect import _get_settings, _make_fqdn, _connect, _run_ps, _run_ps_with_timeout, _safe_json
    from app.collector.winrm_collect import (
        _ps_hardware, _ps_disks, _ps_network,
        _ps_windows_license, _ps_office_license, _ps_software,
        _ps_anydesk, _ps_ultravnc,
    )

    cfg = _get_settings(db)
    if not cfg:
        raise HTTPException(status_code=400, detail="Credenciais WinRM não configuradas. Configure em Configurações → WinRM.")

    host_id_val = host.id

    def sse(data):
        return f"data: {json_mod.dumps(data, ensure_ascii=False)}\n\n"

    def event_stream():
        yield sse({"type": "start", "hostname": host.hostname})

        # Conecta WinRM
        target = _make_fqdn(host.hostname, cfg.get("search", ""))
        try:
            yield sse({"type": "step", "step": "connect", "message": f"Conectando em {target}..."})
            session = _connect(target, cfg, operation_timeout_sec=15, read_timeout_sec=20)
            # Teste rápido
            result = session.run_ps("Write-Output 'OK'")
            if result.status_code != 0:
                raise RuntimeError("WinRM não respondeu ao teste")
            yield sse({"type": "step_ok", "step": "connect", "message": f"Conectado em {target}"})
        except Exception as e:
            yield sse({"type": "error", "step": "connect", "message": f"Falha na conexão: {str(e)[:200]}"})
            yield sse({"type": "done", "success": False, "message": "Falha na conexão"})
            return

        # Atualiza status
        host_obj = db.query(Host).filter(Host.id == host_id_val).first()
        host_obj.status = "online"
        host_obj.so_type = "windows"
        host_obj.last_seen = datetime.utcnow()
        host_obj.updated_at = datetime.utcnow()
        if host_obj.is_legacy:
            host_obj.is_legacy = False
            host_obj.legacy_since = None
        db.commit()

        now = datetime.utcnow()
        total_success = 0
        total_fail = 0

        # ---- HARDWARE ----
        yield sse({"type": "step", "step": "hardware", "message": "📡 Coletando hardware..."})
        try:
            raw = _run_ps(session, _ps_hardware())
            hw = _safe_json(raw, "hardware")
            if hw:
                existing = db.query(HostHardware).filter(HostHardware.host_id == host_id_val).first()
                hw_data = {
                    "manufacturer": hw.get("manufacturer"),
                    "model": hw.get("model"),
                    "serial": hw.get("serial"),
                    "cpu": hw.get("cpu"),
                    "ram_gb": hw.get("ram_gb"),
                    "bios_version": hw.get("bios_version"),
                    "last_boot": hw.get("last_boot"),
                    "last_user": hw.get("last_user"),
                    "updated_at": now,
                }
                if existing:
                    for k, v in hw_data.items():
                        setattr(existing, k, v)
                else:
                    db.add(HostHardware(host_id=host_id_val, **hw_data))
                db.commit()
                cpu = hw.get("cpu", "?")[:40]
                ram = hw.get("ram_gb", "?")
                yield sse({"type": "step_ok", "step": "hardware", "message": f"✓ CPU: {cpu} | RAM: {ram} GB"})
                total_success += 1
            else:
                yield sse({"type": "step_warn", "step": "hardware", "message": "⚠ Hardware retornou vazio"})
        except Exception as e:
            yield sse({"type": "step_fail", "step": "hardware", "message": f"✗ Falha: {str(e)[:100]}"})
            total_fail += 1

        # ---- DISCOS ----
        yield sse({"type": "step", "step": "disks", "message": "💾 Coletando discos..."})
        try:
            raw = _run_ps(session, _ps_disks())
            parsed = _safe_json(raw, "disks")
            disks = parsed if isinstance(parsed, list) else [parsed] if parsed else []
            db.query(HostDisk).filter(HostDisk.host_id == host_id_val).delete()
            for d in disks:
                db.add(HostDisk(host_id=host_id_val, drive=d.get("drive"), total_gb=d.get("total_gb"), free_gb=d.get("free_gb"), filesystem=d.get("filesystem"), updated_at=now))
            db.commit()
            drives = ", ".join(d.get("drive", "?") for d in disks)
            yield sse({"type": "step_ok", "step": "disks", "message": f"✓ {len(disks)} disco(s): {drives}"})
            total_success += 1
        except Exception as e:
            yield sse({"type": "step_fail", "step": "disks", "message": f"✗ Falha: {str(e)[:100]}"})
            total_fail += 1

        # ---- REDE ----
        yield sse({"type": "step", "step": "network", "message": "🌐 Coletando rede..."})
        try:
            raw = _run_ps(session, _ps_network())
            parsed = _safe_json(raw, "network")
            network = parsed if isinstance(parsed, list) else [parsed] if parsed else []
            db.query(HostNetwork).filter(HostNetwork.host_id == host_id_val).delete()
            for n in network:
                db.add(HostNetwork(host_id=host_id_val, ip=n.get("ip"), mac=n.get("mac"), gateway=n.get("gateway"), dns=n.get("dns"), adapter_name=n.get("adapter_name"), updated_at=now))
            db.commit()
            ips = ", ".join(n.get("ip", "?") for n in network if n.get("ip"))
            yield sse({"type": "step_ok", "step": "network", "message": f"✓ {len(network)} adaptador(es): {ips}"})
            total_success += 1
        except Exception as e:
            yield sse({"type": "step_fail", "step": "network", "message": f"✗ Falha: {str(e)[:100]}"})
            total_fail += 1

        # ---- LICENÇAS (timeout controlado por step) ----
        yield sse({"type": "step", "step": "licenses", "message": "🔑 Coletando licenças..."})
        try:
            lic_session = _connect(target, cfg, operation_timeout_sec=20, read_timeout_sec=25)
            licenses = []
            for ps_func, label in [(_ps_windows_license, "windows"), (_ps_office_license, "office")]:
                try:
                    raw = _run_ps_with_timeout(lic_session, ps_func(), label=f"license-{label}", timeout_sec=20)
                    parsed = _safe_json(raw, f"license-{label}")
                    if parsed:
                        if isinstance(parsed, list):
                            licenses.extend(parsed)
                        else:
                            licenses.append(parsed)
                    yield sse({"type": "step_progress", "step": "licenses", "message": f"  ✓ licença {label} OK"})
                except TimeoutError as te:
                    yield sse({"type": "step_progress", "step": "licenses", "message": f"  ⏱ licença {label}: timeout"})
                except Exception as ee:
                    yield sse({"type": "step_progress", "step": "licenses", "message": f"  ⚠ licença {label}: {str(ee)[:60]}"})
            db.query(HostLicense).filter(HostLicense.host_id == host_id_val).delete()
            for lic in licenses:
                db.add(HostLicense(host_id=host_id_val, product=lic.get("product", "windows"), edition=lic.get("edition"), version=lic.get("version"), channel=lic.get("channel"), license_status=lic.get("license_status"), partial_product_key=lic.get("partial_product_key"), oem_key_found=lic.get("oem_key_found"), click_to_run=lic.get("click_to_run"), detection_method=lic.get("detection_method"), updated_at=now))
            db.commit()
            lic_summary = ", ".join(f"{l.get('product','?')}={l.get('license_status','?')}" for l in licenses)
            yield sse({"type": "step_ok", "step": "licenses", "message": f"✓ {len(licenses)} licença(s): {lic_summary}"})
            total_success += 1
        except Exception as e:
            yield sse({"type": "step_fail", "step": "licenses", "message": f"✗ Falha: {str(e)[:100]}"})
            total_fail += 1

        # ---- SOFTWARE ----
        yield sse({"type": "step", "step": "software", "message": "📦 Coletando softwares (pode demorar)..."})
        try:
            raw = _run_ps(session, _ps_software())
            parsed = _safe_json(raw, "software")
            software = parsed if isinstance(parsed, list) else [parsed] if parsed else []
            db.query(HostSoftware).filter(HostSoftware.host_id == host_id_val).delete()
            for sw in software:
                db.add(HostSoftware(host_id=host_id_val, name=sw.get("name"), version=sw.get("version"), publisher=sw.get("publisher"), install_date=sw.get("install_date"), install_location=sw.get("install_location"), updated_at=now))
            db.commit()
            yield sse({"type": "step_ok", "step": "software", "message": f"✓ {len(software)} software(s) instalados"})
            total_success += 1
        except Exception as e:
            yield sse({"type": "step_fail", "step": "software", "message": f"✗ Falha: {str(e)[:100]}"})
            total_fail += 1

        # ---- ACESSO REMOTO (AnyDesk + UltraVNC separados) ----
        yield sse({"type": "step", "step": "remote_access", "message": "🖥️ Coletando acesso remoto..."})
        try:
            ra_data = {
                "anydesk_id": None, "anydesk_alias": "", "anydesk_version": "",
                "ultravnc_installed": False, "ultravnc_port": None, "ultravnc_version": "",
                "updated_at": now,
            }
            # AnyDesk (script separado)
            try:
                raw = _run_ps(session, _ps_anydesk())
                ad = _safe_json(raw, "anydesk")
                if ad:
                    ra_data["anydesk_id"] = ad.get("anydesk_id")
                    ra_data["anydesk_alias"] = ad.get("anydesk_alias", "")
                    ra_data["anydesk_version"] = ad.get("anydesk_version", "")
                yield sse({"type": "step_progress", "step": "remote_access", "message": f"  AnyDesk: {ad.get('anydesk_id', 'não encontrado') if ad else 'não encontrado'}"})
            except Exception as e:
                yield sse({"type": "step_progress", "step": "remote_access", "message": f"  AnyDesk: falha — {str(e)[:60]}"})

            # UltraVNC (script separado)
            try:
                raw = _run_ps(session, _ps_ultravnc())
                uv = _safe_json(raw, "ultravnc")
                if uv:
                    ra_data["ultravnc_installed"] = uv.get("installed", False)
                    ra_data["ultravnc_port"] = uv.get("port")
                    ra_data["ultravnc_version"] = uv.get("version", "")
                yield sse({"type": "step_progress", "step": "remote_access", "message": f"  UltraVNC: {'instalado' if uv and uv.get('installed') else 'não encontrado'}"})
            except Exception as e:
                yield sse({"type": "step_progress", "step": "remote_access", "message": f"  UltraVNC: falha — {str(e)[:60]}"})

            # Salva no banco
            existing = db.query(HostRemoteAccess).filter(HostRemoteAccess.host_id == host_id_val).first()
            if existing:
                for k, v in ra_data.items():
                    setattr(existing, k, v)
            else:
                db.add(HostRemoteAccess(host_id=host_id_val, **ra_data))
            db.commit()

            parts = []
            if ra_data["anydesk_id"]:
                parts.append(f"AnyDesk: {ra_data['anydesk_id']}")
            if ra_data["ultravnc_installed"]:
                parts.append(f"UltraVNC: :{ra_data['ultravnc_port'] or 5900}")
            msg = " | ".join(parts) if parts else "Nenhum encontrado"
            yield sse({"type": "step_ok", "step": "remote_access", "message": f"✓ {msg}"})
            total_success += 1
        except Exception as e:
            yield sse({"type": "step_fail", "step": "remote_access", "message": f"✗ Falha: {str(e)[:100]}"})
            total_fail += 1

        # ---- DONE ----
        # Scan history
        db.add(ScanHistory(host_id=host_id_val, hostname=host.hostname, status="success", started_at=now, finished_at=datetime.utcnow()))

        # Activity log
        log_activity(db,
            activity_type="manual_collect",
            hostname=host.hostname,
            host_id=host_id_val,
            status="success" if total_fail == 0 else "partial",
            message=f"Coleta manual: {total_success} OK, {total_fail} falha(s)",
            details={"success": total_success, "fail": total_fail},
            source="manual",
        )
        db.commit()

        yield sse({
            "type": "done",
            "success": total_fail == 0,
            "message": f"Coleta concluída: {total_success} OK, {total_fail} falha(s)",
            "stats": {"success": total_success, "fail": total_fail},
        })

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{host_id}/action/install-updates-stream")
def action_install_updates_stream(host_id: int, reboot: bool = False, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Instala updates pendentes do host via Windows Update (WinRM), com progresso em SSE."""
    import json as json_mod
    import threading
    import queue as queue_mod
    from app.collector.winrm_collect import _get_settings, install_host_updates, scan_host_patches, _ps_pending_updates, _connect, _make_fqdn, _run_ps_with_timeout, _safe_json
    from app.core.database import HostPendingUpdate, HostPatchStatus

    host = db.query(Host).filter(Host.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host não encontrado")

    cfg = _get_settings(db)
    if not cfg:
        raise HTTPException(status_code=400, detail="Credenciais WinRM não configuradas. Configure em Configurações → WinRM.")

    def sse(data):
        return f"data: {json_mod.dumps(data, ensure_ascii=False)}\n\n"

    def event_stream():
        yield sse({"type": "start", "hostname": host.hostname})
        steps = queue_mod.Queue()

        def worker():
            try:
                result = install_host_updates(host.hostname, cfg, on_step=lambda m: steps.put(("step", m)), reboot=reboot)
                steps.put(("result", result))
            except Exception as e:
                steps.put(("exception", str(e)))

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        result = None
        error = None
        while True:
            try:
                kind, payload = steps.get(timeout=7200)
            except Exception:
                error = "Timeout aguardando instalação"
                break
            if kind == "step":
                cls = "step_ok" if payload.startswith("✓") else "step_warn" if payload.startswith("⚠") else "step"
                yield sse({"type": cls, "step": "install", "message": payload})
            elif kind == "exception":
                error = payload
                break
            else:
                result = payload
                break

        if error:
            yield sse({"type": "error", "step": "install", "message": f"Falha: {str(error)[:200]}"})
            yield sse({"type": "done", "success": False, "message": "Falha na instalação dos updates"})
            return

        success = bool(result.get("ok")) and result.get("install") in (2, 3, None)
        result_codes = {2: "Sucesso", 3: "Sucesso com erros", 4: "Falhou", 5: "Abortado", None: "Não executado"}
        yield sse({"type": "step", "step": "install", "message": f"Instalação: {result.get('install_label', '—')}", "install_result": {
            "found": result.get("found", 0),
            "download": result.get("download"),
            "downloadLabel": result_codes.get(result.get("download"), str(result.get("download"))) if result.get("download") is not None else None,
            "install": result.get("install"),
            "installLabel": result.get("install_label") or result_codes.get(result.get("install"), "—"),
            "rebootRequired": bool(result.get("reboot")),
        }})

        # Reescaneia updates pendentes para atualizar o banco (pula se o host está reiniciando)
        pending_after = None
        if success and not reboot:
            try:
                yield sse({"type": "step", "step": "rescan", "message": "Reescaneando updates pendentes..."})
                scan = scan_host_patches(host.hostname, cfg)
                pending = scan.get("pending", [])
                status = scan.get("status", {})
                now = datetime.utcnow()
                db.query(HostPendingUpdate).filter(HostPendingUpdate.host_id == host_id).delete()
                for p in pending:
                    db.add(HostPendingUpdate(host_id=host_id, kb=p.get("kb"), title=p.get("title"), severity=p.get("severity"), reboot_required=bool(p.get("reboot")), updated_at=now))
                critical = sum(1 for p in pending if (p.get("severity") or "").lower() == "critical")
                existing_ps = db.query(HostPatchStatus).filter(HostPatchStatus.host_id == host_id).first()
                ps_values = {
                    "wu_last_success": status.get("wu_last_success"),
                    "pending_count": len(pending),
                    "critical_pending": critical,
                    "last_scan": now,
                    "updated_at": now,
                }
                if existing_ps:
                    for k, v in ps_values.items():
                        setattr(existing_ps, k, v)
                else:
                    db.add(HostPatchStatus(host_id=host_id, **ps_values))
                db.commit()
                pending_after = len(pending)
                yield sse({"type": "step_ok", "step": "rescan", "message": f"✓ {len(pending)} update(s) ainda pendente(s)", "pending_after": pending_after})
            except Exception as e:
                yield sse({"type": "step_warn", "step": "rescan", "message": f"⚠ Reescaneamento falhou: {str(e)[:100]}"})

        log_activity(db,
            activity_type="manual_collect",
            hostname=host.hostname,
            host_id=host_id,
            status="success" if success else "error",
            message=f"Update remoto: {result.get('found', 0)} update(s), resultado: {result.get('install_label', '—')}",
            details={"found": result.get("found"), "install": result.get("install"), "reboot_required": result.get("reboot")},
            source="manual")

        yield sse({"type": "done", "success": success, "message": f"Instalação concluída: {result.get('install_label', '—')}", "reboot_required": bool(result.get("reboot")), "found": result.get("found", 0), "pending_after": pending_after})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{host_id}/action/scan-updates-stream")
def action_scan_updates_stream(host_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Escaneia patches do host via SSE: KBs instalados + updates pendentes + build."""
    import json as json_mod
    from app.collector.winrm_collect import _get_settings, scan_host_patches
    from app.core.database import HostPatch, HostPendingUpdate, HostPatchStatus

    host = db.query(Host).filter(Host.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host não encontrado")

    cfg = _get_settings(db)
    if not cfg:
        raise HTTPException(status_code=400, detail="Credenciais WinRM não configuradas. Configure em Configurações → WinRM.")

    def sse(data):
        return f"data: {json_mod.dumps(data, ensure_ascii=False)}\n\n"

    def event_stream():
        yield sse({"type": "start", "hostname": host.hostname})
        now = datetime.utcnow()

        steps = []
        try:
            result = scan_host_patches(host.hostname, cfg, on_step=lambda m: steps.append(m))
        except Exception as e:
            yield sse({"type": "error", "step": "connect", "message": f"Falha na conexão: {str(e)[:200]}"})
            yield sse({"type": "done", "success": False, "message": "Falha na conexão WinRM"})
            return

        # Reproduz os passos no console
        for m in steps:
            cls = "step_ok" if m.startswith("✓") else "step_warn" if m.startswith("⚠") else "step"
            yield sse({"type": cls, "step": "patches", "message": m})

        # Persiste no banco
        hotfixes = result.get("hotfixes", [])
        pending = result.get("pending", [])
        status = result.get("status", {})

        db.query(HostPatch).filter(HostPatch.host_id == host_id).delete()
        for p in hotfixes:
            db.add(HostPatch(host_id=host_id, kb=p.get("kb"), description=p.get("description"), installed_on=p.get("installed_on"), updated_at=now))

        db.query(HostPendingUpdate).filter(HostPendingUpdate.host_id == host_id).delete()
        for p in pending:
            db.add(HostPendingUpdate(host_id=host_id, kb=p.get("kb"), title=p.get("title"), severity=p.get("severity"), reboot_required=bool(p.get("reboot")), updated_at=now))

        critical = sum(1 for p in pending if (p.get("severity") or "").lower() == "critical")
        existing_ps = db.query(HostPatchStatus).filter(HostPatchStatus.host_id == host_id).first()
        ps_values = {
            "os_edition": status.get("os_edition"),
            "display_version": status.get("display_version"),
            "build": status.get("build"),
            "wu_last_success": status.get("wu_last_success"),
            "pending_count": len(pending),
            "critical_pending": critical,
            "last_error": status.get("last_error"),
            "last_scan": now,
            "updated_at": now,
        }
        if existing_ps:
            for k, v in ps_values.items():
                setattr(existing_ps, k, v)
        else:
            db.add(HostPatchStatus(host_id=host_id, **ps_values))

        # Atualiza host
        host.status = "online"
        host.so_type = "windows"
        host.last_seen = now
        host.updated_at = now
        if host.is_legacy:
            host.is_legacy = False
            host.legacy_since = None

        log_activity(db,
            activity_type="manual_collect",
            hostname=host.hostname,
            host_id=host_id,
            status="success",
            message=f"Scan de updates: {len(hotfixes)} KBs, {len(pending)} pendente(s)",
            details={"hotfixes": len(hotfixes), "pending": len(pending), "critical": critical},
            source="manual",
        )
        db.commit()

        yield sse({
            "type": "done",
            "success": True,
            "message": f"Scan concluído: {len(hotfixes)} KBs instalados, {len(pending)} update(s) pendente(s)",
            "stats": {"hotfixes": len(hotfixes), "pending": len(pending), "critical": critical},
        })

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.delete("/{host_id}")
def delete_host(host_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Exclui permanentemente um host e todos os dados associados."""
    host = db.query(Host).filter(Host.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host não encontrado")
    hostname = host.hostname
    # Remove dados associados
    db.query(HostHardware).filter(HostHardware.host_id == host_id).delete()
    db.query(HostDisk).filter(HostDisk.host_id == host_id).delete()
    db.query(HostNetwork).filter(HostNetwork.host_id == host_id).delete()
    db.query(HostLicense).filter(HostLicense.host_id == host_id).delete()
    db.query(HostSoftware).filter(HostSoftware.host_id == host_id).delete()
    db.query(HostRemoteAccess).filter(HostRemoteAccess.host_id == host_id).delete()
    db.query(ScanHistory).filter(ScanHistory.host_id == host_id).delete()
    db.delete(host)
    db.commit()
    return {"detail": f"Host {hostname} excluído permanentemente"}


# ---- Ações nos Hosts ----

@router.post("/{host_id}/action/ping")
def action_ping(host_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Testa conectividade ping com o host com diagnóstico de DNS."""
    import subprocess
    import socket
    import re

    host = db.query(Host).filter(Host.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host não encontrado")

    debug = []
    hostname = host.hostname

    # 1. Lê resolv.conf atual
    resolv_content = ""
    try:
        with open("/etc/resolv.conf", "r") as f:
            resolv_content = f.read().strip()
        debug.append(f"resolv.conf: {resolv_content.replace(chr(10), ' | ')}")
    except Exception as e:
        debug.append(f"resolv.conf: erro ao ler: {e}")

    # 2. Tenta resolver DNS
    resolved_ip = None
    try:
        results = socket.getaddrinfo(hostname, None)
        ips = list(set(addr[4][0] for addr in results))
        resolved_ip = ips[0] if ips else None
        debug.append(f"DNS resolveu: {hostname} → {', '.join(ips)}")
    except socket.gaierror as e:
        debug.append(f"DNS falhou para '{hostname}': {e}")
    except Exception as e:
        debug.append(f"DNS erro: {e}")

    # 3. Executa ping
    try:
        result = subprocess.run(
            ["ping", "-c", "3", "-W", "3", hostname],
            capture_output=True, text=True, timeout=15
        )
        ping_output = (result.stdout or "").strip()
        ping_stderr = (result.stderr or "").strip()
        debug.append(f"ping exit code: {result.returncode}")
        if ping_stderr:
            debug.append(f"ping stderr: {ping_stderr[-200:]}")
        if ping_output:
            debug.append(f"ping output: {ping_output[-200:]}")

        if result.returncode == 0:
            avg_match = re.search(r"rtt min/avg/max/mdev = [\d.]+/([\d.]+)/", ping_output)
            avg_ms = float(avg_match.group(1)) if avg_match else None
            msg = f"{hostname} respondeu ao ping" + (f" ({avg_ms:.0f}ms)" if avg_ms else "")
            if resolved_ip:
                msg += f" [IP: {resolved_ip}]"
            # Atualiza ping_status
            host.ping_status = "online"
            host.last_ping = datetime.utcnow()
            log_activity(db, activity_type="ping_single", hostname=hostname, host_id=host_id, status="success", message=msg, source="manual")
            db.commit()
            return {
                "success": True,
                "message": msg,
                "hostname": hostname,
                "resolved_ip": resolved_ip,
                "avg_ms": avg_ms,
                "debug": debug,
            }
        else:
            log_activity(db, activity_type="ping_single", hostname=hostname, host_id=host_id, status="fail", message=f"{hostname} não respondeu ao ping", source="manual")
            db.commit()
            return {
                "success": False,
                "message": f"{hostname} não respondeu ao ping" + (f" [resolveu: {resolved_ip}]" if resolved_ip else " [DNS não resolveu]"),
                "hostname": hostname,
                "resolved_ip": resolved_ip,
                "debug": debug,
            }
    except subprocess.TimeoutExpired:
        host.ping_status = "offline"
        host.last_ping = datetime.utcnow()
        log_activity(db, activity_type="ping_single", hostname=hostname, host_id=host_id, status="timeout", message=f"Timeout ao pingar {hostname}", source="manual")
        db.commit()
        return {"success": False, "message": f"Timeout ao pingar {hostname}", "hostname": hostname, "resolved_ip": resolved_ip, "debug": debug}
    except Exception as e:
        return {"success": False, "message": f"Erro: {str(e)[:200]}", "hostname": hostname, "resolved_ip": resolved_ip, "debug": debug}


@router.post("/{host_id}/action/test-winrm")
def action_test_winrm(host_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Testa conexão WinRM com o host usando credenciais das settings."""
    try:
        import winrm
    except ImportError:
        raise HTTPException(status_code=500, detail="Biblioteca pywinrm não instalada")

    host = db.query(Host).filter(Host.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host não encontrado")

    from app.core.database import Setting
    winrm_user = db.query(Setting).filter(Setting.key == "username", Setting.category == "winrm").first()
    winrm_pass = db.query(Setting).filter(Setting.key == "password", Setting.category == "winrm").first()
    winrm_scheme = db.query(Setting).filter(Setting.key == "scheme", Setting.category == "winrm").first()
    winrm_port = db.query(Setting).filter(Setting.key == "port", Setting.category == "winrm").first()
    dns_search = db.query(Setting).filter(Setting.key == "search_domain", Setting.category == "dns").first()

    if not winrm_user or not winrm_pass:
        return {"success": False, "message": "Credenciais WinRM não configuradas. Vá em Configurações.", "hostname": host.hostname}

    scheme = winrm_scheme.value if winrm_scheme else "http"
    port = int(winrm_port.value) if winrm_port else 5985

    # Monta FQDN se hostname não contém ponto
    target_host = host.hostname
    search = dns_search.value.strip() if dns_search and dns_search.value else ""
    if "." not in target_host and search:
        target_host = f"{target_host}.{search}"

    try:
        endpoint = f"{scheme}://{target_host}:{port}"
        session = winrm.Session(
            endpoint,
            auth=(winrm_user.value, winrm_pass.value),
            transport="ntlm",
            server_cert_validation="ignore",
        )
        result = session.run_ps("Write-Output 'OK'")
        stdout = result.std_out.decode("utf-8", errors="replace").strip()
        # Atualiza status e last_seen
        host.status = "online"
        host.so_type = "windows"
        host.last_seen = datetime.utcnow()
        log_activity(db, activity_type="winrm_test", hostname=host.hostname, host_id=host_id, status="success", message=f"WinRM OK: {host.hostname}", source="manual")
        db.commit()
        return {
            "success": True,
            "message": f"WinRM OK — resposta: {stdout}",
            "hostname": host.hostname,
        }
    except Exception as e:
        # Atualiza status se falhou
        host.status = "offline"
        host.updated_at = datetime.utcnow()
        log_activity(db, activity_type="winrm_test", hostname=host.hostname, host_id=host_id, status="fail", message=f"WinRM falhou: {str(e)[:100]}", source="manual")
        db.commit()
        return {
            "success": False,
            "message": f"Falha WinRM: {str(e)[:200]}",
            "hostname": host.hostname,
        }


@router.post("/{host_id}/action/enable-winrm")
def action_enable_winrm(host_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Tenta ativar WinRM remotamente via impacket.

    Executa remotamente via WMI/SMB:
      1. Enable-PSRemoting -Force
      2. Configura TrustedHosts = *
      3. Habilita autenticação Basic e NTLM
      4. Permite tráfego não criptografado
    """
    host = db.query(Host).filter(Host.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host não encontrado")

    from app.core.database import Setting
    winrm_user = db.query(Setting).filter(Setting.key == "username", Setting.category == "winrm").first()
    winrm_pass = db.query(Setting).filter(Setting.key == "password", Setting.category == "winrm").first()
    dns_search = db.query(Setting).filter(Setting.key == "search_domain", Setting.category == "dns").first()

    if not winrm_user or not winrm_pass:
        return {"success": False, "message": "Credenciais WinRM não configuradas", "hostname": host.hostname}

    # Monta FQDN se hostname não contém ponto
    target_host = host.hostname
    search = dns_search.value.strip() if dns_search and dns_search.value else ""
    if "." not in target_host and search:
        target_host = f"{target_host}.{search}"

    try:
        from impacket.smbconnection import SMBConnection  # noqa: F401
    except ImportError:
        raise HTTPException(status_code=500, detail="impacket não instalado. Adicione ao requirements.txt do backend.")

    import subprocess
    import sys
    import shutil
    import os

    # Parse domínio/usuário
    raw_user = winrm_user.value
    password = winrm_pass.value
    domain = ""
    user = raw_user
    # Aceita tanto \ quanto \\ como separador
    if "\\" in user:
        parts = user.split("\\", 1)
        domain = parts[0]
        user = parts[1]
    elif "/" in user:
        parts = user.split("/", 1)
        domain = parts[0]
        user = parts[1]
    elif "@" in user:
        parts = user.split("@", 1)
        user = parts[0]
        domain = parts[1]

    # Debug de credenciais (sem mostrar senha)
    debug_creds = f"domain={domain}, user={user}, host={target_host}"

    # Script PowerShell para habilitar WinRM
    ps_script = (
        "powershell.exe -ExecutionPolicy Bypass -Command "
        "\"Enable-PSRemoting -Force; "
        "Set-Item WSMan:\\localhost\\Client\\TrustedHosts -Value '*' -Force; "
        "Set-Item WSMan:\\localhost\\Service\\Auth\\Basic -Value $true -Force; "
        "Set-Item WSMan:\\localhost\\Service\\Auth\\Negotiate -Value $true -Force; "
        "winrm set winrm/config/service '@{AllowUnencrypted=\"true\"}'\""
    )

    # Monta credencial no formato impacket: domain/user:password@host
    if domain:
        creds = f"{domain}/{user}:{password}@{target_host}"
    else:
        creds = f"{user}:{password}@{target_host}"

    # Encontra o script psexec do impacket
    psexec_cmd = None

    # 1. Tenta console scripts instalados pelo pip
    for name in ["psexec.py", "impacket-psexec", "psexec"]:
        found = shutil.which(name)
        if found:
            psexec_cmd = [found, creds, ps_script]
            break

    # 2. Tenta encontrar no pacote impacket
    if not psexec_cmd:
        try:
            import impacket
            pkg_path = os.path.dirname(impacket.__file__)
            for candidate in [
                os.path.join(pkg_path, "examples", "psexec.py"),
                os.path.join(pkg_path, "scripts", "psexec.py"),
            ]:
                if os.path.isfile(candidate):
                    psexec_cmd = [sys.executable, candidate, creds, ps_script]
                    break
        except Exception:
            pass

    # 3. Fallback: tenta via wmiexec (mais disponível)
    if not psexec_cmd:
        for name in ["wmiexec.py", "impacket-wmiexec", "wmiexec"]:
            found = shutil.which(name)
            if found:
                psexec_cmd = [found, creds, ps_script]
                break

    # 4. Último fallback: smbexec
    if not psexec_cmd:
        for name in ["smbexec.py", "impacket-smbexec", "smbexec"]:
            found = shutil.which(name)
            if found:
                psexec_cmd = [found, creds, ps_script]
                break

    if not psexec_cmd:
        return {
            "success": False,
            "message": "Não encontrou psexec/wmiexec/smbexec do impacket. Verifique a instalação.",
            "hostname": host.hostname,
        }

    # Verifica se porta SMB (445) está alcançável antes de tentar
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        # Resolve hostname primeiro
        target_ip = target_host
        try:
            target_ip = socket.gethostbyname(target_host)
        except socket.gaierror:
            pass
        result_sock = sock.connect_ex((target_ip, 445))
        sock.close()
        if result_sock != 0:
            return {
                "success": False,
                "message": f"Porta SMB (445) fechada/inacessível em {target_host} (IP: {target_ip}). O PSExec precisa de SMB para funcionar. Verifique firewall do Windows.",
                "hostname": host.hostname,
                "debug": [f"Credenciais: {debug_creds}", f"Target: {target_ip}:445 → fechada"],
            }
    except socket.timeout:
        return {
            "success": False,
            "message": f"Timeout ao testar porta SMB (445) em {target_host}. Host pode estar offline ou firewall bloqueando.",
            "hostname": host.hostname,
            "debug": [f"Credenciais: {debug_creds}"],
        }
    except Exception as e:
        pass  # Continua mesmo sem conseguir testar a porta

    try:
        result = subprocess.run(
            psexec_cmd,
            capture_output=True, text=True, timeout=30
        )

        output = (result.stdout or "") + (result.stderr or "")

        # Debug info
        debug = [
            f"Credenciais: {debug_creds}",
            f"Comando: {' '.join(psexec_cmd[:2])} [creds] [script]",
            f"Exit code: {result.returncode}",
        ]

        if result.returncode == 0 or "completed successfully" in output.lower() or "[+]" in output:
            host.status = "online"
            host.so_type = "windows"
            host.last_seen = datetime.utcnow()
            db.commit()
            log_activity(db, activity_type="winrm_enable", hostname=host.hostname, host_id=host_id, status="success", message=f"WinRM ativado remotamente em {host.hostname}", source="manual")
            db.commit()
            return {
                "success": True,
                "message": f"WinRM ativado remotamente em {host.hostname}. Use 'Testar WinRM' para confirmar.",
                "hostname": host.hostname,
                "debug": debug,
            }
        else:
            err_lines = output.strip().split("\n")[-8:]
            err = " | ".join(line.strip() for line in err_lines if line.strip())
            debug.append(f"Output: {err[:300]}")
            return {
                "success": False,
                "message": f"Falha ao ativar WinRM: {err[:300]}",
                "hostname": host.hostname,
                "debug": debug,
            }
    except subprocess.TimeoutExpired:
        log_activity(db, activity_type="winrm_enable", hostname=host.hostname, host_id=host_id, status="timeout", message=f"Timeout ao ativar WinRM em {host.hostname}", source="manual")
        db.commit()
        return {"success": False, "message": f"Timeout (30s) ao ativar WinRM em {host.hostname}. Possível firewall bloqueando SMB (porta 445).", "hostname": host.hostname}
    except Exception as e:
        return {"success": False, "message": f"Erro: {str(e)[:200]}", "hostname": host.hostname}


@router.get("/{host_id}")
def get_host(host_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    host = db.query(Host).filter(Host.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host não encontrado")

    hardware = db.query(HostHardware).filter(HostHardware.host_id == host_id).first()
    disks = db.query(HostDisk).filter(HostDisk.host_id == host_id).all()
    network = db.query(HostNetwork).filter(HostNetwork.host_id == host_id).all()
    licenses = db.query(HostLicense).filter(HostLicense.host_id == host_id).all()
    software = db.query(HostSoftware).filter(HostSoftware.host_id == host_id).all()
    remote_access = db.query(HostRemoteAccess).filter(HostRemoteAccess.host_id == host_id).first()
    distro = db.query(HostDistro).filter(HostDistro.host_id == host_id).first()
    scans = (
        db.query(ScanHistory)
        .filter(ScanHistory.host_id == host_id)
        .order_by(ScanHistory.finished_at.desc())
        .limit(20)
        .all()
    )

    patches = db.query(HostPatch).filter(HostPatch.host_id == host_id).order_by(HostPatch.installed_on.desc()).limit(200).all()
    pending_updates = db.query(HostPendingUpdate).filter(HostPendingUpdate.host_id == host_id).all()
    patch_status = db.query(HostPatchStatus).filter(HostPatchStatus.host_id == host_id).first()

    return {
        "host": HostResponse.model_validate(host),
        "hardware": hardware,
        "disks": disks,
        "network": network,
        "licenses": licenses,
        "software": software,
        "remote_access": remote_access,
        "distro": distro,
        "scans": scans,
        "patches": patches,
        "pending_updates": pending_updates,
        "patch_status": patch_status,
    }
