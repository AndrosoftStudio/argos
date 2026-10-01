@echo off
chcp 65001 >nul
title Argos EPI v20 - Copiar banco do Docker
color 0B
setlocal EnableExtensions
cd /d "%~dp0"

REM Copia contas, funcionarios, rostos, areas e auditoria do banco do Docker
REM (argosepi-db) para o banco portatil do iniciar.bat. As fotos ja estao em dados\.
REM O banco do Docker so e lido, nada muda nele.
set "PGBIN=%CD%\bin\pgsql\bin"
set "PGDATA=%CD%\dados\pgdata"
set "PG_PORTA=5433"

echo.
echo  [1/2] Exportando o banco do Docker para dados\banco.sql...
docker exec argosepi-db pg_isready -U argos -d argosepi >nul 2>&1
if errorlevel 1 (
    echo  [ERRO] O container argosepi-db nao esta ligado. Ligue o Docker Desktop
    echo         ^(ou rode instalar_docker.bat^) e tente de novo.
    goto :fim_erro
)
if not exist "dados" mkdir dados
docker exec argosepi-db pg_dump -U argos --no-owner --clean --if-exists argosepi > "dados\banco.sql"
if errorlevel 1 (
    echo  [ERRO] Falha ao exportar.
    goto :fim_erro
)
echo  [OK] dados\banco.sql criado.
echo.

echo  [2/2] Importando no banco portatil...
if not exist "%PGDATA%\PG_VERSION" (
    echo  [INFO] O banco portatil ainda nao existe. Rode configurar.bat:
    echo         ele importa o dados\banco.sql sozinho ao criar o banco.
    goto :fim_ok
)
set "PG_LIGUEI="
"%PGBIN%\pg_ctl.exe" -D "%PGDATA%" status >nul 2>&1
if errorlevel 1 (
    "%PGBIN%\pg_ctl.exe" -D "%PGDATA%" -l "%CD%\dados\postgres.log" -w -t 60 start >nul
    if errorlevel 1 (
        echo  [ERRO] O banco portatil nao ligou. Veja dados\postgres.log
        goto :fim_erro
    )
    set "PG_LIGUEI=1"
)
set "PGPASSWORD=argos"
"%PGBIN%\psql.exe" -q -v ON_ERROR_STOP=1 -1 -h 127.0.0.1 -p %PG_PORTA% -U argos -d argosepi -f "dados\banco.sql" >nul 2>"dados\banco_import.log"
set "ERRO_IMPORT=%errorlevel%"
if defined PG_LIGUEI "%PGBIN%\pg_ctl.exe" -D "%PGDATA%" -m fast -w stop >nul 2>&1
if not "%ERRO_IMPORT%"=="0" (
    echo  [ERRO] Falha ao importar. Veja dados\banco_import.log
    goto :fim_erro
)
echo  [OK] Banco copiado. O iniciar.bat ja usa estes dados.

:fim_ok
echo.
pause
exit /b 0

:fim_erro
echo.
pause
exit /b 1
