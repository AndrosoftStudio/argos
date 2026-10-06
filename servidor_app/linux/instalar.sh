#!/bin/sh
# Instala (ou atualiza) o Argos EPI Servidor para o usuario atual, sem root:
#   programa em ~/.local/share/argos-epi-servidor, atalho no menu de aplicativos (e na area de trabalho).
# O pacote ja vem compilado (motor/ com as bibliotecas, modelos em models/): nada e baixado depois.
# Ao atualizar, a pasta dados (banco, rostos, gravacoes), o .env e os modelos enviados pelo painel
# ficam como estao; o resto da pasta antiga (inclusive sobras de versoes anteriores) e apagado.
set -e
ORIGEM="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DESTINO="${ARGOS_DESTINO:-${XDG_DATA_HOME:-$HOME/.local/share}/argos-epi-servidor}"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONES="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/512x512/apps"

if [ "$(id -u)" = "0" ]; then
  echo "Nao rode como root (sem sudo). O programa fica so na sua conta." >&2
  exit 1
fi
case "$(uname -m)" in x86_64|amd64) ;; *) echo "Este pacote e para PCs x64 (64 bits)." >&2; exit 1 ;; esac

echo "Argos EPI Servidor $(cat "$ORIGEM/VERSAO.txt" 2>/dev/null) -> $DESTINO"

# fecha a versao que estiver aberta (ela desliga o banco direito)
if [ -d "$DESTINO" ]; then
  for pid in $(pgrep -f "$DESTINO/janela/argos-epi-servidor" 2>/dev/null); do kill -TERM "$pid" 2>/dev/null || true; done
  i=0
  while pgrep -f "$DESTINO/(janela|motor|python)/" >/dev/null 2>&1 && [ $i -lt 45 ]; do sleep 1; i=$((i+1)); done
  pkill -KILL -f "$DESTINO/(janela|motor|python)/" 2>/dev/null || true
  if [ -x "$DESTINO/bin/pgsql/bin/pg_ctl" ] && [ -d "$DESTINO/dados/pgdata" ]; then
    "$DESTINO/bin/pgsql/bin/pg_ctl" -D "$DESTINO/dados/pgdata" stop -m fast >/dev/null 2>&1 || true
  fi
fi

mkdir -p "$DESTINO"
if [ "$ORIGEM" != "$DESTINO" ]; then
  # limpa a instalacao anterior: fica so o que e do usuario (dados, .env, modelos e envios)
  for item in "$DESTINO"/* "$DESTINO"/.[!.]*; do
    [ -e "$item" ] || continue
    case "$(basename "$item")" in dados|.env|models|uploads) continue ;; esac
    rm -rf "$item"
  done
  for item in "$ORIGEM"/*; do
    [ -e "$item" ] || continue
    nome="$(basename "$item")"
    case "$nome" in dados|.env) continue ;; esac
    if [ "$nome" = "models" ]; then mkdir -p "$DESTINO/models"; cp -a "$item/." "$DESTINO/models/"; continue; fi
    cp -a "$item" "$DESTINO/"
  done
fi
chmod +x "$DESTINO/argos-epi-servidor" "$DESTINO/instalar.sh" "$DESTINO/desinstalar.sh" "$DESTINO/janela/argos-epi-servidor" \
  "$DESTINO/motor/argos-motor" "$DESTINO/bin/cloudflared" "$DESTINO/bin/pgsql/bin/"* 2>/dev/null || true

# atalho no menu de aplicativos
mkdir -p "$APPS" "$ICONES"
cp "$DESTINO/argos-epi-servidor.png" "$ICONES/argos-epi-servidor.png"
cat > "$APPS/argos-epi-servidor.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Argos EPI Servidor
Comment=Servidor de cameras com IA para EPIs
Exec="$DESTINO/argos-epi-servidor" %U
Icon=argos-epi-servidor
Terminal=false
Categories=Utility;Video;
StartupWMClass=argos-epi-servidor
EOF
chmod +x "$APPS/argos-epi-servidor.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q "${ICONES%/512x512/apps}" >/dev/null 2>&1 || true

# e na area de trabalho, se existir
MESA="$(command -v xdg-user-dir >/dev/null 2>&1 && xdg-user-dir DESKTOP || echo "$HOME/Desktop")"
if [ -d "$MESA" ] && [ "${ARGOS_SEM_ATALHO:-0}" != "1" ]; then
  cp "$APPS/argos-epi-servidor.desktop" "$MESA/"
  chmod +x "$MESA/argos-epi-servidor.desktop"
  command -v gio >/dev/null 2>&1 && gio set "$MESA/argos-epi-servidor.desktop" metadata::trusted true 2>/dev/null || true
fi

echo "Pronto. Abrindo o Argos EPI Servidor (depois, pelo menu de aplicativos)."
echo "Para desinstalar: $DESTINO/desinstalar.sh"
if [ -n "$DISPLAY$WAYLAND_DISPLAY" ] && [ "${ARGOS_NAO_ABRIR:-0}" != "1" ]; then
  nohup "$DESTINO/argos-epi-servidor" >/dev/null 2>&1 &
fi
