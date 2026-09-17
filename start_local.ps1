$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendPort = 8000
$frontendPort = 5173

function Test-PortListening([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

Set-Location $projectRoot

if (-not (Test-PortListening $backendPort)) {
    Start-Process powershell -ArgumentList @(
        '-NoExit',
        '-ExecutionPolicy', 'Bypass',
        '-Command', "Set-Location '$projectRoot'; `$env:PYTHONPATH='.'; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port $backendPort"
    )
    Write-Host "Backend dang khoi dong tai http://127.0.0.1:$backendPort" -ForegroundColor Green
} else {
    Write-Host "Backend da dang chay tai http://127.0.0.1:$backendPort" -ForegroundColor Yellow
}

if (-not (Test-PortListening $frontendPort)) {
    Start-Process powershell -ArgumentList @(
        '-NoExit',
        '-ExecutionPolicy', 'Bypass',
        '-Command', "Set-Location '$projectRoot\frontend'; npm run dev -- --host 127.0.0.1"
    )
    Write-Host "Frontend dang khoi dong tai http://127.0.0.1:$frontendPort" -ForegroundColor Green
} else {
    Write-Host "Frontend da dang chay tai http://127.0.0.1:$frontendPort" -ForegroundColor Yellow
}

Write-Host "Mo giao dien: http://127.0.0.1:$frontendPort" -ForegroundColor Cyan
