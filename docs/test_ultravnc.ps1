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