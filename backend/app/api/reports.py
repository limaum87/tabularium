from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import (
    get_db, HostSoftware, HostLicense, ScanHistory, Host,
)
from app.core.security import get_current_user

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


@router.get("/api/compliance")
def compliance(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Resumo de compliance de licenças."""
    total_hosts = db.query(Host).count()
    online_hosts = db.query(Host).filter(Host.status == "online").count()

    windows_licensed = (
        db.query(HostLicense)
        .filter(HostLicense.product == "windows", HostLicense.license_status == "Licensed")
        .count()
    )
    windows_unlicensed = (
        db.query(HostLicense)
        .filter(HostLicense.product == "windows", HostLicense.license_status != "Licensed")
        .count()
    )
    office_licensed = (
        db.query(HostLicense)
        .filter(HostLicense.product == "office", HostLicense.license_status == "Licensed")
        .count()
    )
    office_unlicensed = (
        db.query(HostLicense)
        .filter(HostLicense.product == "office", HostLicense.license_status != "Licensed")
        .count()
    )

    return {
        "total_hosts": total_hosts,
        "online_hosts": online_hosts,
        "offline_hosts": total_hosts - online_hosts,
        "windows": {"licensed": windows_licensed, "unlicensed": windows_unlicensed},
        "office": {"licensed": office_licensed, "unlicensed": office_unlicensed},
    }
