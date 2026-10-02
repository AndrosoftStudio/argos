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
  say "${GREEN}${BOLD} ARGOS EPI v20 - AndrosoftStudio${RESET}"
  say "${GREEN}${BOLD}=====================================================${RESET}"
  say ""
  say "${RED}[ERRO] Ambiente virtual nao encontrado.${RESET}"
  say "[INFO] Execute primeiro: bash configurar.sh"
  exit 1
fi

"$VENV_PY" scripts/banner.py --subtitle "Inicializacao do servidor local - dashboard, IA e Cloudflare" || true
say "Projeto : $ROOT_DIR"
say "Painel  : http://localhost:8088 (ou a proxima porta livre, mostrada abaixo)"
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

# Banco: PostgreSQL portatil do configurar.sh (dados/pgdata, porta 5433).
# Sem ele, usa DATABASE_URL do ambiente/.env ou o banco do Docker em 127.0.0.1:5432.
# shellcheck source=scripts/pg_portatil.sh
source "$ROOT_DIR/scripts/pg_portatil.sh"
PG_LIGUEI=""
PGBIN=""
desligar_banco() {
  if [[ -n "$PG_LIGUEI" ]]; then
    say "[*] Desligando o banco..."
    pg_desligar "$PGBIN"
    PG_LIGUEI=""
  fi
}
trap desligar_banco EXIT INT TERM

if [[ -z "${DATABASE_URL:-}" && -f "$PGDATA/PG_VERSION" ]] && PGBIN="$(pg_bin)"; then
  if ! pg_ligado "$PGBIN"; then
    say "[*] Ligando o banco (PostgreSQL portatil, porta $PG_PORTA)..."
    if ! pg_ligar "$PGBIN"; then
      say "${RED}[ERRO] O banco nao ligou. Veja dados/postgres.log${RESET}"
      say "       Se a porta $PG_PORTA estiver em uso, feche o programa que a usa."
      exit 1
    fi
    PG_LIGUEI=1
  fi
  export DATABASE_URL="$PG_URL"
  say "[OK] Banco ligado: dados/pgdata (porta $PG_PORTA)"
elif [[ -n "${DATABASE_URL:-}" ]]; then
  say "[INFO] Banco: DATABASE_URL definido no ambiente."
else
  say "[INFO] Sem banco portatil (rode bash configurar.sh): usando DATABASE_URL do .env"
  say "       ou o PostgreSQL em 127.0.0.1:5432."
fi
say ""

say "[*] Iniciando servidor..."
say "[*] Pressione Ctrl+C para encerrar."
say ""

"$VENV_PY" run.py || true
say ""
say "[!] Servidor encerrado."
desligar_banco
