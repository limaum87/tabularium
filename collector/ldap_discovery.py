"""Tabularium Collector — Módulo LDAP (Active Directory)."""

import socket
import struct

from ldap3 import Server, Connection, ALL, SUBTREE
from collector.logger import get_logger

log = get_logger()


def discover_hosts(cfg):
    """Consulta computadores no Active Directory via LDAP.

    Retorna lista de dicts: [{"hostname": "PC-01", "dn": "CN=PC-01,..."}]
    """
    ad = cfg.get("ad", {})
    server_url = ad.get("server", "")
    bind_dn = ad.get("bind_dn", "")
    password = ad.get("password", "")
    base_dn = ad.get("base_dn", "")
    search_filter = ad.get("search_filter", "(objectClass=computer)")
    ou_list = ad.get("ou_list", [])

    if not all([server_url, bind_dn, password, base_dn]):
        log.error("Configuração LDAP incompleta. Verifique config.yaml → ad")
        return []

    hosts = []
    server = Server(server_url, get_info=ALL, connect_timeout=10)

    try:
        conn = Connection(server, user=bind_dn, password=password, auto_bind=True, read_only=True)
        log.info(f"Conectado ao AD: {server_url}")
    except Exception as e:
        log.error(f"Falha ao conectar ao AD ({server_url}): {e}")
        return []

    # Se ou_list vazio, busca no base_dn
    search_bases = ou_list if ou_list else [base_dn]

    for base in search_bases:
        log.info(f"Buscando computadores em: {base}")
        try:
            conn.search(
                search_base=base,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=["cn", "dNSHostName", "name", "operatingSystem"],
            )
            for entry in conn.entries:
                hostname = ""
                # Tenta dNSHostName primeiro, depois cn, depois name
                if hasattr(entry, "dNSHostName") and entry.dNSHostName.value:
                    hostname = entry.dNSHostName.value.split(".")[0]
                elif hasattr(entry, "cn") and entry.cn.value:
                    hostname = entry.cn.value
                elif hasattr(entry, "name") and entry.name.value:
                    hostname = entry.name.value

                if hostname:
                    hosts.append({
                        "hostname": hostname.upper(),
                        "dn": entry.entry_dn,
                        "os": getattr(entry, "operatingSystem", None) and entry.operatingSystem.value or "",
                    })
        except Exception as e:
            log.error(f"Erro ao buscar em {base}: {e}")

    conn.unbind()

    # Deduplica por hostname
    seen = set()
    unique = []
    for h in hosts:
        if h["hostname"] not in seen:
            seen.add(h["hostname"])
            unique.append(h)

    log.info(f"Encontrados {len(unique)} computadores no AD")
    return unique
