"""
Scripts PowerShell executados remotamente via WinRM.
Cada função retorna um script PS1 como string.
Todos retornam JSON para parsing no collector.
"""


def ps_hardware():
    """Coleta dados de hardware do host."""
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


def ps_disks():
    """Coleta dados de discos/volumes."""
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


def ps_network():
    """Coleta dados de adaptadores de rede ativos."""
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


def ps_windows_license():
    """Coleta status de licença do Windows."""
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


def ps_office_license():
    """Coleta status de licença do Office (se instalado)."""
    return r"""
$ErrorActionPreference = "Stop"
try {
    $lic = $null
    # Registry first (fast)
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
    try {
        $c2r = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration" -ErrorAction Stop
        if ($c2r.ProductReleaseIds) {
            $clickToRun = $true
            $regVer = $c2r.ProductReleaseIds
        }
    } catch {}
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
            license_status      = switch ($lic.LicenseStatus) { 0 { "Unlicensed" } 1 { "Licensed" } default { "Unknown" } }
            partial_product_key = $lic.PartialProductKey
            channel             = $lic.ProductKeyChannel
            detection_method    = "WMI"
        } | ConvertTo-Json -Compress
        return
    }
    @{ product = "office"; installed = [bool]$regVer; version = $regVer; click_to_run = $clickToRun; detection_method = "Registry" } | ConvertTo-Json -Compress
} catch {
    @{ product = "office"; installed = $false; detection_method = "error"; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
"""


def ps_hotfixes():
    """Lista KBs instalados (rápido, via WMI)."""
    return r"""
$ErrorActionPreference = "SilentlyContinue"
Get-HotFix | ForEach-Object {
    @{
        kb           = $_.HotFixID
        description  = $_.Description
        installed_on = if ($_.InstalledOn) { $_.InstalledOn.ToString("yyyy-MM-dd") } else { $null }
    }
} | ConvertTo-Json -Compress
"""


def ps_pending_updates():
    """Busca updates pendentes via COM Microsoft.Update.Session.

    ATENÇÃO: pode demorar 1-3 min (contata WU/WSUS). Chamar com timeout alto.
    """
    return r"""
$ErrorActionPreference = "Stop"
$out = @{}
$pending = @()
try {
    # Última instalação com sucesso (registry)
    try {
        $lt = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\Results\Install' -EA Stop).LastSuccessTime
        $out.wu_last_success = "$lt"
    } catch { $out.wu_last_success = $null }

    $session = New-Object -ComObject Microsoft.Update.Session
    $searcher = $session.CreateUpdateSearcher()
    $result = $searcher.Search("IsInstalled=0 and IsHidden=0")
    foreach ($u in $result.Updates) {
        $kb = ""
        try { if ($u.KBArticleIDs.Count -gt 0) { $kb = "KB" + $u.KBArticleIDs[0] } } catch {}
        $pending += @{
            title    = $u.Title
            kb       = $kb
            severity = if ($u.MsrcSeverity) { "$($u.MsrcSeverity)" } else { "None" }
            reboot   = [bool]($u.InstallationBehavior.RebootBehavior -gt 1)
        }
    }
    $out.pending = $pending
    $out.ok = $true
} catch {
    $out.ok = $false
    $out.error = $_.Exception.Message
    $out.pending = @()
}
$out | ConvertTo-Json -Depth 4 -Compress
"""


def ps_software():
    """Coleta softwares instalados (via Registry — mais completo que WMI)."""
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
