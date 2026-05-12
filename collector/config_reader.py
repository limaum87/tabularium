"""Tabularium Collector — Leitura de configuração."""

import os
import yaml


_config = None


def load_config(path=None):
    """Carrega config.yaml. Prioridade: parâmetro > env COLLECTOR_CONFIG > config.yaml."""
    global _config
    if path is None:
        path = os.environ.get("COLLECTOR_CONFIG", os.path.join(os.path.dirname(__file__), "config.yaml"))

    with open(path, "r", encoding="utf-8") as f:
        _config = yaml.safe_load(f)
    return _config


def get_config():
    """Retorna config carregada. Carrega automaticamente se necessário."""
    if _config is None:
        return load_config()
    return _config
