$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonDir = Join-Path $repoRoot "components\python"
$env:CALL_CENTER_DB_PATH = Join-Path $repoRoot "data\twilio-runtime.db"
$env:UV_CACHE_DIR = Join-Path $pythonDir ".uv-cache"
$port = 8000

$listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) {
    $ownerPids = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($ownerPid in $ownerPids) {
        if ($ownerPid) {
            try {
                Write-Host "Liberando puerto $port. Cerrando proceso PID $ownerPid ..." -ForegroundColor Yellow
                Stop-Process -Id $ownerPid -Force -ErrorAction Stop
            } catch {
                Write-Host "No se pudo cerrar el proceso PID $ownerPid que ocupa el puerto $port." -ForegroundColor Red
                throw
            }
        }
    }
    Start-Sleep -Milliseconds 600
}

Write-Host "Iniciando backend FastAPI para Twilio en puerto 8000..." -ForegroundColor Cyan
Write-Host "Usando base de datos: $env:CALL_CENTER_DB_PATH" -ForegroundColor DarkCyan
Write-Host "Usando cache local de uv: $env:UV_CACHE_DIR" -ForegroundColor DarkCyan
Set-Location $pythonDir

$venvPython = Join-Path $pythonDir ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    Write-Host "Usando Python local del entorno virtual: $venvPython" -ForegroundColor Green
    & $venvPython "src/main.py"
} elseif (Test-Path "C:\Python313\python.exe") {
    Write-Host "Usando uv con Python 3.13 global." -ForegroundColor Yellow
    uv run --python C:\Python313\python.exe src/main.py
} else {
    Write-Host "Usando uv con Python por defecto del sistema." -ForegroundColor Yellow
    uv run src/main.py
}
