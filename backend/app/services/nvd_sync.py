"""Sincronização KB↔CVE via API MSRC CVRF (sem chave, endpoint público).

Baixa boletins mensais CVRF, extrai CVEs e os KBs que os corrigem,
cacheando nas tabelas cves e kb_cves.
"""
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import requests

from app.core.database import Setting

NS = {
    "cvrf": "http://www.icasi.org/CVRF/schema/cvrf/1.1",
    "vuln": "http://www.icasi.org/CVRF/schema/vuln/1.1",
    "prod": "http://www.icasi.org/CVRF/schema/prod/1.1",
}

CVRF_URL = "https://api.msrc.microsoft.com/cvrf/{month}?api-version=2021-08-25"
KB_RE = re.compile(r"KB\d{6,}", re.IGNORECASE)

_sync_lock = threading.Lock()
_sync_running = False


def _months_back(n):
    """Lista de strings 'yyyy-Mon' dos últimos n meses (mais recente primeiro)."""
    months = []
    d = datetime.utcnow().replace(day=1)
    for _ in range(n):
        months.append(d.strftime("%Y-%b"))
        d = (d - timedelta(days=1)).replace(day=1)
    return months


def _severity(score):
    if score is None:
        return "None"
    if score >= 9.0:
        return "Critical"
    if score >= 7.0:
        return "High"
    if score >= 4.0:
        return "Medium"
    return "Low"


def _parse_cvrf(xml_text):
    """Extrai [(cve_id, title, base_score, set_kbs)] de um doc CVRF."""
    root = ET.fromstring(xml_text)
    out = []
    for v in root.findall("vuln:Vulnerability", NS):
        cve_id = v.findtext("vuln:CVE", default="", namespaces=NS)
        if not cve_id:
            continue
        title = v.findtext("vuln:Title", default="", namespaces=NS) or ""
        # CVSS: pega o maior BaseScore dos ScoreSets (v3 > v2 quando ambos)
        score = None
        for el in v.iter("{http://www.icasi.org/CVRF/schema/vuln/1.1}BaseScore"):
            try:
                s = float(el.text)
                score = s if score is None else max(score, s)
            except (ValueError, TypeError, AttributeError):
                pass
        # KBs: pega todas as menções KB\d+ no subtree do CVE
        kbs = set()
        for el in v.iter():
            if el.text:
                for m in KB_RE.findall(el.text):
                    kbs.add(m.upper())
        if kbs:
            out.append((cve_id, title[:490], score, kbs))
    return out


def is_running():
    return _sync_running


def sync_cves(months_back=24, on_step=None, request_timeout=60):
    """Sincroniza CVEs dos últimos N meses. Bloqueante — rodar em thread.

    Retorna dict com estatísticas.
    """
    global _sync_running

    from app.core.database import SessionLocal, Cve, KbCve

    with _sync_lock:
        if _sync_running:
            return {"ok": False, "error": "Sync já em andamento"}
        _sync_running = True

    db = SessionLocal()
    stats = {"months": 0, "cves": 0, "new_cves": 0, "mappings": 0, "errors": []}
    try:
        def _notify(msg):
            if on_step:
                try:
                    on_step(msg)
                except Exception:
                    pass

        # Settings
        months_setting = db.query(Setting).filter(Setting.key == "months_back", Setting.category == "vulns").first()
        if months_setting and months_setting.value and months_setting.value.isdigit():
            months_back = max(1, int(months_setting.value))

        seen = {c.cve_id for c in db.query(Cve.cve_id).all()}

        try:
            for month in _months_back(months_back):
                _notify(f"Baixando boletim {month}...")
                r = requests.get(CVRF_URL.format(month=month), timeout=request_timeout,
                                 headers={"User-Agent": "Tabularium/1.0"})
                if r.status_code == 404:
                    continue  # mês sem boletim
                r.raise_for_status()
                parsed = _parse_cvrf(r.text)
                stats["months"] += 1

                for cve_id, title, score, kbs in parsed:
                    stats["cves"] += 1
                    if cve_id not in seen:
                        db.add(Cve(
                            cve_id=cve_id, title=title, cvss_score=score,
                            severity=_severity(score), published=month,
                        ))
                        seen.add(cve_id)
                        stats["new_cves"] += 1
                    for kb in kbs:
                        exists = db.query(KbCve).filter(KbCve.kb == kb, KbCve.cve_id == cve_id).first()
                        if not exists:
                            db.add(KbCve(kb=kb, cve_id=cve_id))
                            stats["mappings"] += 1
                db.commit()
                _notify(f"✓ {month}: {len(parsed)} CVE(s)")
        except Exception as e:
            db.rollback()
            stats["errors"].append(str(e)[:300])
            _notify(f"✗ Erro: {str(e)[:100]}")

        # Registra última sincronização
        last = db.query(Setting).filter(Setting.key == "last_sync", Setting.category == "vulns").first()
        now_str = datetime.utcnow().isoformat()
        if last:
            last.value = now_str
        else:
            db.add(Setting(key="last_sync", value=now_str, category="vulns"))
        db.commit()
        stats["ok"] = True
        _notify(f"Sync concluído: {stats['new_cves']} CVEs novos, {stats['mappings']} mapeamentos KB↔CVE")
        return stats
    finally:
        db.close()
        with _sync_lock:
            _sync_running = False
