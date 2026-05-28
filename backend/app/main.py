from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os
import asyncio
import subprocess
from datetime import datetime, timedelta

from app.core.database import engine, Base, SessionLocal, User, Host, HostDistro
from app.core.config import settings
from app.core.security import hash_password
from app.api import auth, users, hosts, reports, settings as settings_api, discovery, activity

# ---- Background Tasks ----

PING_INTERVAL = 300  # 5 minutos
PING_LOG_INTERVAL = 3600  # log a cada 1 hora
COLLECT_INTERVAL = 21600  # 6 horas (padrão, pode ser alterado no banco)
COLLECT_STARTUP_DELAY = 60  # espera 60s antes da primeira coleta
NET_DISCOVERY_INTERVAL = 86400  # 24 horas (1x por dia)
NET_DISCOVERY_STARTUP_DELAY = 120  # espera 2 min antes do primeiro scan
AD_DISCOVERY_INTERVAL = 259200  # 3 dias (1x a cada 3 dias)
AD_DISCOVERY_STARTUP_DELAY = 180  # espera 3 min antes do primeiro scan

_ping_task = None
_collect_task = None
_net_discovery_task = None
_ad_discovery_task = None
_last_ping_log = None
_last_collect_log = None
_next_ping_at = None       # datetime da próxima execução de ping sweep
_next_collect_at = None    # datetime da próxima execução de coleta automática
_next_net_discovery_at = None  # datetime do próximo network discovery
_next_ad_discovery_at = None   # datetime do próximo AD discovery


async def _ping_loop():
    """Background task: faz ping em todos os hosts a cada 5 minutos."""
    global _last_ping_log, _next_ping_at
    while True:
        try:
            _next_ping_at = datetime.utcnow() + timedelta(seconds=PING_INTERVAL)
            await asyncio.sleep(PING_INTERVAL)
            should_log = False
            now_ts = asyncio.get_event_loop().time()
            if _last_ping_log is None or (now_ts - _last_ping_log) >= PING_LOG_INTERVAL:
                should_log = True
                _last_ping_log = now_ts
            # Roda em thread pool para não bloquear o event loop
            await asyncio.to_thread(_run_ping_sweep, should_log)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[ping-sweep] Erro: {e}")


def _run_ping_sweep(log=False):
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

        # Activity log para ping sweep automático (apenas quando log=True)
        if log:
            try:
                from app.api.activity import log_activity
                log_activity(db,
                    activity_type="ping_sweep",
                    status="success",
                    message=f"Ping sweep automático: {online_count} online, {offline_count} offline de {len(active_hosts)} hosts",
                    details={"online": online_count, "offline": offline_count, "total": len(active_hosts)},
                    source="system",
                )
                db.commit()
            except Exception as e:
                print(f"[ping-sweep] Erro ao registrar log: {e}")

        print(f"[ping-sweep] {len(active_hosts)} hosts verificados — online: {online_count} | offline: {offline_count}")
    except Exception as e:
        print(f"[ping-sweep] Erro geral: {e}")
    finally:
        db.close()


# ---- Coleta automática background ----

def _get_collect_settings():
    """Lê settings de coleta do banco. Retorna dict ou None se não configurado."""
    from app.core.database import Setting
    db = SessionLocal()
    try:
        winrm_user = db.query(Setting).filter(Setting.key == "username", Setting.category == "winrm").first()
        winrm_pass = db.query(Setting).filter(Setting.key == "password", Setting.category == "winrm").first()
        winrm_scheme = db.query(Setting).filter(Setting.key == "scheme", Setting.category == "winrm").first()
        winrm_port = db.query(Setting).filter(Setting.key == "port", Setting.category == "winrm").first()
        dns_search = db.query(Setting).filter(Setting.key == "search_domain", Setting.category == "dns").first()
        interval_setting = db.query(Setting).filter(Setting.key == "interval_hours", Setting.category == "schedule").first()
        enabled_setting = db.query(Setting).filter(Setting.key == "enabled", Setting.category == "schedule").first()

        # Verifica se está habilitado
        if enabled_setting and enabled_setting.value.strip().lower() in ("false", "0", "no"):
            return None

        if not winrm_user or not winrm_pass:
            return None

        scheme = winrm_scheme.value if winrm_scheme else "http"
        port = int(winrm_port.value) if winrm_port and winrm_port.value else 5985
        search = dns_search.value.strip() if dns_search and dns_search.value else ""
        interval_h = int(interval_setting.value) if interval_setting and interval_setting.value else 6

        return {
            "username": winrm_user.value,
            "password": winrm_pass.value,
            "scheme": scheme,
            "port": port,
            "search": search,
            "interval_hours": max(1, interval_h),
        }
    except Exception as e:
        print(f"[collect-auto] Erro ao ler settings: {e}")
        return None
    finally:
        db.close()


def _run_auto_collect():
    """Executa coleta automática em todos os hosts ativos."""
    from datetime import datetime
    from app.core.database import HostHardware, HostDisk, HostNetwork, HostLicense, HostSoftware, HostRemoteAccess, ScanHistory
    from app.api.activity import log_activity
    from app.collector.winrm_collect import collect_host, _make_fqdn, _connect, _run_ps, _safe_json
    from app.collector.winrm_collect import (
        _ps_hardware, _ps_disks, _ps_network,
        _ps_windows_license, _ps_office_license, _ps_software,
        _ps_anydesk, _ps_ultravnc,
    )
    import json as json_mod

    cfg = _get_collect_settings()
    if not cfg:
        return

    db = SessionLocal()
    try:
        active_hosts = db.query(Host).filter(Host.is_legacy == False).all()
        if not active_hosts:
            print("[collect-auto] Nenhum host ativo para coletar")
            return

        now = datetime.utcnow()
        stats = {"collected": 0, "offline": 0, "errors": 0}

        print(f"[collect-auto] Iniciando coleta de {len(active_hosts)} hosts...")

        for host in active_hosts:
            target = _make_fqdn(host.hostname, cfg.get("search", ""))
            try:
                session = _connect(target, cfg, operation_timeout_sec=15, read_timeout_sec=20)
                # Teste rápido com timeout curto
                test = session.run_ps("Write-Output 'OK'")
                if test.status_code != 0:
                    raise RuntimeError("WinRM não respondeu")
            except Exception as e:
                stats["offline"] += 1
                log_activity(db, activity_type="collector_checkin", hostname=host.hostname, host_id=host.id,
                             status="offline", message=f"Coleta auto: {host.hostname} offline — {str(e)[:80]}", source="system")
                continue

            # Atualiza status do host
            host_obj = db.query(Host).filter(Host.id == host.id).first()
            host_obj.status = "online"
            host_obj.so_type = "windows"
            host_obj.last_seen = now
            host_obj.updated_at = now
            if host_obj.is_legacy:
                host_obj.is_legacy = False
                host_obj.legacy_since = None
            db.commit()

            host_id_val = host.id
            total_success = 0
            total_fail = 0

            # HARDWARE
            try:
                raw = _run_ps(session, _ps_hardware())
                hw = _safe_json(raw, "hardware")
                if hw:
                    existing = db.query(HostHardware).filter(HostHardware.host_id == host_id_val).first()
                    hw_data = {
                        "manufacturer": hw.get("manufacturer"), "model": hw.get("model"),
                        "serial": hw.get("serial"), "cpu": hw.get("cpu"),
                        "ram_gb": hw.get("ram_gb"), "bios_version": hw.get("bios_version"),
                        "last_boot": hw.get("last_boot"), "last_user": hw.get("last_user"),
                        "updated_at": now,
                    }
                    if existing:
                        for k, v in hw_data.items():
                            setattr(existing, k, v)
                    else:
                        db.add(HostHardware(host_id=host_id_val, **hw_data))
                    db.commit()
                    total_success += 1
            except Exception:
                total_fail += 1

            # DISCOS
            try:
                raw = _run_ps(session, _ps_disks())
                parsed = _safe_json(raw, "disks")
                disks = parsed if isinstance(parsed, list) else [parsed] if parsed else []
                db.query(HostDisk).filter(HostDisk.host_id == host_id_val).delete()
                for d in disks:
                    db.add(HostDisk(host_id=host_id_val, drive=d.get("drive"), total_gb=d.get("total_gb"),
                                    free_gb=d.get("free_gb"), filesystem=d.get("filesystem"), updated_at=now))
                db.commit()
                total_success += 1
            except Exception:
                total_fail += 1

            # REDE
            try:
                raw = _run_ps(session, _ps_network())
                parsed = _safe_json(raw, "network")
                network = parsed if isinstance(parsed, list) else [parsed] if parsed else []
                db.query(HostNetwork).filter(HostNetwork.host_id == host_id_val).delete()
                for n in network:
                    db.add(HostNetwork(host_id=host_id_val, ip=n.get("ip"), mac=n.get("mac"),
                                       gateway=n.get("gateway"), dns=n.get("dns"),
                                       adapter_name=n.get("adapter_name"), updated_at=now))
                db.commit()
                total_success += 1
            except Exception:
                total_fail += 1

            # LICENÇAS
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
                    db.add(HostLicense(host_id=host_id_val, product=lic.get("product", "windows"),
                                       edition=lic.get("edition"), version=lic.get("version"),
                                       channel=lic.get("channel"), license_status=lic.get("license_status"),
                                       partial_product_key=lic.get("partial_product_key"),
                                       oem_key_found=lic.get("oem_key_found"),
                                       click_to_run=lic.get("click_to_run"),
                                       detection_method=lic.get("detection_method"), updated_at=now))
                db.commit()
                total_success += 1
            except Exception:
                total_fail += 1

            # SOFTWARE
            try:
                raw = _run_ps(session, _ps_software())
                parsed = _safe_json(raw, "software")
                software = parsed if isinstance(parsed, list) else [parsed] if parsed else []
                db.query(HostSoftware).filter(HostSoftware.host_id == host_id_val).delete()
                for sw in software:
                    db.add(HostSoftware(host_id=host_id_val, name=sw.get("name"), version=sw.get("version"),
                                        publisher=sw.get("publisher"), install_date=sw.get("install_date"),
                                        install_location=sw.get("install_location"), updated_at=now))
                db.commit()
                total_success += 1
            except Exception:
                total_fail += 1

            # ACESSO REMOTO
            try:
                ra_data = {"anydesk_id": None, "anydesk_alias": "", "anydesk_version": "",
                           "ultravnc_installed": False, "ultravnc_port": None, "ultravnc_version": "", "updated_at": now}
                try:
                    raw = _run_ps(session, _ps_anydesk())
                    ad = _safe_json(raw, "anydesk")
                    if ad:
                        ra_data["anydesk_id"] = ad.get("anydesk_id")
                        ra_data["anydesk_alias"] = ad.get("anydesk_alias", "")
                        ra_data["anydesk_version"] = ad.get("anydesk_version", "")
                except Exception:
                    pass
                try:
                    raw = _run_ps(session, _ps_ultravnc())
                    uv = _safe_json(raw, "ultravnc")
                    if uv:
                        ra_data["ultravnc_installed"] = uv.get("installed", False)
                        ra_data["ultravnc_port"] = uv.get("port")
                        ra_data["ultravnc_version"] = uv.get("version", "")
                except Exception:
                    pass
                existing_ra = db.query(HostRemoteAccess).filter(HostRemoteAccess.host_id == host_id_val).first()
                if existing_ra:
                    for k, v in ra_data.items():
                        setattr(existing_ra, k, v)
                else:
                    db.add(HostRemoteAccess(host_id=host_id_val, **ra_data))
                db.commit()
                total_success += 1
            except Exception:
                total_fail += 1

            # Scan history
            db.add(ScanHistory(host_id=host_id_val, hostname=host.hostname, status="success",
                               started_at=now, finished_at=datetime.utcnow()))

            log_activity(db, activity_type="collector_checkin", hostname=host.hostname, host_id=host_id_val,
                         status="success" if total_fail == 0 else "partial",
                         message=f"Coleta auto: {host.hostname} — {total_success} OK, {total_fail} falha(s)",
                         details={"success": total_success, "fail": total_fail},
                         source="system")
            db.commit()
            stats["collected"] += 1

        # Log geral da rodada
        msg = f"Coleta automática concluída: {stats['collected']} coletados, {stats['offline']} offline, {stats['errors']} erros"
        log_activity(db, activity_type="collector_checkin", status="success", message=msg,
                     details=stats, source="system")
        db.commit()
        print(f"[collect-auto] {msg}")

    except Exception as e:
        print(f"[collect-auto] Erro geral: {e}")
        try:
            log_activity(db, activity_type="collector_checkin", status="error",
                         message=f"Coleta automática falhou: {str(e)[:200]}", source="system")
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _run_auto_network_discovery():
    """Executa network discovery automático e importa novos hosts."""
    import json as json_mod
    import socket
    import ipaddress
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.core.database import Host, HostNetwork, Setting
    from app.api.activity import log_activity

    db = SessionLocal()
    try:
        # Lê configs
        settings = {}
        for r in db.query(Setting).all():
            settings[f"{r.category}.{r.key}"] = r.value

        subnets_raw = settings.get("network.subnets", "[]")
        ssh_port = int(settings.get("network.ssh_port", "22"))
        timeout = float(settings.get("network.timeout", "2"))
        max_workers = int(settings.get("network.max_workers", "100"))
        exclude_raw = settings.get("network.exclude_ips", "[]")
        auto_import = settings.get("network.auto_import", "true").strip().lower() in ("true", "1", "yes")

        try:
            subnets = json_mod.loads(subnets_raw) if subnets_raw else []
        except Exception:
            subnets = []
        try:
            exclude_ips = set(json_mod.loads(exclude_raw)) if exclude_raw else set()
        except Exception:
            exclude_ips = set()

        if not subnets:
            return  # Sem sub-redes configuradas, pula silenciosamente

        # Gera IPs
        all_ips = []
        for subnet in subnets:
            try:
                network = ipaddress.ip_network(subnet.strip(), strict=False)
                for ip in network.hosts():
                    ip_str = str(ip)
                    if ip_str not in exclude_ips:
                        all_ips.append(ip_str)
            except ValueError:
                continue

        if not all_ips:
            return

        print(f"[net-discovery-auto] Escaneando {len(all_ips)} IPs em {len(subnets)} sub-rede(s)...")

        # Funções auxiliares inline
        def grab_banner(ip, port=ssh_port, tout=timeout):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(tout)
                sock.connect((ip, port))
                banner = sock.recv(256).decode("utf-8", errors="ignore").strip()
                sock.close()
                return banner if banner else None
            except (socket.timeout, socket.error, ConnectionRefusedError, OSError):
                return None

        def resolve_hostname(ip):
            try:
                hostname, _, _ = socket.gethostbyaddr(ip)
                return hostname.split(".")[0].upper()
            except (socket.herror, socket.gaierror, OSError):
                return None

        # Scan paralelo
        found = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for ip in all_ips:
                futures[executor.submit(grab_banner, ip)] = ip
            for future in as_completed(futures):
                try:
                    banner = future.result()
                    if banner:
                        ip = futures[future]
                        hostname = resolve_hostname(ip)
                        found.append({"ip": ip, "hostname": hostname, "ssh_banner": banner})
                except Exception:
                    pass

        # Verifica quais já existem
        existing_hostnames = {h.hostname.upper() for h in db.query(Host).all()}
        existing_ips = set()
        for r in db.query(HostNetwork.ip).filter(HostNetwork.ip.isnot(None)).all():
            if r.ip:
                existing_ips.add(r.ip)

        new_hosts = []
        for h in found:
            hostname = h.get("hostname") or ""
            ip = h["ip"]
            if hostname.upper() not in existing_hostnames and ip not in existing_ips:
                new_hosts.append(h)

        print(f"[net-discovery-auto] {len(found)} hosts com SSH, {len(new_hosts)} novos")

        # Auto-importa se habilitado
        if auto_import and new_hosts:
            now = datetime.utcnow()
            imported = 0
            for h in new_hosts:
                hostname = (h.get("hostname") or h["ip"]).strip().upper()
                if not hostname:
                    continue
                existing = db.query(Host).filter(Host.hostname == hostname).first()
                if existing:
                    continue
                host = Host(hostname=hostname, status="unknown", so_type="linux", last_seen=None)
                db.add(host)
                db.flush()
                if h["ip"]:
                    net = HostNetwork(host_id=host.id, ip=h["ip"], updated_at=now)
                    db.add(net)
                imported += 1
            db.commit()

            log_activity(db,
                activity_type="discovery_run",
                status="success",
                message=f"Network Discovery auto: {len(found)} com SSH, {imported} novos importados",
                details={"total_ssh": len(found), "imported": imported, "scanned": len(all_ips), "auto": True},
                source="system",
            )
            db.commit()
            print(f"[net-discovery-auto] {imported} novos hosts Linux importados")
        elif new_hosts:
            # Só loga se encontrou novos mas não importou
            log_activity(db,
                activity_type="discovery_run",
                status="success",
                message=f"Network Discovery auto: {len(found)} com SSH, {len(new_hosts)} novos (auto-import desabilitado)",
                details={"total_ssh": len(found), "new": len(new_hosts), "scanned": len(all_ips), "auto": True},
                source="system",
            )
            db.commit()
        else:
            # Nenhum novo, log discreto
            log_activity(db,
                activity_type="discovery_run",
                status="success",
                message=f"Network Discovery auto: {len(found)} com SSH, nenhum novo",
                details={"total_ssh": len(found), "new": 0, "scanned": len(all_ips), "auto": True},
                source="system",
            )
            db.commit()

    except Exception as e:
        print(f"[net-discovery-auto] Erro: {e}")
        try:
            from app.api.activity import log_activity
            log_activity(db, activity_type="discovery_run", status="error",
                         message=f"Network Discovery auto falhou: {str(e)[:200]}", source="system")
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _run_auto_ad_discovery():
    """Executa AD discovery automático e importa novos hosts."""
    import json as json_mod
    from app.core.database import Host, Setting
    from app.api.activity import log_activity

    db = SessionLocal()
    try:
        # Lê configs do AD
        settings = {}
        for r in db.query(Setting).all():
            settings[f"{r.category}.{r.key}"] = r.value

        server = settings.get("ad.server", "")
        bind_dn = settings.get("ad.bind_dn", "")
        password = settings.get("ad.password", "")
        base_dn = settings.get("ad.base_dn", "")
        search_filter = settings.get("ad.search_filter", "(objectClass=computer)")
        ou_list_raw = settings.get("ad.ou_list", "[]")
        auto_import = settings.get("ad.auto_import", "true").strip().lower() in ("true", "1", "yes")

        try:
            ou_list = json_mod.loads(ou_list_raw) if ou_list_raw else []
        except Exception:
            ou_list = []

        if not all([server, bind_dn, password, base_dn]):
            return  # AD não configurado, pula silenciosamente

        # Importa ldap3
        try:
            from ldap3 import Server, Connection, ALL, SUBTREE
        except ImportError:
            print("[ad-discovery-auto] Biblioteca ldap3 não instalada")
            return

        print(f"[ad-discovery-auto] Consultando AD ({server})...")

        # Conecta
        try:
            srv = Server(server, get_info=ALL, connect_timeout=10)
            conn = Connection(srv, user=bind_dn, password=password, auto_bind=True, read_only=True)
        except Exception as e:
            print(f"[ad-discovery-auto] Falha ao conectar ao AD: {e}")
            return

        # Busca computadores
        search_bases = ou_list if ou_list else [base_dn]
        ad_hosts = []

        for base in search_bases:
            try:
                conn.search(
                    search_base=base,
                    search_filter=search_filter,
                    search_scope=SUBTREE,
                    attributes=["cn", "dNSHostName", "name", "operatingSystem"],
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

        # Verifica quais já existem
        existing = {h.hostname.upper() for h in db.query(Host).all()}
        new_hosts = [h for h in unique if h["hostname"] not in existing]

        print(f"[ad-discovery-auto] {len(unique)} computadores no AD, {len(new_hosts)} novos")

        # Auto-importa se habilitado
        if auto_import and new_hosts:
            imported = 0
            for h in new_hosts:
                hn = h["hostname"].strip().upper()
                if not hn:
                    continue
                if db.query(Host).filter(Host.hostname == hn).first():
                    continue
                host = Host(hostname=hn, status="unknown", so_type="unknown", last_seen=None)
                db.add(host)
                imported += 1
            db.commit()

            log_activity(db,
                activity_type="discovery_run",
                status="success",
                message=f"AD Discovery auto: {len(unique)} no AD, {imported} novos importados",
                details={"total": len(unique), "imported": imported, "new": len(new_hosts), "auto": True, "source": "ad"},
                source="system",
            )
            db.commit()
            print(f"[ad-discovery-auto] {imported} novos hosts importados")
        elif new_hosts:
            log_activity(db,
                activity_type="discovery_run",
                status="success",
                message=f"AD Discovery auto: {len(unique)} no AD, {len(new_hosts)} novos (auto-import desabilitado)",
                details={"total": len(unique), "new": len(new_hosts), "auto": True, "source": "ad"},
                source="system",
            )
            db.commit()
        else:
            log_activity(db,
                activity_type="discovery_run",
                status="success",
                message=f"AD Discovery auto: {len(unique)} no AD, nenhum novo",
                details={"total": len(unique), "new": 0, "auto": True, "source": "ad"},
                source="system",
            )
            db.commit()

    except Exception as e:
        print(f"[ad-discovery-auto] Erro: {e}")
        try:
            from app.api.activity import log_activity
            log_activity(db, activity_type="discovery_run", status="error",
                         message=f"AD Discovery auto falhou: {str(e)[:200]}", source="system")
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


async def _ad_discovery_loop():
    """Background task: AD discovery automático 1x a cada 3 dias."""
    global _next_ad_discovery_at

    _next_ad_discovery_at = datetime.utcnow() + timedelta(seconds=AD_DISCOVERY_STARTUP_DELAY)
    await asyncio.sleep(AD_DISCOVERY_STARTUP_DELAY)

    while True:
        try:
            # Verifica se AD está configurado
            db = SessionLocal()
            try:
                from app.core.database import Setting
                server = db.query(Setting).filter(Setting.key == "server", Setting.category == "ad").first()
                if not server or not server.value or not server.value.strip():
                    print("[ad-discovery-auto] AD não configurado. Pulando.")
                    _next_ad_discovery_at = datetime.utcnow() + timedelta(seconds=AD_DISCOVERY_INTERVAL)
                    await asyncio.sleep(AD_DISCOVERY_INTERVAL)
                    continue
            finally:
                db.close()

            await asyncio.wait_for(
                asyncio.to_thread(_run_auto_ad_discovery),
                timeout=600  # 10 min max
            )
        except asyncio.TimeoutError:
            print("[ad-discovery-auto] Timeout (10min) — scan abortado")
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[ad-discovery-auto] Erro no loop: {e}")

        _next_ad_discovery_at = datetime.utcnow() + timedelta(seconds=AD_DISCOVERY_INTERVAL)
        await asyncio.sleep(AD_DISCOVERY_INTERVAL)


async def _net_discovery_loop():
    """Background task: network discovery automático 1x por dia."""
    global _next_net_discovery_at

    _next_net_discovery_at = datetime.utcnow() + timedelta(seconds=NET_DISCOVERY_STARTUP_DELAY)
    await asyncio.sleep(NET_DISCOVERY_STARTUP_DELAY)

    while True:
        try:
            # Verifica se tem sub-redes configuradas
            db = SessionLocal()
            try:
                from app.core.database import Setting
                subnets_setting = db.query(Setting).filter(Setting.key == "subnets", Setting.category == "network").first()
                if not subnets_setting or not subnets_setting.value or subnets_setting.value.strip() in ("[]", ""):
                    print("[net-discovery-auto] Sem sub-redes configuradas. Pulando.")
                    _next_net_discovery_at = datetime.utcnow() + timedelta(seconds=NET_DISCOVERY_INTERVAL)
                    await asyncio.sleep(NET_DISCOVERY_INTERVAL)
                    continue
            finally:
                db.close()

            await asyncio.wait_for(
                asyncio.to_thread(_run_auto_network_discovery),
                timeout=1800  # 30 min max
            )
        except asyncio.TimeoutError:
            print("[net-discovery-auto] Timeout (30min) — scan abortado")
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[net-discovery-auto] Erro no loop: {e}")

        _next_net_discovery_at = datetime.utcnow() + timedelta(seconds=NET_DISCOVERY_INTERVAL)
        await asyncio.sleep(NET_DISCOVERY_INTERVAL)


async def _collect_loop():
    """Background task: coleta automática de dados via WinRM."""
    global _last_collect_log, _next_collect_at
    # Espera um pouco antes da primeira coleta (deixa o sistema estabilizar)
    _next_collect_at = datetime.utcnow() + timedelta(seconds=COLLECT_STARTUP_DELAY)
    await asyncio.sleep(COLLECT_STARTUP_DELAY)

    while True:
        try:
            # Lê intervalo do banco
            cfg = _get_collect_settings()
            interval = cfg["interval_hours"] * 3600 if cfg else COLLECT_INTERVAL

            # Executa coleta se tiver credenciais — RODA EM THREAD POOL
            # Timeout total: 2 minutos por host, máximo 3 horas para tudo
            if cfg:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(_run_auto_collect),
                        timeout=10800  # 3 horas max
                    )
                except asyncio.TimeoutError:
                    print("[collect-auto] Timeout geral (3h) — coleta abortada")
            else:
                print("[collect-auto] Credenciais WinRM não configuradas. Pulando coleta automática.")

            _next_collect_at = datetime.utcnow() + timedelta(seconds=interval)
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[collect-auto] Erro no loop: {e}")
            _next_collect_at = datetime.utcnow() + timedelta(seconds=300)
            await asyncio.sleep(300)  # tenta de novo em 5min


# ---- Lifespan ----

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Cria tabelas, faz seed do admin, aplica DNS e inicia tasks de background."""
    Base.metadata.create_all(bind=engine)
    _migrate_db()
    _seed_admin()
    _apply_dns_from_db()

    # Inicia background ping task
    global _ping_task, _collect_task, _net_discovery_task, _ad_discovery_task
    _ping_task = asyncio.create_task(_ping_loop())
    print(f"[ping-sweep] Iniciado — intervalo: {PING_INTERVAL}s")

    # Inicia background collect task
    _collect_task = asyncio.create_task(_collect_loop())
    print(f"[collect-auto] Iniciado — intervalo padrão: {COLLECT_INTERVAL}s")

    # Inicia background network discovery task
    _net_discovery_task = asyncio.create_task(_net_discovery_loop())
    print(f"[net-discovery-auto] Iniciado — intervalo: {NET_DISCOVERY_INTERVAL}s (1x por dia)")

    # Inicia background AD discovery task
    _ad_discovery_task = asyncio.create_task(_ad_discovery_loop())
    print(f"[ad-discovery-auto] Iniciado — intervalo: {AD_DISCOVERY_INTERVAL}s (1x a cada 3 dias)")

    yield

    # Cancela tasks ao desligar
    if _ping_task:
        _ping_task.cancel()
    if _collect_task:
        _collect_task.cancel()
    if _net_discovery_task:
        _net_discovery_task.cancel()
    if _ad_discovery_task:
        _ad_discovery_task.cancel()


def _migrate_db():
    """Aplica migrations pendentes (ALTER TABLE para novas colunas)."""
    import sqlalchemy

    conn = engine.connect()
    try:
        inspector = sqlalchemy.inspect(engine)
        columns = [col['name'] for col in inspector.get_columns('hosts')]

        if 'ping_status' not in columns:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE hosts ADD COLUMN ping_status VARCHAR(20) DEFAULT 'unknown'"
            ))
            print("[migration] Adicionado ping_status na tabela hosts")

        if 'last_ping' not in columns:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE hosts ADD COLUMN last_ping DATETIME DEFAULT NULL"
            ))
            print("[migration] Adicionado last_ping na tabela hosts")

        if 'so_type' not in columns:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE hosts ADD COLUMN so_type ENUM('windows','linux','unknown') DEFAULT 'unknown'"
            ))
            print("[migration] Adicionado so_type na tabela hosts")

        conn.commit()
    except Exception as e:
        print(f"[migration] Erro: {e}")
    finally:
        conn.close()

    # Cria tabela host_distro se não existir
    try:
        tables = sqlalchemy.inspect(engine).get_table_names()
        if 'host_distro' not in tables:
            HostDistro.__table__.create(bind=engine, checkfirst=True)
            print("[migration] Tabela host_distro criada")
    except Exception as e:
        print(f"[migration] Erro ao criar host_distro: {e}")


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

        # Garante settings de schedule padrão
        from app.core.database import Setting
        sched_enabled = db.query(Setting).filter(Setting.key == "enabled", Setting.category == "schedule").first()
        if not sched_enabled:
            db.add(Setting(key="enabled", value="true", category="schedule"))
        sched_interval = db.query(Setting).filter(Setting.key == "interval_hours", Setting.category == "schedule").first()
        if not sched_interval:
            db.add(Setting(key="interval_hours", value="6", category="schedule"))
        db.commit()
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
app.include_router(activity.router)


# Health check
@app.get("/api/health")
def health():
    return {"status": "ok"}


# Frontend estático (em produção serve pelo Nginx, mas facilita dev)
frontend_path = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.isdir(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
