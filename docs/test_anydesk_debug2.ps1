# test_anydesk_debug2.ps1
# Rodar: powershell -ExecutionPolicy Bypass -File test_anydesk_debug2.ps1

Write-Host '=== PROCURANDO ANYDESK ID ===' -ForegroundColor Cyan

# 1. System-wide: system.conf
Write-Host ''
Write-Host '[1] C:\ProgramData\AnyDesk\system.conf'
try {
    $c = Get-Content 'C:\ProgramData\AnyDesk\system.conf' -EA Stop
    Write-Host '  ENCONTRADO' -ForegroundColor Green
    $c | ForEach-Object { Write-Host "  $_" }
} catch {
    Write-Host '  NAO ENCONTRADO' -ForegroundColor Red
}

# 2. System-wide: service.conf
Write-Host ''
Write-Host '[2] C:\ProgramData\AnyDesk\service.conf'
try {
    $c = Get-Content 'C:\ProgramData\AnyDesk\service.conf' -EA Stop
    Write-Host '  ENCONTRADO' -ForegroundColor Green
    $c | ForEach-Object { Write-Host "  $_" }
} catch {
    Write-Host '  NAO ENCONTRADO' -ForegroundColor Red
}

# 3. System-wide: user.conf
Write-Host ''
Write-Host '[3] C:\ProgramData\AnyDesk\user.conf'
try {
    $c = Get-Content 'C:\ProgramData\AnyDesk\user.conf' -EA Stop
    Write-Host '  ENCONTRADO' -ForegroundColor Green
    $c | ForEach-Object { Write-Host "  $_" }
} catch {
    Write-Host '  NAO ENCONTRADO' -ForegroundColor Red
}

# 4. Todos os .conf em ProgramData
Write-Host ''
Write-Host '[4] Arquivos em C:\ProgramData\AnyDesk\'
try {
    Get-ChildItem 'C:\ProgramData\AnyDesk\' -EA Stop | ForEach-Object {
        Write-Host "  $($_.Name)  ($([math]::Round($_.Length/1KB,1)) KB)"
    }
} catch {
    Write-Host '  Diretorio nao encontrado' -ForegroundColor Red
}

# 5. Per-user: service.conf e system.conf
Write-Host ''
Write-Host '[5] Per-user service.conf E system.conf'
foreach ($dir in Get-ChildItem 'C:\Users' -Directory -EA SilentlyContinue) {
    foreach ($fname in 'service.conf','system.conf','user.conf') {
        $f = Join-Path $dir.FullName "AppData\Roaming\AnyDesk\$fname"
        if (Test-Path $f) {
            Write-Host "  ENCONTRADO: $f" -ForegroundColor Green
            $c = Get-Content $f -EA SilentlyContinue
            $c | Where-Object { $_ -match 'ad\.anynet\.id' -or $_ -match 'id' } | ForEach-Object { Write-Host "    $_" }
        }
    }
}

# 6. CLI
Write-Host ''
Write-Host '[6] CLI anydesk --get-id'
foreach ($p in 'C:\Program Files (x86)\AnyDesk\AnyDesk.exe','C:\Program Files\AnyDesk\AnyDesk.exe') {
    if (Test-Path $p) {
        Write-Host "  Exe encontrado: $p"
        try {
            $id = & $p --get-id 2>&1
            Write-Host "  ID: $id" -ForegroundColor Green
        } catch {
            Write-Host "  Erro: $_"
        }
    }
}

Write-Host ''
Write-Host '=== FIM ===' -ForegroundColor Green
