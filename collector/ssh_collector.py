"""Tabularium Collector — Módulo SSH para hosts Linux.

Conecta via SSH usando chave privada e coleta:
  - Hardware (fabricante, modelo, serial, CPU, RAM)
  - Distro (nome, versão, kernel)
  - Discos
  - Rede (IPs, MACs)
"""

import io
import json

import paramiko

from collector.logger import get_logger

log = get_logger()


class SSHCollector:
    """Conecta a um host Linux via SSH (chave privada) e coleta dados."""

    def __init__(self, cfg):
        ssh_cfg = cfg.get("ssh", {})
        self.username = ssh_cfg.get("username", "root")
        self.port = int(ssh_cfg.get("port", 22))
        self.private_key = ssh_cfg.get("private_key", "")
        self.key_passphrase = ssh_cfg.get("key_passphrase", "")
        self.timeout = int(ssh_cfg.get("timeout", 15))
        self._key = None

        # Faz parse da chave privada uma vez
        if self.private_key:
            try:
                key_file = io.StringIO(self.private_key)
                self._key = paramiko.RSAKey.from_private_key(
                    key_file, password=self.key_passphrase or None
                )
            except paramiko.PasswordRequiredException:
                log.warning("  SSH: chave privada requer passphrase não informada")
                self._key = None
            except paramiko.SSHException:
                # Tenta Ed25519 se RSA falhar
                try:
                    key_file = io.StringIO(self.private_key)
                    self._key = paramiko.Ed25519Key.from_private_key(
                        key_file, password=self.key_passphrase or None
                    )
                except Exception as e:
                    log.warning(f"  SSH: falha ao carregar chave privada: {e}")
                    self._key = None
            except Exception as e:
                log.warning(f"  SSH: falha ao carregar chave privada: {e}")
                self._key = None

    @property
    def is_configured(self):
        """Retorna True se o collector tem chave privada configurada."""
        return self._key is not None

    def _connect(self, hostname):
        """Cria conexão SSH com o host."""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        client.connect(
            hostname=hostname,
            port=self.port,
            username=self.username,
            pkey=self._key,
            timeout=self.timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        return client

    def _run(self, client, cmd):
        """Executa um comando SSH e retorna stdout como string."""
        _, stdout, stderr = client.exec_command(cmd, timeout=self.timeout)
        exit_code = stdout.channel.recv_exit_status()
        output = stdout.read().decode("utf-8", errors="replace").strip()
        if exit_code != 0:
            err = stderr.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"SSH cmd failed (rc={exit_code}): {err[:200]}")
        return output

    def _safe_json(self, raw, context=""):
        """Tenta parsear JSON. Retorna dict/list ou None."""
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            log.warning(f"  JSON inválido ({context}): {e}")
            return None

    # ---- Scripts de coleta (comandos shell) ----

    def _cmd_hardware(self):
        """Retorna comando shell para coletar hardware."""
        return r"""
echo '{"manufacturer":"'$(cat /sys/devices/virtual/dmi/id/board_vendor 2>/dev/null || echo "N/A")'",'\
'"model":"'$(cat /sys/devices/virtual/dmi/id/product_name 2>/dev/null || echo "N/A")'",'\
'"serial":"'$(cat /sys/devices/virtual/dmi/id/product_serial 2>/dev/null || cat /sys/devices/virtual/dmi/id/board_serial 2>/dev/null || echo "N/A")'",'\
'"cpu":"'$(grep "model name" /proc/cpuinfo 2>/dev/null | head -1 | cut -d: -f2 | xargs || echo "N/A")'",'\
'"cpu_cores":"'$(nproc 2>/dev/null || echo "N/A")'",'\
'"ram_gb":'$(awk '/MemTotal/ {printf "%.1f", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo '"N/A"')'}'
"""

    def _cmd_distro(self):
        """Retorna comando shell para coletar info da distro."""
        return r"""
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo "{\"name\":\"${NAME}\",\"version\":\"${VERSION}\",\"id\":\"${ID}\",\"id_like\":\"${ID_LIKE}\",\"pretty_name\":\"${PRETTY_NAME}\"}"
elif [ -f /etc/redhat-release ]; then
    echo "{\"name\":\"$(cat /etc/redhat-release)\",\"version\":\"\",\"id\":\"rhel\",\"id_like\":\"\",\"pretty_name\":\"$(cat /etc/redhat-release)\"}"
else
    KERNEL=$(uname -r 2>/dev/null || echo "unknown")
    echo "{\"name\":\"Unknown\",\"version\":\"\",\"id\":\"unknown\",\"id_like\":\"\",\"pretty_name\":\"Linux ($KERNEL)\",\"kernel\":\"$KERNEL\"}"
fi
KERNEL=$(uname -r 2>/dev/null || echo "")
echo "{\"kernel\":\"$KERNEL\"}" > /dev/null
"""

    def _cmd_distro_combined(self):
        """Retorna comando que coleta distro + kernel em um JSON."""
        return r"""
KERNEL=$(uname -r 2>/dev/null || echo "unknown")
ARCH=$(uname -m 2>/dev/null || echo "unknown")
if [ -f /etc/os-release ]; then
    . /etc/os-release
    printf '{"name":"%s","version":"%s","id":"%s","id_like":"%s","pretty_name":"%s","kernel":"%s","arch":"%s"}' \
        "$NAME" "$VERSION" "$ID" "$ID_LIKE" "$PRETTY_NAME" "$KERNEL" "$ARCH"
elif [ -f /etc/redhat-release ]; then
    REL=$(cat /etc/redhat-release)
    printf '{"name":"%s","version":"","id":"rhel","id_like":"","pretty_name":"%s","kernel":"%s","arch":"%s"}' \
        "$REL" "$REL" "$KERNEL" "$ARCH"
else
    printf '{"name":"Unknown","version":"","id":"unknown","id_like":"","pretty_name":"Linux","kernel":"%s","arch":"%s"}' \
        "$KERNEL" "$ARCH"
fi
"""

    def _cmd_disks(self):
        """Retorna comando shell para coletar discos."""
        return r"""
lsblk -b -d -o NAME,SIZE,ROTA,MODEL 2>/dev/null | tail -n +2 | while read NAME SIZE ROTA MODEL; do
    echo "{\"drive\":\"/dev/$NAME\",\"total_gb\":$(echo "scale=1; $SIZE/1024/1024/1024" | bc 2>/dev/null || echo "0"),\"filesystem\":\"$(blkid /dev/$NAME -o value -s TYPE 2>/dev/null || echo "unknown")\",\"model\":\"$MODEL\",\"rotational\":\"$ROTA\"}"
done | paste -sd',' | sed 's/^/[/;s/$/]/'
"""

    def _cmd_network(self):
        """Retorna comando shell para coletar info de rede."""
        return r"""
ip -j addr show 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    result = []
    for iface in data:
        name = iface.get('ifname','')
        if name == 'lo': continue
        mac = iface.get('address','')
        for addr in iface.get('addr_info', []):
            if addr.get('family') == 'inet':
                result.append({'adapter_name': name, 'ip': addr.get('local',''), 'mac': mac})
    if not result:
        for iface in data:
            name = iface.get('ifname','')
            if name == 'lo': continue
            mac = iface.get('address','')
            result.append({'adapter_name': name, 'ip': '', 'mac': mac})
    print(json.dumps(result))
except:
    print('[]')
" 2>/dev/null || echo '[]'
"""

    def _cmd_network_fallback(self):
        """Fallback para coleta de rede sem ip -j."""
        return r"""
for iface in $(ls /sys/class/net/ 2>/dev/null); do
    [ "$iface" = "lo" ] && continue
    MAC=$(cat /sys/class/net/$iface/address 2>/dev/null || echo "")
    IP=$(ip -4 addr show $iface 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1)
    echo "{\"adapter_name\":\"$iface\",\"ip\":\"${IP}\",\"mac\":\"${MAC}\"}"
done | paste -sd',' | sed 's/^/[/;s/$/]/'
"""

    def collect(self, hostname):
        """Coleta dados de um host Linux via SSH. Retorna dict para checkin."""
        log.info(f"  SSH: coletando dados de {hostname}...")

        if not self._key:
            raise RuntimeError("SSH não configurado: chave privada não carregada")

        client = self._connect(hostname)
        data = {"hostname": hostname, "so_type": "linux"}

        try:
            # Hardware
            try:
                raw = self._run(client, self._cmd_hardware())
                hw = self._safe_json(raw, "hardware")
                if hw:
                    # Mapeia para o formato esperado pelo backend
                    data["hardware"] = {
                        "manufacturer": hw.get("manufacturer"),
                        "model": hw.get("model"),
                        "serial": hw.get("serial"),
                        "cpu": hw.get("cpu"),
                        "cpu_cores": hw.get("cpu_cores"),
                        "ram_gb": hw.get("ram_gb"),
                        "bios_version": None,
                        "last_boot": None,
                        "last_user": None,
                    }
                log.info(f"    ✓ hardware: CPU={hw.get('cpu','?')[:40] if hw else '?'} | RAM={hw.get('ram_gb','?') if hw else '?'} GB")
            except Exception as e:
                log.warning(f"    ⚠ hardware: {e}")
                data["hardware"] = None

            # Distro
            try:
                raw = self._run(client, self._cmd_distro_combined())
                distro = self._safe_json(raw, "distro")
                data["distro"] = distro
                if distro:
                    log.info(f"    ✓ distro: {distro.get('pretty_name', '?')} | kernel={distro.get('kernel', '?')}")
                else:
                    log.info(f"    ✓ distro: não identificada")
            except Exception as e:
                log.warning(f"    ⚠ distro: {e}")
                data["distro"] = None

            # Discos
            try:
                raw = self._run(client, self._cmd_disks())
                disks = self._safe_json(raw, "disks")
                if not isinstance(disks, list):
                    disks = [disks] if disks else []
                data["disks"] = disks
                log.info(f"    ✓ discos: {len(disks)} dispositivo(s)")
            except Exception as e:
                log.warning(f"    ⚠ discos: {e}")
                data["disks"] = []

            # Rede
            try:
                raw = self._run(client, self._cmd_network())
                network = self._safe_json(raw, "network")
                if not isinstance(network, list):
                    # Tenta fallback
                    raw2 = self._run(client, self._cmd_network_fallback())
                    network = self._safe_json(raw2, "network-fallback")
                    if not isinstance(network, list):
                        network = [network] if network else []
                data["network"] = network
                ips = [n.get("ip") for n in network if n.get("ip")]
                log.info(f"    ✓ rede: {len(network)} interface(s) {ips}")
            except Exception as e:
                log.warning(f"    ⚠ rede: {e}")
                data["network"] = []

        finally:
            client.close()

        log.info(f"  SSH ✓ {hostname}: hw={bool(data.get('hardware'))} distro={bool(data.get('distro'))} discos={len(data.get('disks',[]))} rede={len(data.get('network',[]))}")
        return data
