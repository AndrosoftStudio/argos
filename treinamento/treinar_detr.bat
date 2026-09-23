@echo off
chcp 65001 >nul
title Argos EPI - Treinamento do DETR (deixe esta janela aberta)
setlocal
cd /d "%~dp0\.."

REM Mesmo ambiente do treinar_tudo.bat: prefere o de treino com CUDA (D:\ArgosEPI\venv)
set "PY="
if not "%ARGOS_TREINO_DIR%"=="" if exist "%ARGOS_TREINO_DIR%\venv\Scripts\python.exe" set "PY=%ARGOS_TREINO_DIR%\venv\Scripts\python.exe"
if "%PY%"=="" if exist "D:\ArgosEPI\venv\Scripts\python.exe" set "PY=D:\ArgosEPI\venv\Scripts\python.exe"
if "%PY%"=="" if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
if "%PY%"=="" (
    echo  [ERRO] Python do ambiente nao encontrado. Rode configurar.bat primeiro.
    pause
    exit /b 1
)

echo.
echo  ARGOS EPI - treino do DETR (Detection Transformer)
echo  =================================================
echo  Usa o mesmo dataset do argos_epi_v1 e leva de 1 a 2 dias numa GPU de 8 GB.
echo  Pode desligar no meio: rodando de novo, ele continua de onde parou.
echo  No fim ele copia o modelo para models\ e mostra a nota do YOLO e a do DETR.
echo.
"%PY%" treinamento\treinar_detr.py %*
echo.
pause
endlocal
