"""Tasks em background (instalação de updates remota, etc).

Registro em memória: task_id -> task dict. A instalação roda em thread
própria; o frontend faz polling de GET /api/tasks/{id} para acompanhar.
"""
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import Host, get_db
from app.core.security import get_current_user
from app.collector.winrm_collect import _get_settings, install_host_updates
from app.api.activity import log_activity

router = APIRouter(prefix="/api", tags=["tasks"])

_tasks = {}
_lock = threading.Lock()
_MAX_TASKS = 100


def _prune():
    """Mantém no máximo _MAX_TASKS (remove as mais antigas finalizadas)."""
    with _lock:
        if len(_tasks) <= _MAX_TASKS:
            return
        finished = sorted(
            (t for t in _tasks.values() if t["status"] != "running"),
            key=lambda t: t["created_at"],
        )
        for t in finished[: len(_tasks) - _MAX_TASKS]:
            _tasks.pop(t["id"], None)


@router.post("/hosts/{host_id}/action/install-updates")
def start_install_updates(host_id: int, reboot: bool = False, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Inicia instalação de updates em background. Retorna task_id."""
    host = db.query(Host).filter(Host.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host não encontrado")
    cfg = _get_settings(db)
    if not cfg:
        raise HTTPException(status_code=400, detail="Credenciais WinRM não configuradas. Configure em Configurações → WinRM.")

    # Evita duas tasks simultâneas para o mesmo host
    with _lock:
        for t in _tasks.values():
            if t["host_id"] == host_id and t["status"] == "running":
                raise HTTPException(status_code=409, detail=f"Já existe uma instalação em andamento para {host.hostname} (task {t['id'][:8]}).")

    task_id = uuid.uuid4().hex
    task = {
        "id": task_id,
        "type": "install-updates",
        "host_id": host_id,
        "hostname": host.hostname,
        "status": "running",
        "reboot": reboot,
        "steps": [],
        "result": None,
        "error": None,
        "created_at": datetime.utcnow().isoformat(),
        "finished_at": None,
    }
    with _lock:
        _tasks[task_id] = task
    _prune()

    def worker():
        def on_step(msg):
            task["steps"].append({"at": datetime.utcnow().isoformat(), "message": msg})
        try:
            result = install_host_updates(host.hostname, cfg, on_step=on_step, reboot=reboot)
            task["result"] = result
            task["status"] = "success" if result.get("ok") else "error"
        except Exception as e:
            task["error"] = str(e)[:500]
            task["status"] = "error"
        task["finished_at"] = datetime.utcnow().isoformat()
        log_activity(db,
            activity_type="manual_collect",
            hostname=host.hostname,
            host_id=host_id,
            status="success" if task["status"] == "success" else "error",
            message=f"Update remoto (task): {task.get('result', {}).get('install_label') or task.get('error', '—')}",
            details={"task_id": task_id, "found": (task.get("result") or {}).get("found")},
            source="manual")

    threading.Thread(target=worker, daemon=True).start()
    return {"task_id": task_id, "hostname": host.hostname}


@router.get("/tasks")
def list_tasks(_=Depends(get_current_user)):
    with _lock:
        tasks = sorted(_tasks.values(), key=lambda t: t["created_at"], reverse=True)
        import copy
        return [copy.deepcopy(t) for t in tasks]


@router.get("/tasks/{task_id}")
def get_task(task_id: str, _=Depends(get_current_user)):
    with _lock:
        task = _tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task não encontrada")
        import copy
        return copy.deepcopy(task)


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, _=Depends(get_current_user)):
    with _lock:
        task = _tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task não encontrada")
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="Não é possível remover uma task em execução")
        del _tasks[task_id]
    return {"ok": True}
