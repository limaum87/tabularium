"""API de Vulnerabilidades & Patch Compliance (Fase 1 — patches, Fase 2 — CVEs)."""
import threading

from sqlalchemy.orm import Session

from fastapi import APIRouter, Depends, HTTPException

from app.core.database import (
    get_db, Host, HostPatchStatus, HostPendingUpdate, HostPatch,
    HostDistro, Cve, KbCve, Setting,
)
from app.core.security import get_current_user

router = APIRouter(prefix="/api/vulnerabilities", tags=["vulnerabilities"])


@router.get("/overview")
def patch_overview(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Visão geral de compliance de patches + risco por host (estilo Tenable)."""
    hosts = db.query(Host).filter(Host.is_legacy == False).all()
    statuses = {s.host_id: s for s in db.query(HostPatchStatus).all()}
    pending_rows = db.query(HostPendingUpdate).all()
    total_kbs = db.query(HostPatch).count()

    # CVEs por KB (para exposição a vulnerabilidades)
    kb_cves_rows = (
        db.query(KbCve.kb, Cve.cve_id, Cve.cvss_score, Cve.severity)
        .outerjoin(Cve, KbCve.cve_id == Cve.cve_id)
        .all()
    )
    kb_vulns = {}  # kb -> [(cve_id, cvss)]
    for kb, cve_id, cvss, _sev in kb_cves_rows:
        if cve_id:
            kb_vulns.setdefault(kb.upper(), []).append((cve_id, cvss or 0))

    pending_by_host = {}
    critical_by_host = {}
    reboot_by_host = {}
    for p in pending_rows:
        pending_by_host[p.host_id] = pending_by_host.get(p.host_id, 0) + 1
        if (p.severity or "").lower() == "critical":
            critical_by_host[p.host_id] = critical_by_host.get(p.host_id, 0) + 1
        if p.reboot_required:
            reboot_by_host[p.host_id] = True

    hosts_out = []
    for h in hosts:
        st = statuses.get(h.id)
        pending = pending_by_host.get(h.id, 0)
        critical = critical_by_host.get(h.id, 0)

        # Exposição a CVEs: KBs pendentes × mapeamento MSRC
        cve_ids = set()
        cve_scores = []
        for p in pending_rows:
            if p.host_id == h.id and p.kb:
                for cve_id, cvss in kb_vulns.get(p.kb.upper(), []):
                    cve_ids.add(cve_id)
                    cve_scores.append(cvss)
        cve_count = len(cve_ids)
        max_cvss = max(cve_scores) if cve_scores else None
        # Risk score 0-10: soma dos CVSS das CVEs expostas, saturado em 10
        risk_score = round(min(10, sum(cve_scores) / 25), 1) if cve_scores else 0

        # Score de compliance: 100 sem pendentes, -25 por critical (mín 0),
        # -10 por outro pending, nunca escaneado = None
        if st is None:
            score = None
        elif pending == 0:
            score = 100
        else:
            score = max(0, 100 - critical * 25 - (pending - critical) * 10)

        hosts_out.append({
            "host_id": h.id,
            "hostname": h.hostname,
            "status": h.status,
            "os_edition": st.os_edition if st else None,
            "display_version": st.display_version if st else None,
            "build": st.build if st else None,
            "wu_last_success": st.wu_last_success if st else None,
            "pending_count": pending,
            "critical_pending": critical,
            "reboot_required": reboot_by_host.get(h.id, False),
            "last_scan": st.last_scan.isoformat() if st and st.last_scan else None,
            "last_error": st.last_error if st else None,
            "score": score,
            "cve_count": cve_count,
            "max_cvss": max_cvss,
            "risk_score": risk_score if st is not None else None,
        })

    # Ordena: maior risco primeiro (sem scan no fim)
    hosts_out.sort(key=lambda x: (x["risk_score"] is None, -(x["risk_score"] or 0)))

    scanned = sum(1 for h in hosts_out if h["last_scan"])
    return {
        "summary": {
            "total_hosts": len(hosts_out),
            "scanned": scanned,
            "never_scanned": len(hosts_out) - scanned,
            "compliant": sum(1 for h in hosts_out if h["score"] == 100),
            "outdated": sum(1 for h in hosts_out if h["score"] is not None and h["score"] < 100),
            "total_pending": sum(h["pending_count"] for h in hosts_out),
            "total_critical": sum(h["critical_pending"] for h in hosts_out),
            "reboot_required": sum(1 for h in hosts_out if h["reboot_required"]),
            "avg_score": round(
                sum(h["score"] for h in hosts_out if h["score"] is not None)
                / max(1, sum(1 for h in hosts_out if h["score"] is not None)), 1),
            "high_risk_hosts": sum(1 for h in hosts_out if (h["risk_score"] or 0) >= 7),
            "total_exposed_cves": sum(h["cve_count"] for h in hosts_out),
        },
        "hosts": hosts_out,
    }


@router.get("/report.csv")
def report_csv(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Relatório CSV de compliance + risco (para auditoria)."""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    overview = patch_overview(db=db, _=_)

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([
        "hostname", "so", "versao", "build", "score_patch", "pendentes",
        "criticos", "cves_expostas", "cvss_max", "risk_score", "reboot",
        "ultimo_wu_ok", "ultimo_scan",
    ])
    for h in overview["hosts"]:
        w.writerow([
            h["hostname"], h["os_edition"] or "", h["display_version"] or "",
            h["build"] or "", h["score"] if h["score"] is not None else "",
            h["pending_count"], h["critical_pending"], h["cve_count"],
            h["max_cvss"] if h["max_cvss"] is not None else "",
            h["risk_score"] if h["risk_score"] is not None else "",
            "sim" if h["reboot_required"] else "",
            h["wu_last_success"] or "", h["last_scan"] or "",
        ])

    buf.seek(0)
    from datetime import datetime as _dt
    filename = f"tabularium-patch-report-{_dt.utcnow().strftime('%Y%m%d-%H%M')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/hosts/{host_id}")
def host_vulnerabilities(host_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Detalhe de patches + updates pendentes de um host."""
    host = db.query(Host).filter(Host.id == host_id).first()
    if not host:
        return {"detail": "Host não encontrado"}
    status = db.query(HostPatchStatus).filter(HostPatchStatus.host_id == host_id).first()
    pending = db.query(HostPendingUpdate).filter(HostPendingUpdate.host_id == host_id).all()
    kbs = db.query(HostPatch).filter(HostPatch.host_id == host_id).order_by(HostPatch.installed_on.desc()).all()
    return {
        "hostname": host.hostname,
        "status": {
            "os_edition": status.os_edition if status else None,
            "display_version": status.display_version if status else None,
            "build": status.build if status else None,
            "wu_last_success": status.wu_last_success if status else None,
            "last_scan": status.last_scan.isoformat() if status and status.last_scan else None,
            "last_error": status.last_error if status else None,
        },
        "pending_updates": [
            {"kb": p.kb, "title": p.title, "severity": p.severity, "reboot_required": p.reboot_required}
            for p in pending
        ],
        "installed_kbs": [
            {"kb": k.kb, "description": k.description, "installed_on": k.installed_on}
            for k in kbs
        ],
    }


# ---- Fase 2: CVEs (MSRC) ----

@router.post("/sync-cves")
def sync_cves_endpoint(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Dispara sincronização CVE↔KB (MSRC) em background thread."""
    from app.services import nvd_sync
    from app.core.database import SessionLocal

    if nvd_sync.is_running():
        return {"detail": "Sync já em andamento", "running": True}

    def _run():
        nvd_sync.sync_cves()

    threading.Thread(target=_run, daemon=True).start()
    return {"detail": "Sincronização iniciada", "running": True}


@router.get("/sync-status")
def sync_status(db: Session = Depends(get_db), _=Depends(get_current_user)):
    from app.services import nvd_sync

    last = db.query(Setting).filter(Setting.key == "last_sync", Setting.category == "vulns").first()
    months = db.query(Setting).filter(Setting.key == "months_back", Setting.category == "vulns").first()
    return {
        "running": nvd_sync.is_running(),
        "last_sync": last.value if last else None,
        "months_back": int(months.value) if months and months.value and months.value.isdigit() else 24,
        "total_cves": db.query(Cve).count(),
        "total_mappings": db.query(KbCve).count(),
    }


@router.get("/cves")
def list_cves(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Lista CVEs que afetam hosts com updates pendentes (estilo Tenable)."""
    # KBs pendentes por host
    pending = db.query(HostPendingUpdate.host_id, HostPendingUpdate.kb).all()
    kb_hosts = {}  # kb -> [host_id]
    for host_id, kb in pending:
        if kb:
            kb_hosts.setdefault(kb.upper(), []).append(host_id)

    hosts = {h.id: h for h in db.query(Host).all()}
    mappings = db.query(KbCve).all()
    cves_db = {c.cve_id: c for c in db.query(Cve).all()}

    # cve_id -> {hosts set, kbs set}
    cve_hosts = {}
    for m in mappings:
        affected = kb_hosts.get(m.kb.upper(), [])
        if not affected:
            continue
        entry = cve_hosts.setdefault(m.cve_id, {"hosts": set(), "kbs": set()})
        entry["hosts"].update(affected)
        entry["kbs"].add(m.kb)

    out = []
    for cve_id, info in cve_hosts.items():
        c = cves_db.get(cve_id)
        out.append({
            "cve_id": cve_id,
            "title": c.title if c else None,
            "cvss_score": c.cvss_score if c else None,
            "severity": c.severity if c else "None",
            "published": c.published if c else None,
            "kbs": sorted(info["kbs"]),
            "affected_hosts": len(info["hosts"]),
            "hostnames": sorted(hosts[h].hostname for h in info["hosts"] if h in hosts),
        })

    # Ordena por CVSS desc
    out.sort(key=lambda x: (-(x["cvss_score"] or 0), x["cve_id"]))

    return {
        "summary": {
            "total": len(out),
            "critical": sum(1 for c in out if c["severity"] == "Critical"),
            "high": sum(1 for c in out if c["severity"] == "High"),
            "medium": sum(1 for c in out if c["severity"] == "Medium"),
            "affected_hosts": len({h for c in out for h in c.get("hostnames", [])}),
        },
        "cves": out,
    }
