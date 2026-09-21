#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

GREEN="\033[92m"
BOLD="\033[1m"
RED="\033[91m"
RESET="\033[0m"

say() {
  printf "%b\n" "$*"
}

resolve_venv_python() {
  if [[ -x "$ROOT_DIR/venv/bin/python" ]]; then
    printf "%s" "$ROOT_DIR/venv/bin/python"
    return 0
  fi
  if [[ -x "$ROOT_DIR/venv/Scripts/python.exe" ]]; then
    printf "%s" "$ROOT_DIR/venv/Scripts/python.exe"
    return 0
  fi
  return 1
}

VENV_PY="$(resolve_venv_python || true)"
if [[ -z "$VENV_PY" ]]; then
  say ""
  say "${GREEN}${BOLD}=====================================================${RESET}"
  say "${GREEN}${BOLD} ARGOS EPI v17 - AndrosoftStudio${RESET}"
  say "${GREEN}${BOLD}=====================================================${RESET}"
  say ""
  say "${RED}[ERRO] Ambiente virtual nao encontrado.${RESET}"
  say "[INFO] Execute primeiro: bash configurar.sh"
  exit 1
fi

"$VENV_PY" scripts/banner.py --subtitle "Inicializacao do servidor local - dashboard, IA e Cloudflare" || true
say "Projeto : $ROOT_DIR"
say "Backend : http://localhost:8088"
say "Frontend: http://localhost:8088"
say ""

if [[ ! -f models/yolo26n.pt ]]; then
  say "[AVISO] Modelo yolo26n.pt nao encontrado em models/"
  say "[INFO]  Execute bash configurar.sh para baixar os modelos."
  say ""
fi

if ! command -v cloudflared >/dev/null 2>&1 && [[ ! -x ./cloudflared && ! -f ./cloudflared.exe ]]; then
  say "[INFO] cloudflared nao encontrado."
  say "[INFO] O sistema funcionara apenas na rede local."
  say ""
fi

say "[*] Iniciando servidor..."
say "[*] Acesse: http://localhost:8088"
say "[*] Pressione Ctrl+C para encerrar."
say ""

"$VENV_PY" run.py
say ""
say "[!] Servidor encerrado."
