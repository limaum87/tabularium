"""Tabularium Collector — Network Discovery (Ping Sweep + SSH Banner).

Varre sub-redes configuradas buscando máquinas com porta SSH (22) aberta.
Lê o banner SSH para confirmar que é um servidor Linux.

100% pure Python — sem dependência de nmap.

Retorna lista de dicts:
    [{"ip": "192.168.1.50", "hostname": "web01", "ssh_banner": "OpenSSH 8.9 Ubuntu", "port": 22}]
"""

import socket
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed

from collector.logger import get_logger

log = get_logger()

SSH_PORT = 22
DEFAULT_TIMEOUT = 2  # segundos
DEFAULT_MAX_WORKERS = 100


def _grab_ssh_banner(ip, port=SSH_PORT, timeout=DEFAULT_TIMEOUT):
    """Tenta conectar na porta SSH e ler o banner.

    Retorna o banner string se conseguiu, ou None se a porta está fechada/timeout.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))

        # SSH server envia banner logo após conectar
        banner = sock.recv(256).decode("utf-8", errors="ignore").strip()
        sock.close()

        # Banner SSH típico: "SSH-2.0-OpenSSH_8.9 Ubuntu-3ubuntu0.1"
        if banner.startswith("SSH-"):
            return banner

        return banner if banner else None
    except (socket.timeout, socket.error, ConnectionRefusedError, OSError):
        return None


def _resolve_hostname(ip):
    """Tenta DNS reverso para descobrir o hostname do IP."""
    try:
        hostname, _, _ = socket.gethostbyaddr(ip)
        return hostname.split(".")[0].upper()
    except (socket.herror, socket.gaierror, OSError):
        return None


def _scan_ip(ip, port=SSH_PORT, timeout=DEFAULT_TIMEOUT):
    """Escaneia um único IP: tenta conectar na porta SSH e ler o banner.

    Retorna dict com dados se encontrou SSH, ou None.
    """
    banner = _grab_ssh_banner(ip, port, timeout)
    if banner:
        hostname = _resolve_hostname(ip)
        return {
            "ip": ip,
            "hostname": hostname,
            "ssh_banner": banner,
            "port": port,
        }
    return None


def discover_network_hosts(cfg):
    """Varre sub-redes configuradas buscando hosts com SSH aberto.

    Lê do config:
        network.subnets: ["192.168.1.0/24", "10.0.0.0/24"]
        network.ssh_port: 22
        network.timeout: 2
        network.max_workers: 100
        network.exclude_ips: ["192.168.1.1"]

    Retorna lista de dicts com IPs que têm SSH aberto.
    """
    net_cfg = cfg.get("network", {})
    subnets = net_cfg.get("subnets", [])
    ssh_port = net_cfg.get("ssh_port", SSH_PORT)
    timeout = net_cfg.get("timeout", DEFAULT_TIMEOUT)
    max_workers = net_cfg.get("max_workers", DEFAULT_MAX_WORKERS)
    exclude_ips = set(net_cfg.get("exclude_ips", []))

    if not subnets:
        log.warning("Nenhuma sub-rede configurada para network discovery. Verifique config.yaml → network.subnets")
        return []

    # Gera lista de todos os IPs a escanear
    all_ips = []
    for subnet in subnets:
        try:
            network = ipaddress.ip_network(subnet, strict=False)
            for ip in network.hosts():
                ip_str = str(ip)
                if ip_str not in exclude_ips:
                    all_ips.append(ip_str)
        except ValueError as e:
            log.error(f"Sub-rede inválida '{subnet}': {e}")
            continue

    if not all_ips:
        log.warning("Nenhum IP para escanear após processar sub-redes.")
        return []

    log.info(f"Network discovery: escaneando {len(all_ips)} IPs em {len(subnets)} sub-rede(s) — timeout={timeout}s, workers={max_workers}")

    # Scan paralelo
    found = []
    scanned = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_scan_ip, ip, ssh_port, timeout): ip for ip in all_ips}

        for future in as_completed(futures):
            scanned += 1
            if scanned % 50 == 0:
                log.info(f"  Progresso: {scanned}/{len(all_ips)} IPs escaneados, {len(found)} com SSH")

            try:
                result = future.result()
                if result:
                    found.append(result)
                    log.info(f"  🐧 {result['ip']} — SSH encontrado: {result['ssh_banner'][:60]}")
            except Exception as e:
                log.debug(f"  Erro ao escanear IP: {e}")

    # Ordena por IP
    found.sort(key=lambda x: tuple(int(p) for p in x["ip"].split(".")))

    log.info(f"Network discovery concluído: {scanned} IPs escaneados, {len(found)} hosts com SSH (prováveis Linux)")
    return found
