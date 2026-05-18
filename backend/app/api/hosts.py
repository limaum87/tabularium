from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import (
    get_db, Host, HostHardware, HostDisk, HostNetwork,
    HostLicense, HostSoftware, ScanHistory,
)
from app.core.security import get_current_user
from app.schemas.schemas import CheckinPayload, HostResponse

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

    if host:
        host.domain = body.domain or host.domain
        host.status = "online"
        host.last_seen = now
        host.updated_at = now
        # Se era legado, restaurar automaticamente ao fazer checkin
        if host.is_legacy:
            host.is_legacy = False
            host.legacy_since = None
    else:
        host = Host(
            hostname=body.hostname,
            domain=body.domain,
            status="online",
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
            "updated_at": now,
        }
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
        else:
            db.add(HostHardware(host_id=host_id, **data))

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

    # Scan history
    db.add(ScanHistory(
        host_id=host_id,
        hostname=body.hostname,
        status="success",
        started_at=now,
        finished_at=now,
    ))

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
    return {
        "detail": f"Ping sweep concluído: {online} online, {offline} offline",
        "total": len(active_hosts),
        "online": online,
        "offline": offline,
        "results": results,
    }


@router.get("/{host_id}/action/collect-stream")
def action_collect_stream(host_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Coleta manual via SSE — roda cada step individualmente e envia progresso."""
    import json as json_mod
    import winrm
    from app.collector.winrm_collect import _get_settings, _make_fqdn, _connect, _run_ps, _safe_json
    from app.collector.winrm_collect import (
        _ps_hardware, _ps_disks, _ps_network,
        _ps_windows_license, _ps_office_license, _ps_software,
    )

    host = db.query(Host).filter(Host.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host não encontrado")

    cfg = _get_settings(db)
    if not cfg:
        raise HTTPException(status_code=400, detail="Credenciais WinRM não configuradas")

    host_id_val = host.id

    def sse(data):
        return f"data: {json_mod.dumps(data, ensure_ascii=False)}\n\n"

    def event_stream():
        yield sse({"type": "start", "hostname": host.hostname})

        # Conecta WinRM
        try:
            target = _make_fqdn(host.hostname, cfg.get("search", ""))
            yield sse({"type": "step", "step": "connect", "message": f"Conectando em {target}..."})
            session = _connect(target, cfg)
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

        # ---- LICENÇAS ----
        yield sse({"type": "step", "step": "licenses", "message": "🔑 Coletando licenças..."})
        try:
            licenses = []
            for ps_func, label in [(_ps_windows_license, "windows"), (_ps_office_license, "office")]:
                try:
                    raw = _run_ps(session, ps_func())
                    parsed = _safe_json(raw, f"license-{label}")
                    if parsed:
                        if isinstance(parsed, list):
                            licenses.extend(parsed)
                        else:
                            licenses.append(parsed)
                except Exception:
                    pass
            db.query(HostLicense).filter(HostLicense.host_id == host_id_val).delete()
            for lic in licenses:
                db.add(HostLicense(host_id=host_id_val, product=lic.get("product", "windows"), edition=lic.get("edition"), version=lic.get("version"), channel=lic.get("channel"), license_status=lic.get("license_status"), partial_product_key=lic.get("partial_product_key"), oem_key_found=lic.get("oem_key_found"), detection_method=lic.get("detection_method"), updated_at=now))
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

        # ---- DONE ----
        # Scan history
        db.add(ScanHistory(host_id=host_id_val, hostname=host.hostname, status="success", started_at=now, finished_at=datetime.utcnow()))
        db.commit()

        yield sse({
            "type": "done",
            "success": total_fail == 0,
            "message": f"Coleta concluída: {total_success} OK, {total_fail} falha(s)",
            "stats": {"success": total_success, "fail": total_fail},
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
        host.last_seen = datetime.utcnow()
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
            host.last_seen = datetime.utcnow()
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
    scans = (
        db.query(ScanHistory)
        .filter(ScanHistory.host_id == host_id)
        .order_by(ScanHistory.finished_at.desc())
        .limit(20)
        .all()
    )

    return {
        "host": HostResponse.model_validate(host),
        "hardware": hardware,
        "disks": disks,
        "network": network,
        "licenses": licenses,
        "software": software,
        "scans": scans,
    }
