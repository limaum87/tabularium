from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import (
    get_db, HostSoftware, HostLicense, ScanHistory, Host,
)
from app.core.security import get_current_user
from app.api.hosts import _auto_legacy_check

router = APIRouter(tags=["reports"])


@router.get("/api/software")
def list_software(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Softwares agrupados por nome com contagem de instalações."""
    rows = (
        db.query(
            HostSoftware.name,
            HostSoftware.version,
            HostSoftware.publisher,
            func.count(HostSoftware.id).label("install_count"),
        )
        .group_by(HostSoftware.name, HostSoftware.version, HostSoftware.publisher)
        .order_by(func.count(HostSoftware.id).desc())
        .all()
    )
    return [
        {"name": r.name, "version": r.version, "publisher": r.publisher, "install_count": r.install_count}
        for r in rows
    ]


@router.get("/api/licenses")
def list_licenses(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Todas as licenças detectadas por host."""
    rows = (
        db.query(HostLicense, Host.hostname)
        .join(Host, Host.id == HostLicense.host_id)
        .all()
    )
    return [
        {
            "hostname": hostname,
            "product": lic.product,
            "edition": lic.edition,
            "version": lic.version,
            "channel": lic.channel,
            "license_status": lic.license_status,
            "partial_product_key": lic.partial_product_key,
        }
        for lic, hostname in rows
    ]


@router.get("/api/scans")
def list_scans(limit: int = 50, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Histórico de coletas."""
    rows = (
        db.query(ScanHistory)
        .order_by(ScanHistory.finished_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": s.id,
            "hostname": s.hostname,
            "status": s.status,
            "message": s.message,
            "started_at": str(s.started_at) if s.started_at else None,
            "finished_at": str(s.finished_at) if s.finished_at else None,
        }
        for s in rows
    ]


@router.get("/api/os-versions")
def os_versions(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Distribuição de versões de SO (Windows editions + Linux)."""
    _auto_legacy_check(db)

    # Hosts ativos (não legados)
    active_hosts = db.query(Host).filter(Host.is_legacy == False).all()
    active_host_ids = [h.id for h in active_hosts]

    result = []

    # Windows — agrupa por edition + version da licença (apenas hosts com so_type=windows)
    windows_host_ids = [h.id for h in active_hosts if h.so_type == "windows"]
    if windows_host_ids:
        rows = (
            db.query(
                HostLicense.edition,
                HostLicense.version,
                func.count(HostLicense.id).label("count"),
            )
            .filter(
                HostLicense.product == "windows",
                HostLicense.host_id.in_(windows_host_ids),
            )
            .group_by(HostLicense.edition, HostLicense.version)
            .order_by(func.count(HostLicense.id).desc())
            .all()
        )
        for r in rows:
            label = r.edition or "Desconhecido"
            if r.version:
                label = f"{label} ({r.version})"
            result.append({"os": label, "count": r.count})

        # Windows sem licença detectada (ex: máquina nova ainda não coletada)
        hosts_with_win_lic = set(
            r2.host_id for r2 in
            db.query(HostLicense.host_id)
            .filter(HostLicense.product == "windows", HostLicense.host_id.in_(windows_host_ids))
            .all()
        )
        unlicensed_windows = len(windows_host_ids) - len(hosts_with_win_lic)
        if unlicensed_windows > 0:
            result.append({"os": "Windows (sem licença detectada)", "count": unlicensed_windows})

    # Linux — usa so_type diretamente
    linux_count = sum(1 for h in active_hosts if h.so_type == "linux")
    if linux_count > 0:
        result.append({"os": "Linux", "count": linux_count})

    # Unknown — SO não identificado
    unknown_count = sum(1 for h in active_hosts if h.so_type == "unknown")
    if unknown_count > 0:
        result.append({"os": "Desconhecido", "count": unknown_count})

    return result


@router.get("/api/compliance")
def compliance(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Resumo de compliance de licenças."""
    _auto_legacy_check(db)

    active_hosts = db.query(Host).filter(Host.is_legacy == False)
    total_hosts = active_hosts.count()
    online_hosts = active_hosts.filter(Host.status == "online").count()
    legacy_hosts = db.query(Host).filter(Host.is_legacy == True).count()

    # Para licenças, filtra apenas hosts ativos
    active_host_ids = [h.id for h in active_hosts.all()]

    windows_licensed = (
        db.query(HostLicense)
        .filter(HostLicense.product == "windows", HostLicense.license_status == "Licensed", HostLicense.host_id.in_(active_host_ids))
        .count()
    )
    windows_unlicensed = (
        db.query(HostLicense)
        .filter(HostLicense.product == "windows", HostLicense.license_status != "Licensed", HostLicense.host_id.in_(active_host_ids))
        .count()
    )
    office_licensed = (
        db.query(HostLicense)
        .filter(HostLicense.product == "office", HostLicense.license_status == "Licensed", HostLicense.host_id.in_(active_host_ids))
        .count()
    )
    office_unlicensed = (
        db.query(HostLicense)
        .filter(HostLicense.product == "office", HostLicense.license_status != "Licensed", HostLicense.host_id.in_(active_host_ids))
        .count()
    )

    return {
        "total_hosts": total_hosts,
        "online_hosts": online_hosts,
        "offline_hosts": total_hosts - online_hosts,
        "legacy_hosts": legacy_hosts,
        "windows": {"licensed": windows_licensed, "unlicensed": windows_unlicensed},
        "office": {"licensed": office_licensed, "unlicensed": office_unlicensed},
    }
