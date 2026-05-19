$ErrorActionPreference = "SilentlyContinue"
$r = @{}
$adId = $null
foreach ($p in "HKLM:\SOFTWARE\AnyDesk","HKLM:\SOFTWARE\WOW6432Node\AnyDesk") {
    try { $v = (Get-ItemProperty $p -EA Stop).ClientID; if ($v) { $adId = $v; break } } catch {}
}
if (-not $adId) {
    foreach ($f in "C:\ProgramData\AnyDesk\system.conf","C:\ProgramData\AnyDesk\service.conf") {
        try {
            $c = Get-Content $f -EA Stop
            $m = $c | Where-Object { $_ -match "ad\.anynet\.id\s*=\s*\"?(\d+)" }
            if ($m -and $Matches[1]) { $adId = $Matches[1]; break }
        } catch {}
    }
}
$r.anydesk_id = if ($adId) { [string]$adId } else { $null }
$alias = $null
foreach ($p in "HKLM:\SOFTWARE\AnyDesk","HKLM:\SOFTWARE\WOW6432Node\AnyDesk") {
    try { $a = (Get-ItemProperty $p -EA Stop).Alias; if ($a) { $alias = $a; break } } catch {}
}
$r.anydesk_alias = if ($alias) { $alias } else { "" }
$adv = $null
foreach ($p in "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*","HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*") {
    try { $i = Get-ItemProperty $p -EA SilentlyContinue | ? { $_.DisplayName -like "*AnyDesk*" } | Select -First 1; if ($i) { $adv = $i.DisplayVersion; break } } catch {}
}
$r.anydesk_version = if ($adv) { $adv } else { "" }
$r | ConvertTo-Json -Compress