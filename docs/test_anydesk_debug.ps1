# test_anydesk_debug.ps1
# Rodar: powershell -ExecutionPolicy Bypass -File test_anydesk_debug.ps1

Write-Host '=== ANYDESK service.conf ===' -ForegroundColor Cyan

Write-Host ''
Write-Host '[1] C:\ProgramData\AnyDesk\service.conf'
try {
    $c = Get-Content 'C:\ProgramData\AnyDesk\service.conf' -EA Stop
    Write-Host '  ENCONTRADO' -ForegroundColor Green
    $c | ForEach-Object { Write-Host "  $_" }
} catch {
    Write-Host '  NAO ENCONTRADO' -ForegroundColor Red
}

Write-Host ''
Write-Host '[2] Per-user (C:\Users\*\AppData\Roaming\AnyDesk\service.conf)'
$found = $false
foreach ($dir in Get-ChildItem 'C:\Users' -Directory -EA SilentlyContinue) {
    $f = Join-Path $dir.FullName 'AppData\Roaming\AnyDesk\service.conf'
    if (Test-Path $f) {
        $found = $true
        Write-Host "  ENCONTRADO: $f" -ForegroundColor Green
        $c = Get-Content $f -EA SilentlyContinue
        $c | ForEach-Object { Write-Host "    $_" }
    }
}
if (-not $found) {
    Write-Host '  Nenhum encontrado' -ForegroundColor Red
}

Write-Host ''
Write-Host '[3] Ultimo usuario logado'
try {
    $lastUser = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI' -EA Stop).LastLoggedOnUser
    Write-Host "  LastLoggedOnUser: $lastUser"
} catch {
    Write-Host '  Nao encontrado'
}

Write-Host ''
Write-Host '=== FIM ===' -ForegroundColor Green
