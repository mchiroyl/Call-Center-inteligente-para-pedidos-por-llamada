$ErrorActionPreference = "Stop"

param(
    [string]$NewFolderName = "Call-Center-Inteligente-para-Pedidos-por-Llamada"
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$parentDir = Split-Path -Parent $projectRoot
$currentName = Split-Path -Leaf $projectRoot
$targetPath = Join-Path $parentDir $NewFolderName

if ($currentName -eq $NewFolderName) {
    Write-Host "La carpeta del proyecto ya tiene el nombre objetivo." -ForegroundColor Green
    exit 0
}

if (Test-Path $targetPath) {
    throw "Ya existe una carpeta con el nombre destino: $targetPath"
}

Write-Host "Renombrando carpeta del proyecto..." -ForegroundColor Cyan
Write-Host "Actual: $currentName" -ForegroundColor DarkCyan
Write-Host "Nuevo:  $NewFolderName" -ForegroundColor DarkCyan

Set-Location $parentDir
Rename-Item -LiteralPath $projectRoot -NewName $NewFolderName

Write-Host "Renombrado completado:" -ForegroundColor Green
Write-Host $targetPath
