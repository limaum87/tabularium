import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db, ActivityLog, Host, ScanHistory
from app.core.security import get_current_user

router = APIRouter(prefix="/api/activity", tags=["activity"])


# ---- Helper para registrar atividades (usado por outros módulos) ----

def log_activity(
    db: Session,
    activity_type: str,
    hostname: str | None = None,
    host_id: int | None = None,
    status: str | None = None,
    message: str | None = None,
    details: dict | None = None,
    source: str | None = None,
):
    """Registra uma atividade no log. Não faz commit — deixe o chamador fazer."""
    entry = ActivityLog(
        activity_type=activity_type,
        hostname=hostname,
        host_id=host_id,
        status=status,
        message=message,
        details=json.dumps(details, ensure_ascii=False) if details else None,
        source=source,
    )
    db.add(entry)


# ---- Listagem ----

@router.get("")
def list_activity(
    type: str | None = Query(None, alias="type"),
    source: str | None = Query(None, alias="source"),
    search: str | None = Query(None, alias="search"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """Lista atividades com filtros."""
    q = db.query(ActivityLog)

    if type:
        q = q.filter(ActivityLog.activity_type == type)
    if source:
        q = q.filter(ActivityLog.source == source)
    if search:
        q = q.filter(
            (ActivityLog.hostname.ilike(f"%{search}%"))
            | (ActivityLog.message.ilike(f"%{search}%"))
        )

    total = q.count()
    rows = (
        q.order_by(ActivityLog.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [
            {
                "id": r.id,
                "activity_type": r.activity_type,
                "hostname": r.hostname,
                "host_id": r.host_id,
                "status": r.status,
                "message": r.message,
                "details": json.loads(r.details) if r.details else None,
                "source": r.source,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.get("/summary")
def activity_summary(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Resumo das atividades — contagem por tipo e últimas execuções."""
    # Contagem por tipo
    counts = (
        db.query(ActivityLog.activity_type, func.count(ActivityLog.id))
        .group_by(ActivityLog.activity_type)
        .all()
    )
    count_map = {c[0]: c[1] for c in counts}

    # Última execução de cada tipo
    last_runs = (
        db.query(ActivityLog.activity_type, func.max(ActivityLog.created_at))
        .group_by(ActivityLog.activity_type)
        .all()
    )
    last_map = {r[0]: r[1].isoformat() if r[1] else None for r in last_runs}

    # Tipos de atividade com labels
    types = [
        {"type": "collector_checkin", "label": "Coleta Automática", "icon": "🔄"},
        {"type": "manual_collect", "label": "Coleta Manual", "icon": "🖐"},
        {"type": "discovery_run", "label": "Discovery AD", "icon": "🔍"},
        {"type": "discovery_import", "label": "Importação de Hosts", "icon": "📥"},
        {"type": "ping_sweep", "label": "Ping Sweep", "icon": "📡"},
        {"type": "ping_single", "label": "Ping Individual", "icon": "📡"},
        {"type": "winrm_test", "label": "Teste WinRM", "icon": "🔌"},
        {"type": "winrm_enable", "label": "Ativação WinRM", "icon": "⚡"},
        {"type": "host_created", "label": "Host Criado", "icon": "➕"},
    ]

    for t in types:
        t["count"] = count_map.get(t["type"], 0)
        t["last_run"] = last_map.get(t["type"])

    # Atividade dos últimos 7 dias (por dia)
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    daily = (
        db.query(
            func.date(ActivityLog.created_at).label("day"),
            ActivityLog.activity_type,
            func.count(ActivityLog.id).label("cnt"),
        )
        .filter(ActivityLog.created_at >= seven_days_ago)
        .group_by(func.date(ActivityLog.created_at), ActivityLog.activity_type)
        .order_by(func.date(ActivityLog.created_at))
        .all()
    )
    daily_stats = {}
    for d in daily:
        day_str = str(d.day)
        if day_str not in daily_stats:
            daily_stats[day_str] = {}
        daily_stats[day_str][d.activity_type] = d.cnt

    return {
        "types": types,
        "daily": daily_stats,
    }


@router.get("/export")
def export_activity(
    type: str | None = Query(None, alias="type"),
    format: str = Query("csv", regex="^(csv|json)$"),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """Exporta atividades em CSV ou JSON."""
    q = db.query(ActivityLog)
    if type:
        q = q.filter(ActivityLog.activity_type == type)

    rows = q.order_by(ActivityLog.created_at.desc()).limit(5000).all()

    if format == "json":
        import json as json_mod
        data = [
            {
                "id": r.id,
                "type": r.activity_type,
                "hostname": r.hostname,
                "status": r.status,
                "message": r.message,
                "source": r.source,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
        return StreamingResponse(
            iter([json_mod.dumps(data, indent=2, ensure_ascii=False)]),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=activity_log.json"},
        )
    else:
        import csv
        import io

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Tipo", "Hostname", "Status", "Mensagem", "Fonte", "Data/Hora"])
        for r in rows:
            writer.writerow([
                r.id,
                r.activity_type,
                r.hostname or "",
                r.status or "",
                (r.message or "")[:200],
                r.source or "",
                r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
            ])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=activity_log.csv"},
        )
