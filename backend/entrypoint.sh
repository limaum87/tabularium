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

# Auto-migrate: garante que colunas novas existam
python -c "
from app.core.database import engine
import sqlalchemy
insp = sqlalchemy.inspect(engine)
cols = [c['name'] for c in insp.get_columns('host_licenses')]
if 'click_to_run' not in cols:
    print('[migration] Adicionando coluna click_to_run em host_licenses...')
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text('ALTER TABLE host_licenses ADD COLUMN click_to_run TINYINT(1) DEFAULT NULL AFTER oem_key_found'))
        conn.commit()
    print('[migration] OK')
else:
    print('[migration] Coluna click_to_run já existe, pulando...')
" || echo "[migration] Aviso: não conseguiu verificar/criar coluna click_to_run"

echo "[entrypoint] Iniciando uvicorn..."
exec "$@"
