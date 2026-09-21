@echo off
chcp 65001 >nul
title Argos EPI v17
color 0A
setlocal
cd /d "%~dp0"

echo.
if not exist "venv\Scripts\python.exe" (
    echo  =====================================================
    echo   ARGOS EPI v17 - AndrosoftStudio
    echo  =====================================================
    echo.
    echo  [ERRO] Ambiente virtual nao encontrado.
    echo  [INFO] Execute configurar.bat primeiro!
    echo.
    pause & exit /b 1
)

venv\Scripts\python.exe scripts\banner.py --subtitle "Inicializacao do servidor local - dashboard, IA e Cloudflare"
echo  Projeto : %CD%
echo  Backend : http://localhost:8088
echo  Frontend: http://localhost:8088
echo.

if not exist "models\yolo26n.pt" (
    echo  [AVISO] Modelo yolo26n.pt nao encontrado em models\
    echo  [INFO]  Execute configurar.bat para baixar os modelos.
    echo.
)

if not exist "cloudflared.exe" (
    echo  [INFO] cloudflared.exe nao encontrado.
    echo  [INFO] O sistema funcionara apenas na rede local.
    echo.
)

echo  [*] Iniciando servidor...
echo  [*] Acesse: http://localhost:8088
echo  [*] Pressione Ctrl+C para encerrar.
echo.

call venv\Scripts\activate.bat
python run.py
echo.
echo  [!] Servidor encerrado.
pause
endlocal
