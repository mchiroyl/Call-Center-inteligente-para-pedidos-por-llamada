@echo off
setlocal EnableExtensions
chcp 65001 >nul

if /I not "%~1"=="--runner" (
    powershell -NoLogo -NoProfile -WindowStyle Hidden -Command "Start-Process -WindowStyle Hidden cmd.exe -ArgumentList '/c','\"%~f0\" --runner %~1'"
    exit /b 0
)

set "REPO_ROOT=%~dp0"
set "MODE=%~2"
pushd "%REPO_ROOT%"

echo.
echo ============================================
echo   Call Center IA - Inicio automatico
echo ============================================
echo.

if not exist ".env" (
    echo [ADVERTENCIA] No existe archivo .env en la raiz del proyecto.
    echo [ADVERTENCIA] Copia .env.example a .env y coloca tus claves reales antes de probar voz/IA.
    echo.
)

where uv >nul 2>&1
if errorlevel 1 (
    echo [ERROR] No se encontro uv en PATH.
    echo Instala uv y vuelve a ejecutar este archivo.
    goto :fail
)

where corepack >nul 2>&1
if errorlevel 1 (
    echo [ERROR] No se encontro corepack en PATH.
    echo Instala Node.js y vuelve a ejecutar este archivo.
    goto :fail
)

if /I "%MODE%"=="--fast" goto LAUNCH

echo [1/5] Instalando dependencias web...
call corepack pnpm --dir components\web install
if errorlevel 1 goto :fail

echo.
echo [2/5] Instalando dependencias Python...
set "VENV_PY=%REPO_ROOT%components\python\.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import fastapi,langchain_openai,langgraph,websockets" >nul 2>&1
    if not errorlevel 1 (
        echo [OK] Entorno Python ya listo. Se omite uv sync.
        goto PY_READY
    )
)
set "UV_SYNC_LOG=%REPO_ROOT%uv-sync.log"
set "UV_SYNC_ERR=%REPO_ROOT%uv-sync.err.log"
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p = Start-Process -FilePath 'uv' -ArgumentList @('sync','--project','components\python','--dev') -RedirectStandardOutput '%UV_SYNC_LOG%' -RedirectStandardError '%UV_SYNC_ERR%' -PassThru; " ^
  "if (-not (Wait-Process -Id $p.Id -Timeout 180 -ErrorAction SilentlyContinue)) { " ^
  "  try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch {} ; exit 124 " ^
  "} ; exit $p.ExitCode"
set "UV_SYNC_EXIT=%ERRORLEVEL%"
if "%UV_SYNC_EXIT%"=="124" (
    echo [ADVERTENCIA] uv sync excedio 180s y fue detenido para evitar bloqueo.
    echo [ADVERTENCIA] Se verificara si el entorno actual ya es utilizable...
    call uv run --project components\python python -c "import fastapi,langchain_openai,langgraph,websockets"
    if errorlevel 1 (
        echo [ERROR] El entorno Python no quedo listo tras abortar uv sync.
        echo [ERROR] Revisa %UV_SYNC_LOG% y %UV_SYNC_ERR% y ejecuta manualmente:
        echo        uv sync --project components\python --dev
        goto :fail
    )
    echo [OK] El entorno ya estaba utilizable. Continuando...
) else (
    if not "%UV_SYNC_EXIT%"=="0" (
        echo [ERROR] uv sync fallo con codigo %UV_SYNC_EXIT%.
        echo Revisa el log: %UV_SYNC_LOG%
        goto :fail
    )
)

:PY_READY
echo.
echo [3/5] Construyendo frontend...
call corepack pnpm --dir components\web build
if errorlevel 1 goto :fail

:LAUNCH
if /I "%MODE%"=="--fast" (
  echo [MODO RAPIDO] Se omitieron install/sync/build por solicitud explicita ^(--fast^).
)

echo.
echo [4/5] Abriendo backend en nueva terminal...
start "Call Center Backend" cmd /k "%REPO_ROOT%scripts\launch_backend.cmd"

echo Esperando a que el backend responda en http://127.0.0.1:8000 ...
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ready = $false; " ^
  "for ($i = 0; $i -lt 30; $i++) { " ^
  "  Start-Sleep -Seconds 1; " ^
  "  try { " ^
  "    $r = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8000' -TimeoutSec 3; " ^
  "    if ($r.StatusCode -ge 200) { $ready = $true; break } " ^
  "  } catch {} " ^
  "} " ^
  "if ($ready) { exit 0 } else { exit 1 }"
if errorlevel 1 (
    echo [ADVERTENCIA] El backend no confirmo respuesta HTTP a tiempo. Se continuara para no bloquear el arranque.
) else (
    echo [OK] Backend respondiendo en puerto 8000.
)

echo.
echo [5/5] Abriendo ngrok / Twilio en nueva terminal...
start "Call Center Ngrok" cmd /k "%REPO_ROOT%scripts\launch_ngrok.cmd"

echo Esperando unos segundos para que ngrok exponga la URL publica...
timeout /t 4 /nobreak >nul

echo.
echo Buscando URL de ngrok en ngrok.stdout.log (esperando hasta 30s)...
set "NGROK_URL="
set /a tries=0
:CHECK_NGROK
for /f "usebackq delims=" %%L in (`powershell -NoProfile -Command "if (Test-Path 'ngrok.stdout.log') { Select-String -Path 'ngrok.stdout.log' -Pattern 'url=https://[^\s]+' | Select-Object -Last 1 | ForEach-Object { $m=[regex]::Match($_.Line, 'url=https://[^\s]+'); if ($m.Success) { $m.Groups[0].Value } } }"`) do set "NGROK_URL=%%L"
if defined NGROK_URL goto NGROK_FOUND
set /a tries+=1
if %tries% LSS 30 (
    timeout /t 1 /nobreak >nul
    goto CHECK_NGROK
)
echo No se encontro URL de ngrok en ngrok.stdout.log despues de 30s.
echo Ultimas lineas de ngrok.stdout.log (para diagnostico):
powershell -NoProfile -Command "if (Test-Path 'ngrok.stdout.log') { Get-Content -Path 'ngrok.stdout.log' -Tail 20 } else { Write-Host 'ngrok.stdout.log no existe' }"
echo Revisa si la ventana de ngrok esta abierta o el archivo ngrok.stdout.log.
goto NGROK_END
:NGROK_FOUND
rem NGROK_URL contiene "url=https://..." - recortar prefijo
set "NGROK_URL=%NGROK_URL:~4%"
echo ngrok detectado: %NGROK_URL%
echo Asegurate de copiar esta URL en Twilio si no la viste en la otra consola.
:NGROK_END

echo.
echo Abriendo interfaz principal en el navegador...
start "" "http://127.0.0.1:8000"

echo.
echo Proyecto iniciado.
echo.
echo Rutas principales:
echo - http://127.0.0.1:8000
echo - http://127.0.0.1:8000/login
echo - http://127.0.0.1:8000/operations
echo - http://127.0.0.1:8000/admin
echo.
echo Credenciales demo internas por defecto:
echo - admin / admin123
echo - cocina / cocina123
echo - caja / caja123
echo - operaciones / operaciones123
echo.
echo Si la terminal de ngrok muestra una URL publica nueva,
echo actualiza Twilio con:
echo - /twilio/voice
echo - /twilio/status
echo - /twilio/message
echo.
goto :done

:fail
echo.
echo [FALLO] El arranque se detuvo por un error.
echo Revisa el mensaje mostrado arriba.
popd
exit /b 1

:done
popd
exit /b 0
