@echo off
chcp 65001 >nul
title Argos EPI v20 - Atualizador
color 0B
setlocal EnableExtensions
cd /d "%~dp0"
echo ======================================================
echo   ATUALIZADOR - Argos EPI v20 (sem Docker)
echo ======================================================
echo.
set "PY="
if exist "python\python.exe" set "PY=%CD%\python\python.exe"
if not defined PY if exist "venv\Scripts\python.exe" set "PY=%CD%\venv\Scripts\python.exe"
if not defined PY (
    echo [ERRO] Execute configurar.bat primeiro.
    pause
    exit /b 1
)
if not exist "bin\uv.exe" (
    echo [ERRO] bin\uv.exe nao encontrado. Execute configurar.bat.
    pause
    exit /b 1
)

echo [*] Sincronizando dependencias com o requirements.txt...
set "UV_LINK_MODE=copy"
bin\uv.exe pip install --python "%PY%" -r requirements.txt
echo.
echo [CONCLUIDO] Execute iniciar.bat para ligar.
pause
