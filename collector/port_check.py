"""Tabularium Collector — Teste de portas TCP (WinRM, SSH)."""

import socket

from collector.logger import get_logger

log = get_logger()

# Portas padrão
WINRM_HTTP_PORT = 5985
WINRM_HTTPS_PORT = 5986
SSH_PORT = 22


def test_port(hostname, port, timeout=5):
    """Testa se uma porta TCP está aberta em um host.

    Retorna True se a porta respondeu (connect succeeded), False caso contrário.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((hostname, port))
        sock.close()
        return result == 0
    except (socket.gaierror, socket.timeout, OSError) as e:
        log.debug(f"  Porta {port} em {hostname}: {e}")
        return False


def check_winrm_port(hostname, scheme="http", timeout=5):
    """Testa se a porta WinRM está acessível.

    Retorna True se a porta WinRM respondeu.
    """
    port = WINRM_HTTP_PORT if scheme == "http" else WINRM_HTTPS_PORT
    return test_port(hostname, port, timeout)


def check_ssh_port(hostname, timeout=5):
    """Testa se a porta SSH (22) está acessível.

    Retorna True se a porta SSH respondeu.
    """
    return test_port(hostname, SSH_PORT, timeout)


def classify_hosts(hosts, winrm_scheme="http", port_timeout=5):
    """Classifica hosts em categorias baseado em portas abertas.

    Recebe lista de hosts (dicts com 'hostname').
    Retorna dict com:
      - winrm_ok:   hosts com porta WinRM aberta
      - linux:      hosts sem WinRM mas com porta SSH aberta
      - unknown:    hosts sem WinRM nem SSH
    """
    winrm_ok = []
    linux = []
    unknown = []

    for host in hosts:
        hostname = host["hostname"] if isinstance(host, dict) else host

        # Testa WinRM
        if check_winrm_port(hostname, scheme=winrm_scheme, timeout=port_timeout):
            winrm_ok.append(host)
            log.debug(f"  ✓ {hostname} — WinRM acessível")
            continue

        # WinRM falhou, testa SSH
        if check_ssh_port(hostname, timeout=port_timeout):
            linux.append(host)
            log.debug(f"  🐧 {hostname} — Linux (SSH acessível, sem WinRM)")
            continue

        # Nenhum dos dois
        unknown.append(host)
        log.debug(f"  ? {hostname} — sem WinRM nem SSH")

    return {
        "winrm_ok": winrm_ok,
        "linux": linux,
        "unknown": unknown,
    }
