"""Tabularium Collector — Módulo WinRM."""

import json

import winrm

from collector.logger import get_logger
from collector.scripts.powershell_scripts import (
    ps_hardware, ps_disks, ps_network,
    ps_windows_license, ps_office_license, ps_software,
    ps_hotfixes, ps_pending_updates,
)

log = get_logger()


class WinRMCollector:
    """Conecta a um host Windows via WinRM e coleta dados via PowerShell."""

    def __init__(self, cfg):
        winrm_cfg = cfg.get("winrm", {})
        self.username = winrm_cfg.get("username", "")
        self.password = winrm_cfg.get("password", "")
        self.scheme = winrm_cfg.get("scheme", "http")
        self.port = winrm_cfg.get("port", 5985)
        self.timeout = winrm_cfg.get("timeout", 30)
        self.verify_ssl = winrm_cfg.get("verify_ssl", False)

    def _connect(self, hostname, operation_timeout_sec=60, read_timeout_sec=90):
        """Cria sessão WinRM com o host."""
        endpoint = f"{self.scheme}://{hostname}:{self.port}"
        session = winrm.Session(
            endpoint,
            auth=(self.username, self.password),
            transport="ntlm",
            server_cert_validation="ignore" if not self.verify_ssl else "validate",
            operation_timeout_sec=operation_timeout_sec,
            read_timeout_sec=read_timeout_sec,
        )
        return session

    def _run_script(self, session, script):
        """Executa um script PowerShell e retorna o stdout como string."""
        result = session.run_ps(script)
        if result.status_code != 0:
            stderr = result.std_err.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"PowerShell error: {stderr[:200]}")
        return result.std_out.decode("utf-8", errors="replace").strip()

    def _safe_json(self, raw, context=""):
        """Tenta fazer parse do JSON. Retorna dict/list ou None."""
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            log.warning(f"  JSON inválido ({context}): {e}")
            return None

    def collect(self, hostname):
        """Coleta todos os dados de um host. Retorna dict pronto para o checkin."""
        log.info(f"  Coletando dados de {hostname}...")
        session = self._connect(hostname)

        data = {"hostname": hostname}

        # Hardware
        try:
            raw = self._run_script(session, ps_hardware())
            data["hardware"] = self._safe_json(raw, "hardware")
        except Exception as e:
            log.warning(f"  ⚠ hardware: {e}")
            data["hardware"] = None

        # Discos
        try:
            raw = self._run_script(session, ps_disks())
            parsed = self._safe_json(raw, "disks")
            data["disks"] = parsed if isinstance(parsed, list) else [parsed] if parsed else []
        except Exception as e:
            log.warning(f"  ⚠ discos: {e}")
            data["disks"] = []

        # Rede
        try:
            raw = self._run_script(session, ps_network())
            parsed = self._safe_json(raw, "network")
            data["network"] = parsed if isinstance(parsed, list) else [parsed] if parsed else []
        except Exception as e:
            log.warning(f"  ⚠ rede: {e}")
            data["network"] = []

        # Licenças — sessão com timeout maior
        lic_session = self._connect(hostname, operation_timeout_sec=120, read_timeout_sec=180)
        licenses = []
        for ps_func, label in [(ps_windows_license, "windows"), (ps_office_license, "office")]:
            try:
                raw = self._run_script(lic_session, ps_func())
                parsed = self._safe_json(raw, f"license-{label}")
                if parsed:
                    if isinstance(parsed, list):
                        licenses.extend(parsed)
                    else:
                        licenses.append(parsed)
            except Exception as e:
                log.warning(f"  ⚠ licença {label}: {e}")
        data["licenses"] = licenses

        # Software
        try:
            raw = self._run_script(session, ps_software())
            parsed = self._safe_json(raw, "software")
            data["software"] = parsed if isinstance(parsed, list) else [parsed] if parsed else []
        except Exception as e:
            log.warning(f"  ⚠ software: {e}")
            data["software"] = []

        # Patches (KBs + updates pendentes)
        try:
            hf_session = self._connect(hostname, operation_timeout_sec=30, read_timeout_sec=40)
            raw = self._run_script(hf_session, ps_hotfixes())
            parsed = self._safe_json(raw, "hotfixes")
            data["patches"] = parsed if isinstance(parsed, list) else [parsed] if parsed else []
        except Exception as e:
            log.warning(f"  ⚠ hotfixes: {e}")
            data["patches"] = None

        try:
            wu_session = self._connect(hostname, operation_timeout_sec=60, read_timeout_sec=360)
            raw = self._run_script(wu_session, ps_pending_updates())
            parsed = self._safe_json(raw, "pending-updates") or {}
            if parsed.get("ok"):
                data["pending_updates"] = parsed.get("pending", [])
                data["patch_status"] = {"wu_last_success": parsed.get("wu_last_success")}
            else:
                data["pending_updates"] = []
                data["patch_status"] = {"last_error": (parsed.get("error") or "")[:500]}
        except Exception as e:
            log.warning(f"  ⚠ pending updates: {e}")
            data["pending_updates"] = []
            data["patch_status"] = {"last_error": str(e)[:500]}

        log.info(f"  ✓ {hostname}: hw={bool(data['hardware'])} discos={len(data['disks'])} rede={len(data['network'])} lic={len(data['licenses'])} sw={len(data['software'])} patches={len(data.get('patches') or [])} pendentes={len(data.get('pending_updates') or [])}")
        return data
