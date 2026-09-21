@echo off
chcp 65001 >nul
title Argos EPI v17 - Configuracao
color 0A
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo  [bootstrap] Iniciando configurador do Argos EPI v17...
echo  [bootstrap] Assim que o Python for encontrado, o banner completo sera exibido.
echo.

mkdir bin        2>nul
mkdir models     2>nul
mkdir dados\users 2>nul

REM =====================================================
REM  [1/6] Detectar Python
REM =====================================================
echo  [1/6] Procurando Python 3.12 ou 3.11...

set "PYEXE="

py -3.12 --version >nul 2>&1
if not errorlevel 1 ( set "PYEXE=py -3.12" & goto :found_py )

py -3.11 --version >nul 2>&1
if not errorlevel 1 ( set "PYEXE=py -3.11" & goto :found_py )

for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Program Files\Python311\python.exe"
) do (
    if exist %%P ( set "PYEXE=%%P" & goto :found_py )
)

where python >nul 2>&1 && ( set "PYEXE=python" & goto :found_py )
where py     >nul 2>&1 && ( set "PYEXE=py"     & goto :found_py )

echo  [ERRO] Python 3.11 ou 3.12 nao encontrado.
echo  [INFO] Instale em: https://python.org
pause & exit /b 1

:found_py
cls
%PYEXE% scripts\banner.py --subtitle "Configuracao do ambiente - uv, PyTorch, YOLO, Cloudflare e modelos"
for /f "tokens=*" %%V in ('%PYEXE% --version 2^>^&1') do echo  [OK] %%V via: %PYEXE%
echo  Pasta do projeto: %CD%
echo  Modo: instalacao assistida para Windows
echo.

REM =====================================================
REM  [2/6] Baixar uv.exe + aria2c.exe (bootstrap)
REM =====================================================
echo  [2/6] Verificando uv.exe e aria2c.exe...

set "NEED_BOOT="
if not exist "bin\uv.exe"     set "NEED_BOOT=%NEED_BOOT% uv"
if not exist "bin\aria2c.exe" set "NEED_BOOT=%NEED_BOOT% aria2c"

if defined NEED_BOOT (
    echo  Baixando:%NEED_BOOT%...
    %PYEXE% downloader.py bin %NEED_BOOT%
) else (
    echo  [OK] uv.exe e aria2c.exe ja existem.
)

if not exist "bin\uv.exe" (
    echo  [ERRO] Falha ao baixar uv.exe. Verifique a conexao.
    pause & exit /b 1
)
echo.

REM =====================================================
REM  [3/6] Criar ambiente virtual com uv
REM =====================================================
echo  [3/6] Criando ambiente virtual com uv...

if exist venv ( rmdir /s /q venv >nul 2>&1 )

bin\uv.exe venv venv --python 3.12 2>nul
if not exist "venv\Scripts\python.exe" (
    echo  Tentando Python 3.11...
    bin\uv.exe venv venv --python 3.11 2>nul
)
if not exist "venv\Scripts\python.exe" (
    echo  Tentando sem versao especifica...
    bin\uv.exe venv venv
)
if not exist "venv\Scripts\python.exe" (
    echo  [ERRO] Falha ao criar venv.
    pause & exit /b 1
)
echo  [OK] venv criado.
echo.

REM =====================================================
REM  [4/6] Detectar GPU e instalar PyTorch via uv
REM        uv baixa pacotes em PARALELO - muito mais
REM        rapido que pip para PyTorch (1-2 GB)
REM =====================================================
echo  [4/6] Selecione o hardware:
echo.
echo    [1] NVIDIA  CUDA 12.1  - Melhor performance
echo    [2] NVIDIA  CUDA 12.4  - Drivers mais recentes
echo    [3] AMD / Intel DirectML - Boa performance
echo    [4] CPU apenas          - Funciona em qualquer PC
echo.
set /p GPU_CHOICE="   Digite 1, 2, 3 ou 4: "
echo.

if "%GPU_CHOICE%"=="1" (
    set "TORCH_PKGS=torch torchvision torchaudio"
    set "TORCH_IDX=--index-url https://download.pytorch.org/whl/cu121"
    set "TORCH_LABEL=PyTorch CUDA 12.1"
    set "GPU_TYPE=NVIDIA CUDA 12.1"
)
if "%GPU_CHOICE%"=="2" (
    set "TORCH_PKGS=torch torchvision torchaudio"
    set "TORCH_IDX=--index-url https://download.pytorch.org/whl/cu124"
    set "TORCH_LABEL=PyTorch CUDA 12.4"
    set "GPU_TYPE=NVIDIA CUDA 12.4"
)
if "%GPU_CHOICE%"=="3" (
    set "TORCH_PKGS=torch torchvision torchaudio torch-directml"
    set "TORCH_IDX="
    set "TORCH_LABEL=PyTorch + DirectML"
    set "GPU_TYPE=AMD/Intel DirectML"
)
if "%GPU_CHOICE%"=="4" (
    set "TORCH_PKGS=torch torchvision torchaudio"
    set "TORCH_IDX="
    set "TORCH_LABEL=PyTorch CPU"
    set "GPU_TYPE=CPU"
)
if not defined TORCH_PKGS (
    echo  [AVISO] Opcao invalida, usando CPU.
    set "TORCH_PKGS=torch torchvision torchaudio"
    set "TORCH_IDX="
    set "TORCH_LABEL=PyTorch CPU"
    set "GPU_TYPE=CPU padrao"
)

echo  [*] Instalando %TORCH_LABEL% via uv (paralelo)...
echo.

set UV_CONCURRENT_DOWNLOADS=16
set UV_HTTP_TIMEOUT=600

if defined TORCH_IDX (
    bin\uv.exe pip install --python venv\Scripts\python.exe %TORCH_PKGS% %TORCH_IDX%
) else (
    bin\uv.exe pip install --python venv\Scripts\python.exe %TORCH_PKGS%
)

if errorlevel 1 (
    echo.
    echo  [AVISO] Falhou. Tentando PyTorch CPU como fallback...
    bin\uv.exe pip install --python venv\Scripts\python.exe torch torchvision torchaudio
    if errorlevel 1 (
        echo  [ERRO] PyTorch nao instalado. Verifique a conexao.
        pause & exit /b 1
    )
    set "GPU_TYPE=CPU fallback"
)
echo  [OK] %TORCH_LABEL% instalado.
echo.

REM =====================================================
REM  [5/6] Dependencias do projeto via uv (paralelo)
REM =====================================================
echo  [5/6] Instalando dependencias do projeto...

set UV_CONCURRENT_DOWNLOADS=16

bin\uv.exe pip install --python venv\Scripts\python.exe ^
    "flask>=3.0.0"           ^
    "flask-cors>=4.0.0"      ^
    "flask-sock>=0.7.0"      ^
    "opencv-python>=4.8.0"   ^
    "ultralytics>=8.4.0"     ^
    "numpy>=1.24.0,<2.0"     ^
    "requests>=2.31.0"       ^
    "psutil>=5.9.0"          ^
    "gevent"                 ^
    "Pillow"                  ^
    "pyfiglet>=1.0.2"

if errorlevel 1 (
    echo  [AVISO] Tentando via requirements.txt...
    bin\uv.exe pip install --python venv\Scripts\python.exe -r requirements.txt
    if errorlevel 1 (
        echo  [ERRO] Falha nas dependencias.
        pause & exit /b 1
    )
)
echo  [OK] Dependencias instaladas.
echo.

REM =====================================================
REM  [6/6] Binarios externos + modelos YOLO
REM =====================================================
echo  [6/6] Verificando cloudflared e modelos YOLO...

REM -- cloudflared --
if not exist "cloudflared.exe" (
    if not exist "bin\cloudflared.exe" (
        echo  Baixando cloudflared.exe...
        %PYEXE% downloader.py bin cloudflared
    )
    if exist "bin\cloudflared.exe" (
        copy /y "bin\cloudflared.exe" "cloudflared.exe" >nul
        echo  [OK] cloudflared.exe
    )
) else (
    echo  [OK] cloudflared.exe ja existe.
)

REM -- modelos YOLO --
set "NEED_MODELS="
if not exist "models\yolo26n.pt" set "NEED_MODELS=sim"
if not exist "models\yolo26s.pt" set "NEED_MODELS=sim"

if defined NEED_MODELS (
    echo  Baixando modelos YOLO...
    %PYEXE% downloader.py bin modelos
) else (
    echo  [OK] Modelos YOLO ja existem.
)

REM =====================================================
REM  Resultado final
REM =====================================================
echo.
%PYEXE% scripts\banner.py --subtitle "Configuracao concluida - verificacao final" --compact

if exist "venv\Scripts\python.exe" (
    echo    [OK]  venv criado
) else (
    echo    [!!]  venv NAO criado - PROBLEMA GRAVE
)

if exist "bin\uv.exe"      echo    [OK]  bin\uv.exe
if exist "bin\aria2c.exe"  echo    [OK]  bin\aria2c.exe
if exist "cloudflared.exe" echo    [OK]  cloudflared.exe

if exist "models\yolo26n.pt" (
    echo    [OK]  models\yolo26n.pt
) else (
    echo    [!!]  models\yolo26n.pt NAO baixado
)

if exist "models\yolo26s.pt" (
    echo    [OK]  models\yolo26s.pt
) else (
    echo    [!!]  models\yolo26s.pt NAO baixado
)

echo.
echo  GPU escolhida: %GPU_TYPE%
echo.

venv\Scripts\python.exe -c "import torch; v=torch.__version__; c=torch.cuda.is_available(); print(f'  PyTorch     : v{v}  |  CUDA: {c}')" 2>nul
if errorlevel 1 echo  [!!] PyTorch nao instalado

venv\Scripts\python.exe -c "import ultralytics; print('  Ultralytics : v' + ultralytics.__version__)" 2>nul
if errorlevel 1 echo  [!!] Ultralytics nao instalado

venv\Scripts\python.exe -c "import flask; print('  Flask       : v' + flask.__version__)" 2>nul
if errorlevel 1 echo  [!!] Flask nao instalado

venv\Scripts\python.exe -c "import cv2; print('  OpenCV      : v' + cv2.__version__)" 2>nul
if errorlevel 1 echo  [!!] OpenCV nao instalado

echo.
echo  =====================================================
echo   Tudo pronto. Para iniciar o sistema, execute: iniciar.bat
echo   Frontend local: http://localhost:8088
echo   Hub opcional  : configure BACKEND_HUB_URL no .env
echo  =====================================================
echo.
pause
endlocal
