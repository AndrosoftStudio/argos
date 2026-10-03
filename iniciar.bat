@echo off
chcp 65001 >nul
title Argos EPI v20
color 0A
setlocal EnableExtensions
cd /d "%~dp0"

REM Liga o Argos SEM Docker. Antes, rode configurar.bat uma vez.
set "PGBIN=%CD%\bin\pgsql\bin"
set "PGDATA=%CD%\dados\pgdata"
set "PG_PORTA=5433"

set "PY="
if exist "python\python.exe" set "PY=%CD%\python\python.exe"
if not defined PY if exist "venv\Scripts\python.exe" set "PY=%CD%\venv\Scripts\python.exe"
if not defined PY (
    echo.
    echo  [ERRO] Python do Argos nao encontrado.
    echo  [INFO] Execute configurar.bat primeiro!
    echo.
    pause & exit /b 1
)

"%PY%" scripts\banner.py --subtitle "Servidor local sem Docker - dashboard, IA e Cloudflare"
echo  Projeto : %CD%
echo.

REM Bibliotecas: se o configurar.bat parou no meio, completa aqui
"%PY%" -c "import cv2, ultralytics, flask, psycopg, onnxruntime, cryptography" >nul 2>&1
if not errorlevel 1 goto :libs_ok
if not exist "bin\uv.exe" goto :libs_faltando
echo  [*] Faltam bibliotecas do Argos. Instalando (requirements.txt)...
set "UV_LINK_MODE=copy"
"bin\uv.exe" pip install --python "%PY%" -r requirements.txt
"%PY%" -c "import cv2, ultralytics, flask, psycopg, onnxruntime, cryptography" >nul 2>&1
if errorlevel 1 goto :libs_faltando
echo  [OK] Bibliotecas instaladas.
echo.
:libs_ok

netstat -ano | findstr /r /c:"TCP *[^ ]*:8088 .*LISTENING" >nul
if not errorlevel 1 (
    echo  [INFO] A porta 8088 ja esta em uso ^(talvez pelo Argos do Docker^).
    echo         Este servidor vai usar a proxima porta livre, mostrada logo abaixo.
    echo.
)

REM ---------------------------------------------------------------
REM  Banco: PostgreSQL portatil do configurar.bat (porta 5433).
REM  Sem ele, usa o banco do Docker (argosepi-db em localhost:5432).
REM ---------------------------------------------------------------
set "PG_LIGUEI="
if not exist "%PGBIN%\pg_ctl.exe" goto :banco_docker
if not exist "%PGDATA%\PG_VERSION" goto :banco_docker
"%PGBIN%\pg_ctl.exe" -D "%PGDATA%" status >nul 2>&1
if not errorlevel 1 goto :banco_ligado
echo  [*] Ligando o banco (PostgreSQL portatil, porta %PG_PORTA%)...
"%PGBIN%\pg_ctl.exe" -D "%PGDATA%" -l "%CD%\dados\postgres.log" -w -t 60 start >nul
if errorlevel 1 (
    echo  [ERRO] O banco nao ligou. Veja dados\postgres.log
    echo         Se a porta %PG_PORTA% estiver em uso, feche o programa que a usa.
    pause & exit /b 1
)
set "PG_LIGUEI=1"
:banco_ligado
set "DATABASE_URL=postgresql://argos:argos@127.0.0.1:%PG_PORTA%/argosepi"
echo  [OK] Banco ligado: dados\pgdata (porta %PG_PORTA%)
goto :banco_fim
:banco_docker
REM Sem banco portatil: so segue se houver outro banco (DATABASE_URL no .env
REM ou o do Docker em localhost:5432)
if exist ".env" findstr /r /b /c:"DATABASE_URL=..*" ".env" >nul 2>&1
if exist ".env" if not errorlevel 1 (
    echo  [INFO] Usando o banco do DATABASE_URL do .env.
    goto :banco_fim
)
netstat -ano | findstr /r /c:"TCP *[^ ]*:5432 .*LISTENING" >nul
if errorlevel 1 goto :sem_banco
echo  [INFO] Sem banco portatil: usando o do Docker em localhost:5432.
:banco_fim
echo.

if not exist "models\argos_epi_v1.pt" (
    echo  [AVISO] models\argos_epi_v1.pt nao encontrado: sem deteccao de EPIs.
    echo          Copie a pasta models do pacote.
    echo.
)
if not exist "cloudflared.exe" (
    echo  [INFO] cloudflared.exe nao encontrado: o sistema funciona so na rede local.
    echo.
)

echo  [*] Iniciando servidor...
echo  [*] O endereco do painel aparece logo abaixo ^(normalmente http://localhost:8088^).
echo  [*] Para desligar: Ctrl+C ou feche esta janela.
echo.

"%PY%" run.py

echo.
echo  [!] Servidor encerrado.
if defined PG_LIGUEI (
    echo  [*] Desligando o banco...
    "%PGBIN%\pg_ctl.exe" -D "%PGDATA%" -m fast -w stop >nul 2>&1
)
pause
endlocal
exit /b 0

:libs_faltando
echo.
echo  [ERRO] Faltam bibliotecas do Argos (OpenCV, Ultralytics...).
echo         Rode configurar.bat e espere ate aparecer "TUDO PRONTO".
echo.
pause & exit /b 1

:sem_banco
echo.
echo  [ERRO] Banco de dados nao encontrado.
echo         O configurar.bat cria o banco (PostgreSQL portatil): rode-o
echo         e espere ate aparecer "TUDO PRONTO". Depois abra este de novo.
echo.
pause & exit /b 1
