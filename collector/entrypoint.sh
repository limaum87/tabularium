#!/bin/bash
set -e

# Tabularium Collector — Entrypoint
# Aplica DNS customizado antes de iniciar o collector

echo "[entrypoint] Tabularium Collector iniciando..."

DNS_SERVERS="${DNS_SERVERS:-}"

if [ -n "$DNS_SERVERS" ]; then
    echo "[entrypoint] Aplicando DNS: $DNS_SERVERS"

    if [ -L /etc/resolv.conf ]; then
        rm -f /etc/resolv.conf
    fi

    {
        for ip in $(echo "$DNS_SERVERS" | tr ',' ' '); do
            echo "nameserver $ip"
        done
    } > /etc/resolv.conf

    echo "[entrypoint] /etc/resolv.conf atualizado"
    cat /etc/resolv.conf
fi

echo "[entrypoint] Iniciando collector..."
exec "$@"
