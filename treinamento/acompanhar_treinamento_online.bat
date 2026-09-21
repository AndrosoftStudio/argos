@echo off
chcp 65001 >nul
title Argos EPI - Acompanhar treinamento (online)
setlocal
cd /d "%~dp0\.."

REM Mesmo Python do treino (tem psutil); sem ele, o painel funciona com menos detalhes
set "PY="
if not "%ARGOS_TREINO_DIR%"=="" if exist "%ARGOS_TREINO_DIR%\venv\Scripts\python.exe" set "PY=%ARGOS_TREINO_DIR%\venv\Scripts\python.exe"
if "%PY%"=="" if exist "D:\ArgosEPI\venv\Scripts\python.exe" set "PY=D:\ArgosEPI\venv\Scripts\python.exe"
if "%PY%"=="" if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
if "%PY%"=="" set "PY=python"

echo  Painel local : http://127.0.0.1:8765
echo  Online       : abre um tunel do Cloudflare e avisa o hub; o site acha este PC sozinho.
echo  Fechar esta janela fecha so o painel; o treino continua.
echo.
start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8765"
"%PY%" treinamento\acompanhar_treinamento\servidor.py --publico %*
pause
endlocal
