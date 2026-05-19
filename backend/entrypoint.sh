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

# Auto-migrate: garante que colunas/tabelas novas existam
python -c "
from app.core.database import engine
import sqlalchemy
insp = sqlalchemy.inspect(engine)

# Coluna click_to_run em host_licenses
cols = [c['name'] for c in insp.get_columns('host_licenses')]
if 'click_to_run' not in cols:
    print('[migration] Adicionando coluna click_to_run em host_licenses...')
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text('ALTER TABLE host_licenses ADD COLUMN click_to_run TINYINT(1) DEFAULT NULL AFTER oem_key_found'))
        conn.commit()
    print('[migration] OK')

# Tabela host_remote_access
tables = insp.get_table_names()
if 'host_remote_access' not in tables:
    print('[migration] Criando tabela host_remote_access...')
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text('''
            CREATE TABLE host_remote_access (
                id INT AUTO_INCREMENT PRIMARY KEY,
                host_id INT NOT NULL,
                anydesk_id VARCHAR(50) DEFAULT NULL,
                anydesk_alias VARCHAR(255) DEFAULT NULL,
                anydesk_version VARCHAR(50) DEFAULT NULL,
                ultravnc_installed TINYINT(1) DEFAULT NULL,
                ultravnc_port INT DEFAULT NULL,
                ultravnc_version VARCHAR(50) DEFAULT NULL,
                teamviewer_id VARCHAR(50) DEFAULT NULL,
                updated_at DATETIME DEFAULT NULL,
                INDEX ix_host_remote_access_host_id (host_id)
            )
        '''))
        conn.commit()
    print('[migration] OK')

print('[migration] Tudo ok')
" || echo "[migration] Aviso: não conseguiu verificar/criar migrações"

echo "[entrypoint] Iniciando uvicorn..."
exec "$@"
