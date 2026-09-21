@echo off
chcp 65001 >nul
title Argos EPI - Treinamento do detector de EPIs
setlocal
cd /d "%~dp0\.."

REM Prefere o ambiente de treino com CUDA (D:\ArgosEPI\venv); senao, o do projeto
set "PY="
if not "%ARGOS_TREINO_DIR%"=="" if exist "%ARGOS_TREINO_DIR%\venv\Scripts\python.exe" set "PY=%ARGOS_TREINO_DIR%\venv\Scripts\python.exe"
if "%PY%"=="" if exist "D:\ArgosEPI\venv\Scripts\python.exe" set "PY=D:\ArgosEPI\venv\Scripts\python.exe"
if "%PY%"=="" if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
if "%PY%"=="" (
    echo  [ERRO] Python do ambiente nao encontrado. Rode configurar.bat primeiro.
    pause
    exit /b 1
)

echo  Python : %PY%
echo  Etapas : baixar, montar, professor, pseudo, final, avaliar
echo  Dica   : treinar_tudo.bat --inicio professor   (pula download e montagem)
echo  Retomar: depois de queda de energia, rode treinar_tudo.bat sem argumentos
echo.
"%PY%" treinamento\pipeline.py %*
echo.
pause
endlocal
