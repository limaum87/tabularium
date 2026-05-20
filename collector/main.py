"""Tabularium Collector — Orquestrador principal.

Fluxo de coleta:
    1. Descobrir hosts via LDAP
    2. Testar conectividade (ICMP ping)
    3. Classificar portas: WinRM (5985) e SSH (22)
    4. Coletar dados via WinRM apenas dos hosts Windows acessíveis
    5. Reportar Linux/unknown/offline ao backend

Uso:
    python -m collector.main              # execução única
    python -m collector.main --schedule   # modo agendado
    python -m collector.main --config /path/to/config.yaml
"""

import argparse
import time
from datetime import datetime

import schedule as schedule_lib

from collector.config_reader import load_config, get_config
from collector.logger import setup_logging, get_logger
from collector.ldap_discovery import discover_hosts
from collector.ping_check import check_hosts
from collector.port_check import classify_hosts
from collector.winrm_collector import WinRMCollector
from collector.api_client import APIClient


def run_collection():
    """Executa uma rodada completa de coleta."""
    cfg = get_config()
    log = get_logger()
    api = APIClient(cfg)

    log.info("=" * 60)
    log.info("Iniciando coleta")
    start = datetime.utcnow()

    # -------------------------------------------------------
    # 1. Descobrir hosts via LDAP
    # -------------------------------------------------------
    hosts = discover_hosts(cfg)
    if not hosts:
        log.warning("Nenhum host encontrado no AD. Encerrando.")
        return

    # -------------------------------------------------------
    # 2. Testar conectividade (ICMP ping)
    # -------------------------------------------------------
    ping_cfg = cfg.get("ping", {})
    if ping_cfg.get("enabled", True):
        log.info(f"Ping: testando {len(hosts)} hosts...")
        online, offline = check_hosts(hosts, timeout=ping_cfg.get("timeout", 3))
        log.info(f"Ping: online={len(online)} | offline={len(offline)}")

        # Reportar offline
        for host in offline:
            hostname = host["hostname"] if isinstance(host, dict) else host
            log.info(f"  ✗ {hostname} — offline")
            api.send_scan_result(hostname, "offline", "Host não respondeu ao ping")
    else:
        online = hosts
        log.info(f"Ping desabilitado. Prosseguindo com {len(hosts)} hosts diretamente.")

    if not online:
        log.info("Nenhum host online. Encerrando coleta.")
        return

    # -------------------------------------------------------
    # 3. Classificar portas (WinRM vs SSH)
    # -------------------------------------------------------
    port_cfg = cfg.get("port_check", {})
    if port_cfg.get("enabled", True):
        winrm_scheme = port_cfg.get("winrm_scheme", cfg.get("winrm", {}).get("scheme", "http"))
        port_timeout = port_cfg.get("timeout", 5)

        log.info(f"Portas: classificando {len(online)} hosts online (timeout={port_timeout}s)...")
        classified = classify_hosts(online, winrm_scheme=winrm_scheme, port_timeout=port_timeout)

        winrm_hosts = classified["winrm_ok"]
        linux_hosts = classified["linux"]
        unknown_hosts = classified["unknown"]

        log.info(f"Portas: WinRM={len(winrm_hosts)} | Linux={len(linux_hosts)} | Unknown={len(unknown_hosts)}")
    else:
        winrm_hosts = online
        linux_hosts = []
        unknown_hosts = []
        log.info("Teste de portas desabilitado. Tentando WinRM em todos os hosts online.")

    # -------------------------------------------------------
    # 4. Reportar hosts Linux (SSH acessível, sem WinRM)
    # -------------------------------------------------------
    for host in linux_hosts:
        hostname = host["hostname"] if isinstance(host, dict) else host
        log.info(f"  🐧 {hostname} — Linux detectado (SSH acessível, sem WinRM)")
        api.send_linux_checkin(hostname)

    # -------------------------------------------------------
    # 5. Reportar hosts unknown (sem WinRM nem SSH)
    # -------------------------------------------------------
    for host in unknown_hosts:
        hostname = host["hostname"] if isinstance(host, dict) else host
        log.info(f"  ? {hostname} — sem WinRM nem SSH")
        api.send_scan_result(
            hostname,
            "winrm_unavailable",
            "Host online mas sem WinRM (5985) nem SSH (22). Possível firewall ou SO não identificado.",
        )

    # -------------------------------------------------------
    # 6. Coletar dados via WinRM (apenas hosts acessíveis)
    # -------------------------------------------------------
    if not winrm_hosts:
        log.info("Nenhum host com WinRM acessível. Pulando coleta.")
    else:
        collector = WinRMCollector(cfg)
        stats = {"collected": 0, "api_ok": 0, "api_fail": 0, "errors": 0}

        log.info(f"WinRM: coletando {len(winrm_hosts)} hosts...")
        for i, host in enumerate(winrm_hosts, 1):
            hostname = host["hostname"] if isinstance(host, dict) else host
            log.info(f"  [{i}/{len(winrm_hosts)}] {hostname}")

            try:
                data = collector.collect(hostname)

                # Envia para API
                if api.send_checkin(data):
                    stats["api_ok"] += 1
                else:
                    stats["api_fail"] += 1

                stats["collected"] += 1

            except Exception as e:
                log.error(f"    ✗ Erro ao coletar {hostname}: {e}")
                stats["errors"] += 1
                api.send_scan_result(hostname, "error", str(e)[:200])

        log.info(f"WinRM: coletados={stats['collected']} | API OK={stats['api_ok']} | API fail={stats['api_fail']} | erros={stats['errors']}")

    # -------------------------------------------------------
    # 7. Resumo final
    # -------------------------------------------------------
    elapsed = (datetime.utcnow() - start).total_seconds()
    log.info("-" * 60)
    log.info(
        f"Coleta finalizada em {elapsed:.0f}s — "
        f"total={len(hosts)} | online={len(online)} | "
        f"winrm={len(winrm_hosts)} | linux={len(linux_hosts)} | "
        f"unknown={len(unknown_hosts)} | offline={len(hosts) - len(online)}"
    )
    log.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Tabularium Collector")
    parser.add_argument("--config", "-c", help="Caminho para config.yaml", default=None)
    parser.add_argument("--schedule", "-s", help="Modo agendado (loop)", action="store_true")
    args = parser.parse_args()

    # Carrega configuração
    cfg = load_config(args.config)

    # Setup logging
    log = setup_logging(cfg)

    log.info("Tabularium Collector iniciado")
    log.info(f"Config: {args.config or 'config.yaml'}")

    if args.schedule:
        interval = cfg.get("schedule", {}).get("interval_hours", 6)
        log.info(f"Modo agendado: a cada {interval}h")

        # Executa imediatamente
        run_collection()

        # Agenda próximas
        schedule_lib.every(interval).hours.do(run_collection)

        while True:
            schedule_lib.run_pending()
            time.sleep(60)
    else:
        # Execução única
        run_collection()


if __name__ == "__main__":
    main()
