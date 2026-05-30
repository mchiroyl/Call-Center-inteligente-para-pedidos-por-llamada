@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "REPO_ROOT=%~dp0.."
echo [BOOT] Ngrok launcher iniciado...
echo [BOOT] Repo: %REPO_ROOT%
echo.

powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%\scripts\start_ngrok_for_twilio.ps1"
set "EC=%ERRORLEVEL%"
echo.
if not "%EC%"=="0" (
  echo [BOOT][ERROR] Ngrok termino con codigo %EC%.
) else (
  echo [BOOT] Ngrok finalizado correctamente.
)
echo [BOOT] La ventana queda abierta para diagnostico.

