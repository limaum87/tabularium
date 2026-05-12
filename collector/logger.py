"""Tabularium Collector — Logging estruturado."""

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging(cfg):
    """Configura logging com console + arquivo rotativo."""
    log_cfg = cfg.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file", "logs/collector.log")
    max_bytes = log_cfg.get("max_bytes", 10 * 1024 * 1024)
    backup_count = log_cfg.get("backup_count", 5)

    # Cria diretório de logs
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    # Formato
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-5s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Root logger
    root = logging.getLogger("tabularium")
    root.setLevel(level)

    # Console
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    # File
    file_handler = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    return root


def get_logger():
    """Retorna o logger do collector."""
    return logging.getLogger("tabularium")
