@echo off
chcp 65001 >nul
title Argos EPI v20 - Desligar
cd /d "%~dp0"

echo.
echo  Desligando os containers do Argos EPI...
docker stop argosepi-backend argosepi-backend-cpu argosepi-db 2>nul
echo.
echo  [OK] Desligado. Os dados continuam guardados.
echo       Para ligar de novo: instalar_docker.bat (ou o botao Start no Docker Desktop).
echo.
pause
