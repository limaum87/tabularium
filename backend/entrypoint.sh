#!/bin/bash
set -e

# Tabularium Backend — Entrypoint
# Aplica DNS customizado preservando o DNS interno do Docker

echo "[entrypoint] Tabularium Backend iniciando..."

DNS_SERVERS="${DNS_SERVERS:-}"
DNS_SEARCH="${DNS_SEARCH:-}"

if [ -n "$DNS_SERVERS" ]; then
    echo "[entrypoint] Aplicando DNS: $DNS_SERVERS"

    # Remove symlink se existir e recria como arquivo normal
    if [ -L /etc/resolv.conf ]; then
        rm -f /etc/resolv.conf
    fi

    # Escreve resolv.conf preservando DNS do Docker
    {
        if [ -n "$DNS_SEARCH" ]; then
            echo "search $DNS_SEARCH"
        fi
        echo "nameserver 127.0.0.11"
        for ip in $(echo "$DNS_SERVERS" | tr ',' ' '); do
            echo "nameserver $ip"
        done
        echo "options edns0 trust-ad ndots:0"
    } > /etc/resolv.conf

    echo "[entrypoint] /etc/resolv.conf atualizado"
    cat /etc/resolv.conf
fi

echo "[entrypoint] Iniciando uvicorn..."
exec "$@"
