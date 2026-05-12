"""Tabularium Collector — Orquestrador principal.

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
from collector.winrm_collector import WinRMCollector
from collector.api_client import APIClient


def run_collection():
    """Executa uma rodada completa de coleta."""
    cfg = get_config()
    log = get_logger()

    log.info("=" * 60)
    log.info("Iniciando coleta")
    start = datetime.utcnow()

    # 1. Descobrir hosts via LDAP
    hosts = discover_hosts(cfg)
    if not hosts:
        log.warning("Nenhum host encontrado no AD. Encerrando.")
        return

    # 2. Testar conectividade
    ping_cfg = cfg.get("ping", {})
    if ping_cfg.get("enabled", True):
        log.info(f"Testando conectividade de {len(hosts)} hosts...")
        online, offline = check_hosts(hosts, timeout=ping_cfg.get("timeout", 3))
        log.info(f"Online: {len(online)} | Offline: {len(offline)}")

        # Reportar offline
        api = APIClient(cfg)
        for host in offline:
            hostname = host["hostname"]
            log.info(f"  ✗ {hostname} — offline")
            api.send_scan_result(hostname, "offline", "Host não respondeu ao ping")
    else:
        online = hosts
        log.info(f"Ping desabilitado. Tentando coletar {len(hosts)} hosts diretamente.")

    # 3. Coletar dados via WinRM
    collector = WinRMCollector(cfg)
    api = APIClient(cfg)

    stats = {"collected": 0, "api_ok": 0, "api_fail": 0, "errors": 0}

    for i, host in enumerate(online, 1):
        hostname = host["hostname"]
        log.info(f"[{i}/{len(online)}] {hostname}")

        try:
            data = collector.collect(hostname)

            # Envia para API
            if api.send_checkin(data):
                stats["api_ok"] += 1
            else:
                stats["api_fail"] += 1

            stats["collected"] += 1

        except Exception as e:
            log.error(f"  ✗ Erro ao coletar {hostname}: {e}")
            stats["errors"] += 1
            api.send_scan_result(hostname, "error", str(e)[:200])

    # 4. Resumo
    elapsed = (datetime.utcnow() - start).total_seconds()
    log.info("-" * 60)
    log.info(
        f"Coleta finalizada em {elapsed:.0f}s — "
        f"coletados: {stats['collected']} | "
        f"API OK: {stats['api_ok']} | "
        f"API falha: {stats['api_fail']} | "
        f"erros WinRM: {stats['errors']}"
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
