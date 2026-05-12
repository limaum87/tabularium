"""Tabularium Collector — Envio de dados para a API."""

import time
import requests

from collector.logger import get_logger

log = get_logger()


class APIClient:
    """Cliente para enviar checkins ao backend Tabularium."""

    def __init__(self, cfg):
        api_cfg = cfg.get("api", {})
        self.base_url = api_cfg.get("url", "http://localhost:8091").rstrip("/")
        self.token = api_cfg.get("token", "")
        self.retry_attempts = api_cfg.get("retry_attempts", 3)
        self.retry_delay = api_cfg.get("retry_delay", 5)

    def send_checkin(self, data):
        """Envia dados de um host para POST /api/hosts/checkin.

        Retorna True se sucesso, False caso contrário.
        """
        url = f"{self.base_url}/api/hosts/checkin"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

        for attempt in range(1, self.retry_attempts + 1):
            try:
                resp = requests.post(url, json=data, headers=headers, timeout=30)
                if resp.status_code in (200, 201):
                    log.debug(f"    API: checkin OK (attempt {attempt})")
                    return True
                else:
                    log.warning(f"    API: HTTP {resp.status_code} — {resp.text[:120]}")
            except requests.exceptions.Timeout:
                log.warning(f"    API: timeout (attempt {attempt}/{self.retry_attempts})")
            except requests.exceptions.ConnectionError:
                log.warning(f"    API: conexão recusada (attempt {attempt}/{self.retry_attempts})")
            except Exception as e:
                log.error(f"    API: erro inesperado: {e}")

            if attempt < self.retry_attempts:
                time.sleep(self.retry_delay)

        log.error(f"    API: falha após {self.retry_attempts} tentativas — {data.get('hostname')}")
        return False

    def send_scan_result(self, hostname, status, message=None):
        """Registra resultado de scan diretamente (offline, erro).

        Usa o mesmo endpoint de checkin com dados mínimos.
        """
        data = {
            "hostname": hostname,
            "status": status,
            "message": message,
        }
        # Marca como offline no host
        if status == "offline":
            data["hardware"] = None
        return self.send_checkin(data)
