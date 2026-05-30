param(
    [string]$NgrokExe,

    [string]$NgrokAuthtoken,

    [int]$Port = 8000,

    [int]$ApiPort = 4040
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptRoot

function Import-DotEnv {
    param([string]$EnvFile)

    if (-not (Test-Path $EnvFile)) {
        return
    }

    foreach ($line in Get-Content $EnvFile) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        $parts = $trimmed -split "=", 2
        if ($parts.Count -ne 2) {
            continue
        }
        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        if ($name) {
            [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

function Resolve-NgrokExe {
    param([string]$ProvidedPath)

    if ($ProvidedPath -and (Test-Path $ProvidedPath)) {
        return (Resolve-Path $ProvidedPath).Path
    }

    $command = Get-Command ngrok -ErrorAction SilentlyContinue
    if ($command -and $command.Source -and (Test-Path $command.Source)) {
        return $command.Source
    }

    $candidates = @(
        "$env:ProgramFiles\ngrok\ngrok.exe",
        "$env:LOCALAPPDATA\ngrok\ngrok.exe",
        "$env:LOCALAPPDATA\Programs\ngrok\ngrok.exe",
        "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Links\ngrok.exe"
    ) | Where-Object { $_ -and (Test-Path $_) }

    if ($candidates.Count -gt 0) {
        return $candidates[0]
    }

    throw "No se encontro ngrok. Instalala o pasa -NgrokExe con la ruta completa."
}

Import-DotEnv -EnvFile (Join-Path $repoRoot ".env")

if (-not $NgrokExe -and $env:NGROK_EXE) {
    $NgrokExe = $env:NGROK_EXE
}

if (-not $NgrokAuthtoken -and $env:NGROK_AUTHTOKEN) {
    $NgrokAuthtoken = $env:NGROK_AUTHTOKEN
}

$NgrokExe = Resolve-NgrokExe -ProvidedPath $NgrokExe

if ($NgrokAuthtoken) {
    & $NgrokExe config add-authtoken $NgrokAuthtoken | Out-Null
}

$existing = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "ngrok*.exe" }
foreach ($proc in $existing) {
    try {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
    } catch {
    }
}

Start-Sleep -Milliseconds 800

$stdoutLog = Join-Path $repoRoot "ngrok.stdout.log"
$stderrLog = Join-Path $repoRoot "ngrok.stderr.log"

foreach ($logFile in @($stdoutLog, $stderrLog)) {
    if (Test-Path $logFile) {
        for ($i = 0; $i -lt 5; $i++) {
            try {
                Remove-Item $logFile -Force -ErrorAction Stop
                break
            } catch {
                Start-Sleep -Milliseconds 300
            }
        }
    }
}

Write-Host "Iniciando ngrok para 127.0.0.1:$Port ..."
$ngrokProc = Start-Process -FilePath $NgrokExe `
    -ArgumentList @("http", "127.0.0.1:$Port", "--log=stdout", "--log-format=logfmt") `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

Write-Host "Proceso ngrok PID: $($ngrokProc.Id)"
Write-Host "Esperando tunel publico..."

$apiUrl = "http://127.0.0.1:$ApiPort/api/tunnels"
$api = $null
$httpsTunnel = $null

for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    try {
        $api = Invoke-RestMethod -Uri $apiUrl -Method Get -ErrorAction Stop
        $httpsTunnel = $api.tunnels | Where-Object { $_.proto -eq "https" } | Select-Object -First 1
        if ($httpsTunnel) {
            break
        }
    } catch {
    }
}

if (-not $api) {
    $stderr = if (Test-Path $stderrLog) { Get-Content $stderrLog -Raw } else { "" }
    $stdout = if (Test-Path $stdoutLog) { Get-Content $stdoutLog -Raw } else { "" }
    $details = ($stderr + "`n" + $stdout).Trim()
    if (-not $details) {
        $details = "ngrok no expuso su API local en $apiUrl. Verifica que ngrok exista y que el authtoken sea real."
    }
    throw "No fue posible iniciar ngrok. Detalles:`n$details"
}

if (-not $httpsTunnel) {
    throw "No se pudo obtener la URL publica de ngrok desde $apiUrl"
}

$baseUrl = $httpsTunnel.public_url.TrimEnd("/")

Write-Host ""
Write-Host "Ngrok publico detectado:" -ForegroundColor Green
Write-Host $baseUrl
Write-Host ""
Write-Host "Configura Twilio con estos valores:" -ForegroundColor Cyan
Write-Host "Voice / A call comes in:"
Write-Host "$baseUrl/twilio/voice"
Write-Host "  TwiML conectara el stream bidireccional a:"
Write-Host "$($baseUrl.Replace('https://', 'wss://'))/twilio/media-stream"
Write-Host ""
Write-Host "Voice / Call status changes:"
Write-Host "$baseUrl/twilio/status"
Write-Host ""
Write-Host "Messaging / A message comes in:"
Write-Host "$baseUrl/twilio/message"
Write-Host ""
Write-Host "ngrok en linea. API local: $apiUrl" -ForegroundColor Green
Write-Host "Logs:"
Write-Host "  $stdoutLog"
Write-Host "  $stderrLog"
Write-Host ""
Write-Host "Presiona Ctrl + C para cerrar esta consola."
try {
    while (-not $ngrokProc.HasExited) {
        Start-Sleep -Seconds 5
    }
} finally {
    if (-not $ngrokProc.HasExited) {
        Stop-Process -Id $ngrokProc.Id -Force -ErrorAction SilentlyContinue
    }
}
