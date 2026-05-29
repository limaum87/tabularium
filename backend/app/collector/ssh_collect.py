"""Tabularium Backend — Coleta manual via SSH (hosts Linux).

Conecta via SSH usando paramiko e executa comandos shell para coletar
hardware, discos, rede, distro e software do host.
"""

import json
import io


def get_ssh_settings(db):
    """Busca configurações SSH do banco."""
    from app.core.database import Setting

    ssh_user = db.query(Setting).filter(Setting.key == "username", Setting.category == "ssh").first()
    ssh_port = db.query(Setting).filter(Setting.key == "port", Setting.category == "ssh").first()
    ssh_timeout = db.query(Setting).filter(Setting.key == "timeout", Setting.category == "ssh").first()
    ssh_key = db.query(Setting).filter(Setting.key == "private_key", Setting.category == "ssh").first()
    ssh_passphrase = db.query(Setting).filter(Setting.key == "key_passphrase", Setting.category == "ssh").first()
    dns_search = db.query(Setting).filter(Setting.key == "search_domain", Setting.category == "dns").first()

    if not ssh_user or not ssh_key or not ssh_key.value or not ssh_key.value.strip():
        return None

    return {
        "username": ssh_user.value,
        "port": int(ssh_port.value) if ssh_port and ssh_port.value else 22,
        "timeout": int(ssh_timeout.value) if ssh_timeout and ssh_timeout.value else 15,
        "private_key": ssh_key.value,
        "key_passphrase": ssh_passphrase.value if ssh_passphrase else "",
        "search": dns_search.value.strip() if dns_search and dns_search.value else "",
    }


def make_fqdn(hostname, search):
    """Monta FQDN se necessário."""
    if "." not in hostname and search:
        return f"{hostname}.{search}"
    return hostname


def _load_key(private_key_str, passphrase=None):
    """Carrega chave privada (RSA ou Ed25519)."""
    import paramiko

    key_file = io.StringIO(private_key_str)
    try:
        return paramiko.RSAKey.from_private_key(key_file, password=passphrase or None)
    except paramiko.SSHException:
        key_file = io.StringIO(private_key_str)
        return paramiko.Ed25519Key.from_private_key(key_file, password=passphrase or None)


def ssh_connect(hostname, cfg):
    """Cria conexão SSH usando as configurações."""
    import paramiko

    pkey = _load_key(cfg["private_key"], cfg.get("key_passphrase"))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=hostname,
        port=cfg["port"],
        username=cfg["username"],
        pkey=pkey,
        timeout=cfg["timeout"],
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def ssh_run(client, command, timeout=30):
    """Executa comando SSH e retorna stdout."""
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    if exit_code != 0 and not out:
        return err
    return out


# ---- Scripts de coleta (bash) ----

def bash_hostname():
    return r"""
echo "{\"hostname\":\"$(hostname -s 2>/dev/null || cat /etc/hostname 2>/dev/null | head -1)\",\"fqdn\":\"$(hostname -f 2>/dev/null || hostname 2>/dev/null)\"}"
"""


def bash_hardware():
    return r"""
echo '{'
echo '  "manufacturer": "'$(sudo dmidecode -s system-manufacturer 2>/dev/null | tr -d '"' | head -1)'",'
echo '  "model": "'$(sudo dmidecode -s system-product-name 2>/dev/null | tr -d '"' | head -1)'",'
echo '  "serial": "'$(sudo dmidecode -s system-serial-number 2>/dev/null | tr -d '"' | head -1)'",'
echo '  "cpu": "'$(grep -m1 "model name" /proc/cpuinfo 2>/dev/null | cut -d: -f2 | xargs)'",'
echo '  "ram_gb": '$(awk '/MemTotal/ {printf "%.1f", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 'null')','
echo '  "bios_version": "'$(sudo dmidecode -s bios-version 2>/dev/null | tr -d '"' | head -1)'",'
echo '  "last_boot": "'$(who -b 2>/dev/null | awk '{print $3" "$4}')'",'
echo '  "last_user": "'$(last -1 -w 2>/dev/null | awk '{print $1}')'"'
echo '}'
"""

def bash_disks():
    return r"""
df -B1073741824 --output=target,size,avail,fstype 2>/dev/null | tail -n +2 | grep -v 'tmpfs\|devtmpfs\|squashfs\|overlay\|shm' | awk '{
    gsub(/^ +/, "", $1);
    gsub(/^ +/, "", $2);
    gsub(/^ +/, "", $3);
    gsub(/^ +/, "", $4);
    printf "{\"drive\":\"%s\",\"total_gb\":%s,\"free_gb\":%s,\"filesystem\":\"%s\"}\n", $1, $2, $3, $4
}' | awk 'BEGIN{print "["} {sep=""; if(NR>1) sep=","; printf "%s%s",sep,$0} END{print "]"}'
"""

def bash_network():
    return r"""
ip -j addr show 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
result = []
for iface in data:
    name = iface.get('ifname','')
    if name == 'lo':
        continue
    for addr in iface.get('addr_info', []):
        ip = addr.get('local','')
        if not ip or addr.get('family') != 'inet':
            continue
        result.append({
            'ip': ip,
            'mac': iface.get('address',''),
            'adapter_name': name,
        })
        break
    if not any(r.get('adapter_name') == name for r in result):
        result.append({
            'ip': '',
            'mac': iface.get('address',''),
            'adapter_name': name,
        })
print(json.dumps(result))
" 2>/dev/null || echo '[]'
"""

def bash_distro():
    return r"""
if [ -f /etc/os-release ]; then
    . /etc/os-release
    cat <<EOF
{
  "name": "${NAME}",
  "version": "${VERSION}",
  "distro_id": "${ID}",
  "id_like": "${ID_LIKE}",
  "pretty_name": "${PRETTY_NAME}"
}
EOF
elif [ -f /etc/redhat-release ]; then
    echo "{\"name\": \"$(cat /etc/redhat-release)\", \"distro_id\": \"rhel\"}"
else
    echo "{\"name\": \"$(uname -s)\", \"version\": \"$(uname -r)\"}"
fi
echo ",\"kernel\": \"$(uname -r)\",\"arch\": \"$(uname -m)\""
"""

def bash_distro_fixed():
    return r"""
if [ -f /etc/os-release ]; then
    . /etc/os-release
    cat <<EOJSON
{"name":"$NAME","version":"$VERSION","distro_id":"$ID","id_like":"$ID_LIKE","pretty_name":"$PRETTY_NAME","kernel":"$(uname -r)","arch":"$(uname -m)"}
EOJSON
else
    echo "{\"name\":\"$(uname -s)\",\"version\":\"$(uname -r)\",\"kernel\":\"$(uname -r)\",\"arch\":\"$(uname -m)\"}"
fi
"""

def bash_software():
    return r"""
if command -v dpkg &>/dev/null; then
    dpkg-query -W -f='{"name":"${Package}","version":"${Version}","publisher":""}' 2>/dev/null | awk 'BEGIN{print "["} {sep=""; if(NR>1) sep=","; printf "%s%s",sep,$0} END{print "]"}'
elif command -v rpm &>/dev/null; then
    rpm -qa --qf '{"name":"%{NAME}","version":"%{VERSION}-%{RELEASE}","publisher":"%{VENDOR}"}' 2>/dev/null | awk 'BEGIN{print "["} {sep=""; if(NR>1) sep=","; printf "%s%s",sep,$0} END{print "]"}'
elif command -v pacman &>/dev/null; then
    pacman -Q 2>/dev/null | awk '{printf "{\"name\":\"%s\",\"version\":\"%s\",\"publisher\":\"\"}\n",$1,$2}' | awk 'BEGIN{print "["} {sep=""; if(NR>1) sep=","; printf "%s%s",sep,$0} END{print "]"}'
else
    echo '[]'
fi
"""


def safe_json_parse(raw, context=""):
    """Tenta fazer parse de JSON da saída do comando."""
    if not raw or not raw.strip():
        return None
    # Tenta extrair JSON de saída mista
    for line in raw.strip().splitlines():
        line = line.strip()
        if line.startswith("{") or line.startswith("["):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    # Tenta o bloco inteiro
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass
    # Tenta encontrar JSON válido no meio da saída
    start_brace = raw.find("{")
    start_bracket = raw.find("[")
    start = -1
    if start_brace >= 0 and start_bracket >= 0:
        start = min(start_brace, start_bracket)
    elif start_brace >= 0:
        start = start_brace
    elif start_bracket >= 0:
        start = start_bracket
    if start >= 0:
        try:
            return json.loads(raw[start:])
        except json.JSONDecodeError:
            pass
    return None
