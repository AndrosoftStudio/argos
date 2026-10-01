@echo off
chcp 65001 >nul
title Argos EPI v20 - Instalar no Docker
color 0A
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo  =====================================================
echo   ARGOS EPI v20 - Instalacao automatica no Docker
echo  =====================================================
echo   Pasta: %CD%
echo.

REM Para forcar o tipo: instalar_docker.bat gpu   ou   instalar_docker.bat cpu
set "PERFIL=%~1"

REM ---------------------------------------------------------------
REM  [1/6] Docker instalado e ligado
REM ---------------------------------------------------------------
echo  [1/6] Verificando o Docker...
where docker >nul 2>&1
if errorlevel 1 goto :sem_docker

docker info >nul 2>&1
if not errorlevel 1 goto :docker_ok

echo  [*] O Docker Desktop esta desligado. Abrindo...
if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
set /a ESPERA=0
:espera_docker
ping -n 6 127.0.0.1 >nul
docker info >nul 2>&1
if not errorlevel 1 goto :docker_ok
set /a ESPERA+=5
if %ESPERA% GEQ 240 goto :docker_desligado
echo      aguardando o Docker ligar... %ESPERA%s
goto :espera_docker

:docker_ok
docker compose version >nul 2>&1
if errorlevel 1 goto :sem_compose
echo  [OK] Docker ligado.
echo.

REM ---------------------------------------------------------------
REM  [2/6] Arquivos do projeto e modelos
REM ---------------------------------------------------------------
echo  [2/6] Conferindo os arquivos...
if not exist "docker-compose.yml" goto :pasta_errada
if not exist "backend\app.py" goto :pasta_errada
if not exist "dados" mkdir dados
if not exist "models" mkdir models

if not exist "models\argos_epi_v1.pt" (
    echo  [AVISO] models\argos_epi_v1.pt nao encontrado.
    echo          Sem ele o sistema nao detecta os EPIs. Copie a pasta models do pacote.
)
if not exist "models\insightface\models\buffalo_l\w600k_r50.onnx" (
    echo  [AVISO] Modelos de rosto nao encontrados em models\insightface.
    echo          O sistema liga, mas sem reconhecimento facial.
)
echo  [OK] Arquivos conferidos.
echo.

REM ---------------------------------------------------------------
REM  [3/6] Configuracao (.env)
REM ---------------------------------------------------------------
echo  [3/6] Configuracao (.env)...
if exist ".env" goto :env_ok
if not exist ".env.example" goto :pasta_errada
copy /y ".env.example" ".env" >nul
REM Cada maquina se registra no hub com um nome proprio
powershell -NoProfile -ExecutionPolicy Bypass -Command "$n = 'argosepi-' + $env:COMPUTERNAME.ToLower(); (Get-Content .env) -replace '^BACKEND_NODE_ID=.*', ('BACKEND_NODE_ID=' + $n) | Set-Content -Encoding ascii .env"
echo  [OK] .env criado a partir do .env.example.
goto :env_fim
:env_ok
echo  [OK] .env ja existe, mantido como esta.
:env_fim
echo.

REM ---------------------------------------------------------------
REM  [4/6] GPU NVIDIA ou somente CPU
REM ---------------------------------------------------------------
echo  [4/6] Detectando a placa de video...
if /i "%PERFIL%"=="gpu" goto :perfil_ok
if /i "%PERFIL%"=="cpu" goto :perfil_ok
set "PERFIL=cpu"
nvidia-smi >nul 2>&1
if not errorlevel 1 set "PERFIL=gpu"
:perfil_ok
if /i "%PERFIL%"=="gpu" (set "CONT=argosepi-backend") else (set "CONT=argosepi-backend-cpu")
if /i "%PERFIL%"=="gpu" (echo  [OK] GPU NVIDIA encontrada: modo GPU.) else (echo  [OK] Sem GPU NVIDIA: modo CPU, mais lento mas funciona.)
echo.

REM ---------------------------------------------------------------
REM  [5/6] Containers antigos e portas
REM ---------------------------------------------------------------
echo  [5/6] Removendo containers antigos do Argos (o banco fica guardado no volume)...
for %%C in (argosepi-backend argosepi-backend-cpu argosepi-db argosepi-adminer) do docker rm -f %%C >nul 2>&1

netstat -ano | findstr /r /c:"TCP *[^ ]*:8088 " >nul
if not errorlevel 1 echo  [AVISO] A porta 8088 esta em uso por outro programa. Feche-o se a instalacao falhar.
netstat -ano | findstr /r /c:"TCP *[^ ]*:5432 " >nul
if not errorlevel 1 echo  [AVISO] A porta 5432 esta em uso, talvez por um PostgreSQL instalado. Pare-o se a instalacao falhar.
echo  [OK] Pronto para subir.
echo.

REM ---------------------------------------------------------------
REM  [6/6] Construir e ligar
REM ---------------------------------------------------------------
echo  [6/6] Construindo e ligando (modo %PERFIL%)...
echo      Na primeira vez baixa o PyTorch: leva de 10 a 30 minutos.
echo.
docker compose -p argosepi --profile %PERFIL% up -d --build
if not errorlevel 1 goto :subiu
if /i "%PERFIL%"=="cpu" goto :falhou

echo.
echo  [AVISO] Nao subiu com GPU. Tentando no modo CPU...
docker rm -f argosepi-backend >nul 2>&1
set "PERFIL=cpu"
set "CONT=argosepi-backend-cpu"
docker compose -p argosepi --profile cpu up -d --build
if errorlevel 1 goto :falhou

:subiu
echo.
echo  [*] Esperando o servidor responder (ate 5 minutos)...
set /a ESPERA=0
:espera_backend
curl -s -f -o nul http://localhost:8088/status >nul 2>&1
if not errorlevel 1 goto :pronto
set /a ESPERA+=5
if %ESPERA% GEQ 300 goto :demorou
ping -n 6 127.0.0.1 >nul
goto :espera_backend

:pronto
echo.
echo  =====================================================
echo   TUDO PRONTO! Argos EPI rodando no Docker (modo %PERFIL%)
echo  =====================================================
echo.
echo   Painel local : http://localhost:8088
echo   Status       : http://localhost:8088/status
echo   Site         : https://argosepi.vercel.app
echo.
echo   Containers   : %CONT% e argosepi-db
echo   Ver logs     : docker logs -f %CONT%
echo   Desligar     : parar_docker.bat
echo.
echo   O container liga sozinho junto com o Docker Desktop.
echo.
pause
exit /b 0

REM ---------------------------------------------------------------
REM  Erros
REM ---------------------------------------------------------------
:sem_docker
echo.
echo  [ERRO] Docker nao encontrado.
echo         Instale o Docker Desktop e rode este arquivo de novo:
echo         https://www.docker.com/products/docker-desktop/
goto :fim_erro

:docker_desligado
echo.
echo  [ERRO] O Docker nao ligou em 4 minutos.
echo         Abra o Docker Desktop, espere ficar verde e rode este arquivo de novo.
goto :fim_erro

:sem_compose
echo.
echo  [ERRO] "docker compose" nao encontrado. Atualize o Docker Desktop.
goto :fim_erro

:pasta_errada
echo.
echo  [ERRO] Arquivos do projeto nao encontrados nesta pasta.
echo         Este .bat precisa ficar na pasta do Argos, junto do docker-compose.yml.
goto :fim_erro

:demorou
echo.
echo  [AVISO] O servidor ainda nao respondeu. Ultimas linhas do log:
echo.
docker logs --tail 40 %CONT%
echo.
echo  Pode ser so demora para carregar os modelos. Tente abrir http://localhost:8088
echo  daqui a pouco. Para acompanhar: docker logs -f %CONT%
goto :fim_erro

:falhou
echo.
echo  [ERRO] O Docker nao conseguiu construir ou ligar o Argos.
echo         Leia as mensagens acima. Causas comuns: sem internet, disco cheio,
echo         porta 8088 ou 5432 ocupada.
goto :fim_erro

:fim_erro
echo.
pause
exit /b 1
