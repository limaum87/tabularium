#!/bin/bash
set -e

# Tabularium Backend — Entrypoint
# Aplica DNS customizado do banco antes de iniciar o uvicorn

echo "[entrypoint] Tabularium Backend iniciando..."

# Tenta ler DNS das variáveis de ambiente (opcional)
DNS_SERVERS="${DNS_SERVERS:-}"
DNS_SEARCH="${DNS_SEARCH:-}"

if [ -n "$DNS_SERVERS" ]; then
    echo "[entrypoint] Aplicando DNS: $DNS_SERVERS"

    # Remove symlink se existir e recria como arquivo normal
    if [ -L /etc/resolv.conf ]; then
        rm -f /etc/resolv.conf
    fi

    # Escreve resolv.conf
    {
        if [ -n "$DNS_SEARCH" ]; then
            echo "search $DNS_SEARCH"
        fi
        for ip in $(echo "$DNS_SERVERS" | tr ',' ' '); do
            echo "nameserver $ip"
        done
    } > /etc/resolv.conf

    echo "[entrypoint] /etc/resolv.conf atualizado"
    cat /etc/resolv.conf
fi

echo "[entrypoint] Iniciando uvicorn..."
exec "$@"
