@echo off
chcp 65001 >nul
title Argos EPI v20 - Configurar (sem Docker)
color 0A
setlocal EnableExtensions
cd /d "%~dp0"

REM =====================================================================
REM  Prepara o Argos para rodar SEM Docker e SEM administrador:
REM  Python 3.12 portatil (pasta python\), PyTorch, dependencias,
REM  PostgreSQL portatil (bin\pgsql, banco em dados\pgdata, porta 5433)
REM  e o cloudflared. Tudo fica dentro desta pasta; depois de configurada,
REM  ela pode ser copiada para um pendrive e levada para outro PC Windows.
REM
REM  Uso: configurar.bat          detecta GPU NVIDIA sozinho
REM       configurar.bat cpu      forca o modo CPU
REM       configurar.bat gpu      forca o modo GPU (NVIDIA)
REM =====================================================================
set "MODO=%~1"
set "PG_VERSAO=16.14-1"
set "PG_PORTA=5433"
set "UV=%CD%\bin\uv.exe"
set "PY=%CD%\python\python.exe"
set "PGBIN=%CD%\bin\pgsql\bin"
set "PGDATA=%CD%\dados\pgdata"
set "TAR=%SystemRoot%\System32\tar.exe"
set "UV_LINK_MODE=copy"
set "UV_HTTP_TIMEOUT=600"
set "UV_CONCURRENT_DOWNLOADS=16"

echo.
echo  =====================================================
echo   ARGOS EPI v20 - Configuracao sem Docker
echo  =====================================================
echo   Pasta: %CD%
echo   Nao precisa de administrador. Precisa de internet
echo   (cerca de 1,5 GB na primeira vez).
echo.

if not exist "backend\app.py" goto :pasta_errada
if not exist "requirements.txt" goto :pasta_errada
for %%D in (bin models dados dados\users) do if not exist "%%D" mkdir "%%D"

REM ---------------------------------------------------------------------
REM  [1/7] uv (instalador de Python e pacotes)
REM ---------------------------------------------------------------------
echo  [1/7] Ferramentas de download...
where curl.exe >nul 2>&1
if errorlevel 1 goto :sem_ferramentas
if not exist "%TAR%" goto :sem_ferramentas
if exist "%UV%" goto :uv_ok
echo      baixando uv...
curl.exe -L --fail --retry 3 -# -o "bin\uv.zip" https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip
if errorlevel 1 goto :sem_internet
"%TAR%" -xf "bin\uv.zip" -C bin uv.exe
del /q "bin\uv.zip" >nul 2>&1
if not exist "%UV%" goto :sem_internet
:uv_ok
echo  [OK] uv pronto.
echo.

REM ---------------------------------------------------------------------
REM  [2/7] Python 3.12 portatil em python\
REM ---------------------------------------------------------------------
echo  [2/7] Python 3.12 portatil...
if exist "%PY%" goto :py_ok
if exist "bin\uvpython" rmdir /s /q "bin\uvpython"
"%UV%" python install 3.12 --install-dir "%CD%\bin\uvpython" --no-bin
if errorlevel 1 goto :sem_internet
REM o uv cria um atalho (junction) cpython-3.12-...; o /ad-l pega so a pasta de verdade
set "PYDIR="
for /f "delims=" %%D in ('dir /b /ad-l "bin\uvpython\cpython-3.12*" 2^>nul') do set "PYDIR=%%D"
if not defined PYDIR goto :sem_python
move "bin\uvpython\%PYDIR%" "python" >nul
rmdir /s /q "bin\uvpython" >nul 2>&1
REM sem a marca do uv, os pacotes entram direto nesse Python (e ele fica portatil)
del /q "python\Lib\EXTERNALLY-MANAGED" >nul 2>&1
if not exist "%PY%" goto :sem_python
:py_ok
"%PY%" --version
echo  [OK] Python em python\
echo.

REM ---------------------------------------------------------------------
REM  [3/7] PyTorch: GPU NVIDIA ou CPU
REM ---------------------------------------------------------------------
echo  [3/7] PyTorch...
if /i "%MODO%"=="gpu" goto :modo_ok
if /i "%MODO%"=="cpu" goto :modo_ok
set "MODO=cpu"
nvidia-smi >nul 2>&1
if not errorlevel 1 set "MODO=gpu"
:modo_ok
REM mesmo PyTorch do Docker (cu124 na GPU)
if /i "%MODO%"=="gpu" (set "TORCH_IDX=https://download.pytorch.org/whl/cu124") else (set "TORCH_IDX=https://download.pytorch.org/whl/cpu")
if /i "%MODO%"=="gpu" (echo      GPU NVIDIA: PyTorch com CUDA, cerca de 2,5 GB) else (echo      Sem GPU NVIDIA: PyTorch para CPU, cerca de 250 MB)
"%UV%" pip install --python "%PY%" torch torchvision --index-url %TORCH_IDX% --reinstall-package torch --reinstall-package torchvision
if not errorlevel 1 goto :torch_ok
if /i "%MODO%"=="cpu" goto :sem_internet
echo  [AVISO] Falhou com GPU. Tentando PyTorch para CPU...
set "MODO=cpu"
"%UV%" pip install --python "%PY%" torch torchvision --index-url https://download.pytorch.org/whl/cpu --reinstall-package torch --reinstall-package torchvision
if errorlevel 1 goto :sem_internet
:torch_ok
echo  [OK] PyTorch instalado (modo %MODO%).
echo.

REM ---------------------------------------------------------------------
REM  [4/7] Dependencias do projeto
REM ---------------------------------------------------------------------
echo  [4/7] Dependencias (requirements.txt)...
"%UV%" pip install --python "%PY%" -r requirements.txt
if errorlevel 1 goto :sem_internet
echo  [OK] Dependencias instaladas.
echo.

REM ---------------------------------------------------------------------
REM  [5/7] PostgreSQL portatil (porta 5433, so nesta maquina)
REM ---------------------------------------------------------------------
echo  [5/7] Banco de dados (PostgreSQL portatil)...
if exist "%PGBIN%\pg_ctl.exe" goto :pg_bin_ok
echo      baixando PostgreSQL %PG_VERSAO% (cerca de 320 MB)...
curl.exe -L --fail --retry 3 -# -o "bin\pgsql.zip" https://get.enterprisedb.com/postgresql/postgresql-%PG_VERSAO%-windows-x64-binaries.zip
if errorlevel 1 goto :sem_internet
echo      extraindo...
"%TAR%" -xf "bin\pgsql.zip" -C bin pgsql/bin pgsql/lib pgsql/share
del /q "bin\pgsql.zip" >nul 2>&1
if not exist "%PGBIN%\pg_ctl.exe" goto :sem_postgres
:pg_bin_ok

if exist "%PGDATA%\PG_VERSION" goto :pg_dados_ok
echo      criando o banco em dados\pgdata...
> "%TEMP%\argos_pw.txt" echo argos
"%PGBIN%\initdb.exe" -D "%PGDATA%" -U argos --pwfile="%TEMP%\argos_pw.txt" -E UTF8 --no-locale -A scram-sha-256 >nul
set "ERRO_INITDB=%errorlevel%"
del /q "%TEMP%\argos_pw.txt" >nul 2>&1
if not "%ERRO_INITDB%"=="0" goto :sem_postgres
>> "%PGDATA%\postgresql.conf" echo port = %PG_PORTA%
>> "%PGDATA%\postgresql.conf" echo listen_addresses = '127.0.0.1'
"%PGBIN%\pg_ctl.exe" -D "%PGDATA%" -l "%CD%\dados\postgres.log" -w -t 60 start >nul
if errorlevel 1 goto :sem_postgres
set "PGPASSWORD=argos"
"%PGBIN%\createdb.exe" -h 127.0.0.1 -p %PG_PORTA% -U argos argosepi
if exist "dados\banco.sql" (
    echo      importando dados\banco.sql (copia do banco do Docker^)...
    "%PGBIN%\psql.exe" -q -v ON_ERROR_STOP=1 -1 -h 127.0.0.1 -p %PG_PORTA% -U argos -d argosepi -f "dados\banco.sql" >nul 2>"dados\banco_import.log"
)
"%PGBIN%\pg_ctl.exe" -D "%PGDATA%" -m fast -w stop >nul
echo  [OK] Banco criado (usuario argos, porta %PG_PORTA%).
goto :pg_fim
:pg_dados_ok
echo  [OK] PostgreSQL e banco ja existem (dados\pgdata).
:pg_fim
echo.

REM ---------------------------------------------------------------------
REM  [6/7] cloudflared (link publico) e modelos
REM ---------------------------------------------------------------------
echo  [6/7] cloudflared e modelos...
if exist "cloudflared.exe" goto :cf_ok
echo      baixando cloudflared...
curl.exe -L --fail --retry 3 -# -o "cloudflared.exe" https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
if errorlevel 1 (
    del /q "cloudflared.exe" >nul 2>&1
    echo  [AVISO] cloudflared nao baixou: o sistema funciona so na rede local.
)
:cf_ok
if exist "cloudflared.exe" echo  [OK] cloudflared.exe
if not exist "models\argos_epi_v1.pt" (
    echo  [AVISO] models\argos_epi_v1.pt nao encontrado.
    echo          Sem ele o sistema nao detecta os EPIs. Copie a pasta models do pacote.
)
if not exist "models\insightface\models\buffalo_l\w600k_r50.onnx" (
    echo  [AVISO] Modelos de rosto nao encontrados em models\insightface.
    echo          O sistema liga, mas sem reconhecimento facial.
)
echo.

REM ---------------------------------------------------------------------
REM  [7/7] Configuracao (.env)
REM ---------------------------------------------------------------------
echo  [7/7] Configuracao (.env)...
if exist ".env" goto :env_ok
copy /y ".env.example" ".env" >nul
REM Cada maquina se registra no hub com um nome proprio
powershell -NoProfile -ExecutionPolicy Bypass -Command "$n = 'argosepi-' + $env:COMPUTERNAME.ToLower(); (Get-Content .env) -replace '^BACKEND_NODE_ID=.*', ('BACKEND_NODE_ID=' + $n) | Set-Content -Encoding ascii .env" >nul 2>&1
echo  [OK] .env criado a partir do .env.example.
goto :env_fim
:env_ok
echo  [OK] .env ja existe, mantido como esta.
:env_fim
echo.

echo  Conferindo a instalacao...
"%PY%" -c "import torch, ultralytics, flask, cv2, psycopg, onnxruntime; print('  PyTorch', torch.__version__, '| CUDA:', torch.cuda.is_available()); print('  Ultralytics', ultralytics.__version__, '| OpenCV', cv2.__version__)"
if errorlevel 1 goto :import_falhou

echo.
echo  =====================================================
echo   TUDO PRONTO! Para ligar o Argos: iniciar.bat
echo  =====================================================
echo   Painel local : http://localhost:8088
echo   Site         : https://argosepi.vercel.app
echo                  (precisa da HUB_API_KEY no .env)
echo.
pause
exit /b 0

REM ---------------------------------------------------------------------
REM  Erros
REM ---------------------------------------------------------------------
:pasta_errada
echo.
echo  [ERRO] Arquivos do projeto nao encontrados nesta pasta.
echo         Este .bat precisa ficar na pasta do Argos, junto do run.py.
goto :fim_erro

:sem_ferramentas
echo.
echo  [ERRO] curl.exe ou tar.exe nao encontrados (vem no Windows 10 e 11).
goto :fim_erro

:sem_internet
echo.
echo  [ERRO] Falha ao baixar ou instalar. Confira a internet e rode de novo:
echo         o que ja foi baixado e aproveitado.
goto :fim_erro

:sem_python
echo.
echo  [ERRO] O Python portatil nao foi instalado. Rode de novo.
goto :fim_erro

:sem_postgres
echo.
echo  [ERRO] O PostgreSQL portatil nao funcionou. Veja dados\postgres.log.
echo         Se a porta %PG_PORTA% estiver em uso, feche o programa que a usa.
goto :fim_erro

:import_falhou
echo.
echo  [ERRO] O Python nao conseguiu carregar as bibliotecas (mensagem acima).
echo         Se falar em DLL ou "Visual C++", instale o Visual C++ Redistributable:
echo         https://aka.ms/vs/17/release/vc_redist.x64.exe
goto :fim_erro

:fim_erro
echo.
pause
exit /b 1
