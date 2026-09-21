@echo off
title Argos EPI v17 - Atualizador
color 0B
echo ======================================================
echo   ATUALIZADOR - Argos EPI v17
echo ======================================================
echo.
if not exist venv (
    echo [ERRO] Execute configurar.bat primeiro.
    pause
    exit /b 1
)
call venv\Scripts\activate.bat

echo [*] Sincronizando dependencias com uv...
if exist requirements.txt bin\uv.exe pip install --python venv\Scripts\python.exe -r requirements.txt
echo.
echo [CONCLUIDO] Execute iniciar.bat para ligar.
pause
