# Teste rapido — onde esta o AnyDesk ID?
# Rodar: powershell -ExecutionPolicy Bypass -File test_anydesk_debug.ps1

Write-Host "=== ANYDESK service.conf ===" -ForegroundColor Cyan

# 1. System-wide
Write-Host "`n[1] C:\ProgramData\AnyDesk\service.conf"
try {
    $c = Get-Content "C:\ProgramData\AnyDesk\service.conf" -EA Stop
    Write-Host "  ENCONTRADO — conteudo:" -ForegroundColor Green
    $c | ForEach-Object { Write-Host "  $_" }
} catch {
    Write-Host "  NAO ENCONTRADO" -ForegroundColor Red
}

# 2. Todos os usuarios
Write-Host "`n[2] Per-user (C:\Users\*\AppData\Roaming\AnyDesk\service.conf)"
$found = $false
foreach ($dir in Get-ChildItem "C:\Users" -Directory -EA SilentlyContinue) {
    $f = Join-Path $dir.FullName "AppData\Roaming\AnyDesk\service.conf"
    if (Test-Path $f) {
        $found = $true
        Write-Host "  ENCONTRADO: $f" -ForegroundColor Green
        $c = Get-Content $f -EA SilentlyContinue
        $c | ForEach-Object { Write-Host "    $_" }
    }
}
if (-not $found) {
    Write-Host "  Nenhum encontrado" -ForegroundColor Red
}

# 3. Ultimo usuario logado (registry)
Write-Host "`n[3] Ultimo usuario logado"
try {
    $lastUser = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI" -EA Stop).LastLoggedOnUser
    Write-Host "  LastLoggedOnUser: $lastUser"
} catch {
    Write-Host "  Nao encontrado no registry"
}
try {
    $lastUserSAM = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI" -EA Stop).LastLoggedOnUserSID
    Write-Host "  SID: $lastUserSAM"
} catch {}

Write-Host "`n=== FIM ===" -ForegroundColor Green
