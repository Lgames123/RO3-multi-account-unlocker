@echo off
:: Batch Got Admin Check
NET SESSION >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [!] Solicitando privilegios de Administrador...
    powershell -Command "Start-Process cmd.exe -ArgumentList '/c ""%~dp0run_as_admin.bat""' -Verb RunAs"
    exit /b
)

title RO3 Multi-Client Unlocker
cd /d "%~dp0"
echo [*] Iniciando RO3 Unlocker como Administrador...
echo.

where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERRO] Python nao encontrado no PATH do sistema!
    echo Instale o Python 3.10+ e marque a opcao "Add Python to PATH".
    pause
    exit /b
)

python ro3_unlocker.py
pause
