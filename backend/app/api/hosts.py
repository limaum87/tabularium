from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Header
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
