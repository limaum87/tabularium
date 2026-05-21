"""Tabularium Backend — Coleta manual via WinRM.

Reutiliza os scripts PowerShell do collector para coletar dados
de um host individual sob demanda.
"""

import json
import winrm


def _get_settings(db):
    """Busca configurações WinRM e DNS do banco."""
    from app.core.database import Setting

    winrm_user = db.query(Setting).filter(Setting.key == "username", Setting.category == "winrm").first()
    winrm_pass = db.query(Setting).filter(Setting.key == "password", Setting.category == "winrm").first()
    winrm_scheme = db.query(Setting).filter(Setting.key == "scheme", Setting.category == "winrm").first()
    winrm_port = db.query(Setting).filter(Setting.key == "port", Setting.category == "winrm").first()
    dns_search = db.query(Setting).filter(Setting.key == "search_domain", Setting.category == "dns").first()

    if not winrm_user or not winrm_pass:
        return None

    scheme = winrm_scheme.value if winrm_scheme else "http"
    port = int(winrm_port.value) if winrm_port else 5985
    search = dns_search.value.strip() if dns_search and dns_search.value else ""

    return {
        "username": winrm_user.value,
        "password": winrm_pass.value,
        "scheme": scheme,
        "port": port,
        "search": search,
    }


def _make_fqdn(hostname, search):
    """Monta FQDN se necessário."""
    if "." not in hostname and search:
        return f"{hostname}.{search}"
    return hostname


def _connect(hostname, cfg, operation_timeout_sec=15, read_timeout_sec=20):
    """Cria sessão WinRM."""
    endpoint = f"{cfg['scheme']}://{hostname}:{cfg['port']}"
    return winrm.Session(
        endpoint,
        auth=(cfg["username"], cfg["password"]),
        transport="ntlm",
        server_cert_validation="ignore",
        operation_timeout_sec=operation_timeout_sec,
        read_timeout_sec=read_timeout_sec,
    )


def _run_ps_with_timeout(session, script, label="", timeout_sec=45):
    """Executa PowerShell com timeout aplicado no próprio script.

    Adiciona um [System.Threading.Thread]::Sleep watchdog para evitar
    que o script PS trave indefinidamente.
    """
    import threading
    import queue as queue_mod

    result_q = queue_mod.Queue()

    def _worker():
        try:
            result = session.run_ps(script)
            result_q.put(result)
        except Exception as e:
            result_q.put(e)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout_sec)

    if t.is_alive():
        raise TimeoutError(f"Timeout ({timeout_sec}s) ao executar {label}")

    result = result_q.get()
    if isinstance(result, Exception):
        raise result

    if result.status_code != 0:
        stderr = result.std_err.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"PowerShell error ({label}): {stderr[:200]}")
    return result.std_out.decode("utf-8", errors="replace").strip()


def _run_ps(session, script):
    """Executa PowerShell e retorna stdout."""
    result = session.run_ps(script)
    if result.status_code != 0:
        stderr = result.std_err.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"PowerShell error: {stderr[:200]}")
    return result.std_out.decode("utf-8", errors="replace").strip()


def _safe_json(raw, context=""):
    """Parse JSON seguro."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# ---- Scripts PowerShell (mesmos do collector) ----

def _ps_hardware():
    return r"""
$ErrorActionPreference = "SilentlyContinue"
$obj = @{
    manufacturer = (Get-CimInstance Win32_ComputerSystem).Manufacturer
    model        = (Get-CimInstance Win32_ComputerSystem).Model
    serial       = (Get-CimInstance Win32_BIOS).SerialNumber
    cpu          = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name
    ram_gb       = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
    bios_version = (Get-CimInstance Win32_BIOS).SMBIOSBIOSVersion
    last_boot    = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString("yyyy-MM-dd HH:mm:ss")
    last_user    = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI' -EA 0).LastLoggedOnUser
}
$obj | ConvertTo-Json -Compress
"""


def _ps_disks():
    return r"""
$ErrorActionPreference = "SilentlyContinue"
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
    @{
        drive      = $_.DeviceID
        total_gb   = [math]::Round($_.Size / 1GB, 1)
        free_gb    = [math]::Round($_.FreeSpace / 1GB, 1)
        filesystem = $_.FileSystem
    }
} | ConvertTo-Json -Compress
"""


def _ps_network():
    return r"""
$ErrorActionPreference = "SilentlyContinue"
Get-CimInstance Win32_NetworkAdapterConfiguration -Filter "IPEnabled=True" | ForEach-Object {
    @{
        ip            = ($_.IPAddress | Where-Object { $_ -match "\d+\.\d+\.\d+\.\d+" }) -join ","
        mac           = $_.MACAddress
        gateway       = ($_.DefaultIPGateway -join ",")
        dns           = ($_.DNSServerSearchOrder -join ",")
        adapter_name  = (Get-CimInstance Win32_NetworkAdapter -Filter "Index=$($_.SettingID.Split('=')[-1].Trim('}'))").NetConnectionID
    }
} | ConvertTo-Json -Compress
"""


def _ps_windows_license():
    return r"""
$ErrorActionPreference = "Stop"
try {
    $os = Get-CimInstance Win32_OperatingSystem
    $lic = $null
    $oem = $null
    try {
        $lic = Get-CimInstance SoftwareLicensingProduct -Filter "PartialProductKey <> null" -ErrorAction Stop |
            Where-Object { $_.Name -like "*Windows*" } |
            Select-Object -First 1
    } catch {}
    try {
        $oem = (Get-CimInstance SoftwareLicensingService -ErrorAction Stop).OA3xOriginalProductKey
    } catch {}
    $obj = @{
        product             = "windows"
        edition             = $os.Caption -replace "Microsoft Windows ", ""
        version             = if ($os.Version -match "^10\.0\.(\d+)") { $matches[1] } else { $os.Version }
        build               = $os.BuildNumber
        license_channel     = if ($lic) { $lic.ProductKeyChannel } else { "" }
        license_status      = switch ($lic.LicenseStatus) { 0 { "Unlicensed" } 1 { "Licensed" } 2 { "OOBGrace" } 3 { "OOTGrace" } 4 { "NonGenuine" } 5 { "Notification" } 6 { "ExtendedGrace" } default { "Unknown ($($lic.LicenseStatus))" } }
        partial_product_key = if ($lic) { $lic.PartialProductKey } else { "" }
        oem_key_found       = [bool]$oem
    }
    $obj | ConvertTo-Json -Compress
} catch {
    @{ product = "windows"; license_status = "error"; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
"""


def _ps_office_license():
    return r"""
$ErrorActionPreference = "Stop"
try {
    $lic = $null
    # Registry first (fast) — detecta versão instalada
    $regPaths = @(
        "HKLM:\SOFTWARE\Microsoft\Office",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Office"
    )
    $regVer = ""
    $clickToRun = $false
    foreach ($rp in $regPaths) {
        try {
            $sub = Get-ChildItem $rp -ErrorAction Stop | Where-Object { $_.Name -match "\\1[56789]\." -or $_.Name -match "\\16\." }
            if ($sub) {
                $regVer = ($sub[-1].Name -split "\\")[-1]
                break
            }
        } catch {}
    }
    # Check Click-to-Run
    try {
        $c2r = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration" -ErrorAction Stop
        if ($c2r.ProductReleaseIds) {
            $clickToRun = $true
            $regVer = $c2r.ProductReleaseIds
        }
    } catch {}
    # Try WMI (can be slow, but gives license status)
    try {
        $lic = Get-CimInstance SoftwareLicensingProduct -Filter "PartialProductKey <> null" -ErrorAction Stop |
            Where-Object { $_.Name -like "*Office*" -or $_.Name -like "*365*" } |
            Select-Object -First 1
    } catch {}
    if ($lic) {
        @{
            product             = "office"
            installed           = $true
            version             = ($lic.Name -replace "Office ","" -replace " .*","").Trim()
            edition             = $lic.Name
            license_status      = switch ($lic.LicenseStatus) { 0 { "Unlicensed" } 1 { "Licensed" } 2 { "OOBGrace" } 3 { "OOTGrace" } 4 { "NonGenuine" } 5 { "Notification" } 6 { "ExtendedGrace" } default { "Unknown ($($lic.LicenseStatus))" } }
            partial_product_key = $lic.PartialProductKey
            channel             = $lic.ProductKeyChannel
            detection_method    = "WMI"
        } | ConvertTo-Json -Compress
        return
    }
    # Fallback: Registry only
    @{ product = "office"; installed = [bool]$regVer; version = $regVer; click_to_run = $clickToRun; detection_method = "Registry" } | ConvertTo-Json -Compress
} catch {
    @{ product = "office"; installed = $false; detection_method = "error"; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
"""


def _ps_software():
    return r"""
$ErrorActionPreference = "SilentlyContinue"
$paths = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
)
$items = @()
foreach ($path in $paths) {
    $items += Get-ItemProperty $path -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName }
}
$items | Sort-Object DisplayName -Unique | ForEach-Object {
    @{
        name              = $_.DisplayName
        version           = $_.DisplayVersion
        publisher         = $_.Publisher
        install_date      = $_.InstallDate
        install_location  = $_.InstallLocation
    }
} | ConvertTo-Json -Compress
"""


def _ps_anydesk():
    return r"""
$ErrorActionPreference = "SilentlyContinue"
$r = @{}
$adId = $null
# 1. ProgramData (instalacao global)
foreach ($f in 'C:\ProgramData\AnyDesk\system.conf') {
    try {
        $m = Select-String 'ad\.anynet\.id\s*=\s*(\d+)' $f -EA Stop
        if ($m) { $adId = $m.Matches.Groups[1].Value; break }
    } catch {}
}
# 2. Per-user (ultimo usuario logado)
if (-not $adId) {
    foreach ($d in Get-ChildItem 'C:\Users' -Directory -EA 0) {
        $f = Join-Path $d.FullName 'AppData\Roaming\AnyDesk\system.conf'
        if (Test-Path $f) {
            try {
                $m = Select-String 'ad\.anynet\.id\s*=\s*(\d+)' $f -EA Stop
                if ($m) { $adId = $m.Matches.Groups[1].Value; break }
            } catch {}
        }
    }
}
$r.anydesk_id = if ($adId) { $adId } else { $null }
# Alias
$alias = $null
foreach ($p in 'HKLM:\SOFTWARE\AnyDesk','HKLM:\SOFTWARE\WOW6432Node\AnyDesk') {
    try { $a = (Get-ItemProperty $p -EA Stop).Alias; if ($a) { $alias = $a; break } } catch {}
}
if (-not $alias) {
    foreach ($d in Get-ChildItem 'C:\Users' -Directory -EA 0) {
        $f = Join-Path $d.FullName 'AppData\Roaming\AnyDesk\user.conf'
        if (Test-Path $f) {
            try {
                $m = Select-String 'ad\.anynet\.alias\s*=\s*(.+)' $f -EA Stop
                if ($m) { $alias = $m.Matches.Groups[1].Value.Trim(); break }
            } catch {}
        }
    }
}
$r.anydesk_alias = if ($alias) { $alias } else { '' }
# Versao
$adv = $null
foreach ($p in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*') {
    try { $i = Get-ItemProperty $p -EA 0 | ? { $_.DisplayName -like '*AnyDesk*' } | Select -First 1; if ($i) { $adv = $i.DisplayVersion; break } } catch {}
}
$r.anydesk_version = if ($adv) { $adv } else { '' }
$r | ConvertTo-Json -Compress
"""


def _ps_ultravnc():
    return r"""
$ErrorActionPreference = "SilentlyContinue"
$r = @{ installed = $false; port = $null; version = "" }
foreach ($p in "HKLM:\SOFTWARE\UltraVNC\server","HKLM:\SOFTWARE\WOW6432Node\UltraVNC\server") {
    try { $v = Get-ItemProperty $p -EA Stop; if ($v) { $r.installed = $true; if ($v.PortNumber) { $r.port = [int]$v.PortNumber }; break } } catch {}
}
if (-not $r.installed) { try { $s = Get-Service uvnc_service -EA Stop; if ($s) { $r.installed = $true; $r.port = 5900 } } catch {} }
if ($r.installed -and -not $r.version) {
    foreach ($p in "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*","HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*") {
        try { $i = Get-ItemProperty $p -EA SilentlyContinue | ? { $_.DisplayName -like "*UltraVNC*" } | Select -First 1; if ($i) { $r.version = $i.DisplayVersion; break } } catch {}
    }
}
$r | ConvertTo-Json -Compress
"""


def collect_host(hostname, cfg):
    """Coleta todos os dados de um host via WinRM. Retorna dict com resultados."""
    target = _make_fqdn(hostname, cfg.get("search", ""))
    session = _connect(target, cfg)

    data = {"hostname": hostname}
    errors = []
    debug = []

    # Hardware
    debug.append("📡 Coletando hardware...")
    try:
        raw = _run_ps(session, _ps_hardware())
        data["hardware"] = _safe_json(raw, "hardware")
        debug.append(f"✓ Hardware OK")
    except Exception as e:
        errors.append(f"hardware: {e}")
        data["hardware"] = None
        debug.append(f"✗ Hardware falhou: {str(e)[:80]}")

    # Discos
    debug.append("💾 Coletando discos...")
    try:
        raw = _run_ps(session, _ps_disks())
        parsed = _safe_json(raw, "disks")
        data["disks"] = parsed if isinstance(parsed, list) else [parsed] if parsed else []
        debug.append(f"✓ {len(data['disks'])} disco(s)")
    except Exception as e:
        errors.append(f"discos: {e}")
        data["disks"] = []
        debug.append(f"✗ Discos falhou: {str(e)[:80]}")

    # Rede
    debug.append("🌐 Coletando rede...")
    try:
        raw = _run_ps(session, _ps_network())
        parsed = _safe_json(raw, "network")
        data["network"] = parsed if isinstance(parsed, list) else [parsed] if parsed else []
        debug.append(f"✓ {len(data['network'])} adaptador(es)")
    except Exception as e:
        errors.append(f"rede: {e}")
        data["network"] = []
        debug.append(f"✗ Rede falhou: {str(e)[:80]}")

    # Licenças
    debug.append("🔑 Coletando licenças...")
    licenses = []
    for ps_func, label in [(_ps_windows_license, "windows"), (_ps_office_license, "office")]:
        try:
            raw = _run_ps(session, ps_func())
            parsed = _safe_json(raw, f"license-{label}")
            if parsed:
                if isinstance(parsed, list):
                    licenses.extend(parsed)
                else:
                    licenses.append(parsed)
            debug.append(f"✓ Licença {label}")
        except Exception as e:
            errors.append(f"licença {label}: {e}")
            debug.append(f"✗ Licença {label}: {str(e)[:80]}")
    data["licenses"] = licenses

    # Software (mais demorado)
    debug.append("📦 Coletando softwares...")
    try:
        raw = _run_ps(session, _ps_software())
        parsed = _safe_json(raw, "software")
        data["software"] = parsed if isinstance(parsed, list) else [parsed] if parsed else []
        debug.append(f"✓ {len(data['software'])} software(s)")
    except Exception as e:
        errors.append(f"software: {e}")
        data["software"] = []
        debug.append(f"✗ Software falhou: {str(e)[:80]}")

    data["errors"] = errors
    data["debug"] = debug
    return data
