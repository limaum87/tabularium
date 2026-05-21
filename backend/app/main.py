from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os
import asyncio
import subprocess
from datetime import datetime, timedelta

from app.core.database import engine, Base, SessionLocal, User, Host
from app.core.config import settings
from app.core.security import hash_password
from app.api import auth, users, hosts, reports, settings as settings_api, discovery, activity

# ---- Background Tasks ----

PING_INTERVAL = 300  # 5 minutos
PING_LOG_INTERVAL = 3600  # log a cada 1 hora
COLLECT_INTERVAL = 21600  # 6 horas (padrão, pode ser alterado no banco)
COLLECT_STARTUP_DELAY = 60  # espera 60s antes da primeira coleta

_ping_task = None
_collect_task = None
_last_ping_log = None
_last_collect_log = None
_next_ping_at = None       # datetime da próxima execução de ping sweep
_next_collect_at = None    # datetime da próxima execução de coleta automática


async def _ping_loop():
    """Background task: faz ping em todos os hosts a cada 5 minutos."""
    global _last_ping_log, _next_ping_at
    while True:
        try:
            from datetime import datetime as dt
            _next_ping_at = dt.utcnow() + timedelta(seconds=PING_INTERVAL)
            await asyncio.sleep(PING_INTERVAL)
            should_log = False
            now_ts = asyncio.get_event_loop().time()
            if _last_ping_log is None or (now_ts - _last_ping_log) >= PING_LOG_INTERVAL:
                should_log = True
                _last_ping_log = now_ts
            _run_ping_sweep(log=should_log)
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
                session = _connect(target, cfg, operation_timeout_sec=60, read_timeout_sec=90)
                # Teste rápido
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


async def _collect_loop():
    """Background task: coleta automática de dados via WinRM."""
    global _last_collect_log, _next_collect_at
    # Espera um pouco antes da primeira coleta (deixa o sistema estabilizar)
    from datetime import datetime as dt
    _next_collect_at = dt.utcnow() + timedelta(seconds=COLLECT_STARTUP_DELAY)
    await asyncio.sleep(COLLECT_STARTUP_DELAY)

    while True:
        try:
            # Lê intervalo do banco
            cfg = _get_collect_settings()
            interval = cfg["interval_hours"] * 3600 if cfg else COLLECT_INTERVAL

            # Executa coleta se tiver credenciais
            if cfg:
                _run_auto_collect()
            else:
                print("[collect-auto] Credenciais WinRM não configuradas. Pulando coleta automática.")

            _next_collect_at = dt.utcnow() + timedelta(seconds=interval)
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[collect-auto] Erro no loop: {e}")
            _next_collect_at = dt.utcnow() + timedelta(seconds=300)
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
    global _ping_task, _collect_task
    _ping_task = asyncio.create_task(_ping_loop())
    print(f"[ping-sweep] Iniciado — intervalo: {PING_INTERVAL}s")

    # Inicia background collect task
    _collect_task = asyncio.create_task(_collect_loop())
    print(f"[collect-auto] Iniciado — intervalo padrão: {COLLECT_INTERVAL}s")

    yield

    # Cancela tasks ao desligar
    if _ping_task:
        _ping_task.cancel()
    if _collect_task:
        _collect_task.cancel()


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
