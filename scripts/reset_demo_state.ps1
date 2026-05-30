$ErrorActionPreference = "Stop"

param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

$endpoint = $BaseUrl.TrimEnd("/") + "/api/admin/reset-demo"

Write-Host "Reiniciando demo en $endpoint ..." -ForegroundColor Cyan
$response = Invoke-RestMethod `
    -Method Post `
    -Uri $endpoint `
    -ContentType "application/json" `
    -Body '{"confirm": true}'

Write-Host ""
Write-Host "Demo reiniciada." -ForegroundColor Green
Write-Host ("Ordenes borradas: " + $response.result.orders_removed)
Write-Host ("Sesiones borradas: " + $response.result.sessions_removed)
Write-Host ("Eventos borrados: " + $response.result.events_removed)
