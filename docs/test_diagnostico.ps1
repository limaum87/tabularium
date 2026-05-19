# Diagnostico — onde estao AnyDesk e UltraVNC neste host?
# Rodar: powershell -ExecutionPolicy Bypass -File test_diagnostico.ps1

Write-Host "=== ANYDESK ===" -ForegroundColor Cyan

# Registry
Write-Host "`n[Registry HKLM\SOFTWARE\AnyDesk]"
try { Get-ItemProperty "HKLM:\SOFTWARE\AnyDesk" -EA Stop | Format-List } catch { Write-Host "  Nao encontrado" }
Write-Host "[Registry WOW6432Node\AnyDesk]"
try { Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\AnyDesk" -EA Stop | Format-List } catch { Write-Host "  Nao encontrado" }

# Config files
Write-Host "`n[Config C:\ProgramData\AnyDesk]"
try {
    $files = Get-ChildItem "C:\ProgramData\AnyDesk" -EA Stop
    foreach ($f in $files) { Write-Host "  $($f.Name)" }
    Write-Host "--- system.conf ---"
    Get-Content "C:\ProgramData\AnyDesk\system.conf" -EA SilentlyContinue | ForEach-Object { Write-Host "  $_" }
    Write-Host "--- service.conf ---"
    Get-Content "C:\ProgramData\AnyDesk\service.conf" -EA SilentlyContinue | ForEach-Object { Write-Host "  $_" }
} catch { Write-Host "  Diretorio nao encontrado" }

# Servico
Write-Host "`n[Servico]"
Get-Service *anydesk* -EA SilentlyContinue | Format-Table Name,Status,DisplayName

# Uninstall entry
Write-Host "[Uninstall Registry]"
Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*","HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*" -EA SilentlyContinue |
    Where-Object { $_.DisplayName -like "*AnyDesk*" } |
    Format-List DisplayName,DisplayVersion,InstallLocation

# Exe path
Write-Host "[Exe no PATH]"
Get-Command AnyDesk -EA SilentlyContinue | Format-List Source
Get-ChildItem "C:\Program Files\AnyDesk\AnyDesk.exe","C:\Program Files (x86)\AnyDesk\AnyDesk.exe" -EA SilentlyContinue | ForEach-Object { Write-Host "  Encontrado: $($_.FullName)" }


Write-Host "`n=== ULTRAVNC ===" -ForegroundColor Cyan

# Registry
Write-Host "`n[Registry HKLM\SOFTWARE\UltraVNC]"
try { Get-ItemProperty "HKLM:\SOFTWARE\UltraVNC" -EA Stop | Format-List } catch { Write-Host "  Nao encontrado" }
Write-Host "[Registry HKLM\SOFTWARE\UltraVNC\server]"
try { Get-ItemProperty "HKLM:\SOFTWARE\UltraVNC\server" -EA Stop | Format-List } catch { Write-Host "  Nao encontrado" }
Write-Host "[Registry WOW6432Node\UltraVNC]"
try { Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\UltraVNC" -EA Stop | Format-List } catch { Write-Host "  Nao encontrado" }
Write-Host "[Registry WOW6432Node\UltraVNC\server]"
try { Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\UltraVNC\server" -EA Stop | Format-List } catch { Write-Host "  Nao encontrado" }

# Servico
Write-Host "`n[Servico]"
Get-Service *vnc* -EA SilentlyContinue | Format-Table Name,Status,DisplayName
Get-Service *uvnc* -EA SilentlyContinue | Format-Table Name,Status,DisplayName
Get-Service *ultravnc* -EA SilentlyContinue | Format-Table Name,Status,DisplayName

# Uninstall entry
Write-Host "[Uninstall Registry]"
Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*","HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*" -EA SilentlyContinue |
    Where-Object { $_.DisplayName -like "*UltraVNC*" -or $_.DisplayName -like "*VNC*" } |
    Format-List DisplayName,DisplayVersion,InstallLocation

# Porta 5900
Write-Host "[Porta 5900]"
try {
    $listening = Get-NetTCPConnection -LocalPort 5900 -EA Stop
    $listening | Format-Table LocalAddress,LocalPort,State,OwningProcess
} catch { Write-Host "  Porta 5900 nao esta em uso" }

Write-Host "`n=== FIM ===" -ForegroundColor Green
