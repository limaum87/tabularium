"""Tabularium Collector — Teste de conectividade (ICMP ping)."""

import platform
import subprocess

from collector.logger import get_logger

log = get_logger()


def ping_host(hostname, timeout=3):
    """Testa se um host responde a ICMP ping.

    Retorna True se o host está online, False caso contrário.
    """
    param = "-n" if platform.system().lower() == "windows" else "-c"
    timeout_param = "-w" if platform.system().lower() == "windows" else "-W"

    try:
        result = subprocess.run(
            ["ping", param, "1", timeout_param, str(timeout), hostname],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 2,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def check_hosts(hosts, timeout=3):
    """Recebe lista de hostnames, retorna (online_list, offline_list)."""
    online = []
    offline = []

    for host in hosts:
        hostname = host["hostname"] if isinstance(host, dict) else host
        if ping_host(hostname, timeout):
            online.append(host)
            log.debug(f"  ✓ {hostname} — online")
        else:
            offline.append(host)
            log.debug(f"  ✗ {hostname} — offline")

    return online, offline
