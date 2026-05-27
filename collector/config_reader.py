"""Tabularium Collector — Leitura de configuração.

Suporta duas fontes (em ordem de prioridade):
1. API Backend (GET /api/settings) — recomendado, configurado pelo frontend
2. config.yaml local — fallback para quando API estiver offline
"""

import os
import yaml
import requests


_config = None


def load_config(path=None):
    """Carrega configuração. Prioridade: API > config.yaml.

    Retorna dict com estrutura compatível com os módulos do collector.
    """
    global _config

    # Tenta ler da API primeiro
    api_url = os.environ.get("COLLECTOR_API_URL", "http://localhost:8091")
    api_token = os.environ.get("COLLECTOR_API_TOKEN", "")

    try:
        headers = {}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        resp = requests.get(f"{api_url}/api/settings", headers=headers, timeout=10)
        if resp.status_code == 200:
            remote = resp.json()
            _config = _normalize_api_settings(remote, api_url, api_token)
            return _config
    except Exception:
        pass  # Fallback para config.yaml

    # Fallback: config.yaml local
    if path is None:
        path = os.environ.get("COLLECTOR_CONFIG", os.path.join(os.path.dirname(__file__), "config.yaml"))

    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            _config = yaml.safe_load(f)
        return _config

    # Se não tem nada, retorna config vazia
    _config = {}
    return _config


def _normalize_api_settings(remote, api_url, api_token):
    """Converte settings do banco (categorias) para estrutura esperada pelos módulos."""
    cfg = {}

    # AD / LDAP
    ad = remote.get("ad", {})
    cfg["ad"] = {
        "server": ad.get("server", ""),
        "bind_dn": ad.get("bind_dn", ""),
        "password": ad.get("password", ""),
        "base_dn": ad.get("base_dn", ""),
        "search_filter": ad.get("search_filter", "(objectClass=computer)"),
        "ou_list": ad.get("ou_list", []),
    }
    # ou_list pode vir como string JSON do banco
    if isinstance(cfg["ad"]["ou_list"], str):
        import json
        try:
            cfg["ad"]["ou_list"] = json.loads(cfg["ad"]["ou_list"])
        except Exception:
            cfg["ad"]["ou_list"] = []

    # WinRM
    winrm = remote.get("winrm", {})
    cfg["winrm"] = {
        "username": winrm.get("username", ""),
        "password": winrm.get("password", ""),
        "scheme": winrm.get("scheme", "http"),
        "port": int(winrm.get("port", 5985)),
        "timeout": int(winrm.get("timeout", 30)),
        "verify_ssl": False,
    }

    # Ping
    ping = remote.get("ping", {})
    cfg["ping"] = {
        "enabled": True,
        "timeout": int(ping.get("timeout", 3)),
    }

    # API
    cfg["api"] = {
        "url": api_url,
        "token": api_token,
        "retry_attempts": 3,
        "retry_delay": 5,
    }

    # Schedule
    schedule = remote.get("schedule", {})
    cfg["schedule"] = {
        "enabled": True,
        "interval_hours": int(schedule.get("interval_hours", 6)),
    }

    # SSH
    ssh = remote.get("ssh", {})
    cfg["ssh"] = {
        "username": ssh.get("username", "root"),
        "port": int(ssh.get("port", 22)),
        "private_key": ssh.get("private_key", ""),
        "key_passphrase": ssh.get("key_passphrase", ""),
        "timeout": int(ssh.get("timeout", 15)),
    }

    # Logging (mantém default)
    cfg["logging"] = {
        "level": "INFO",
        "file": "logs/collector.log",
        "max_bytes": 10485760,
        "backup_count": 5,
    }

    return cfg


def get_config():
    """Retorna config carregada. Carrega automaticamente se necessário."""
    if _config is None:
        return load_config()
    return _config
