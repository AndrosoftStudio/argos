#!/bin/sh
# Remove o Argos EPI Servidor desta conta. Pergunta antes de apagar os dados (banco, rostos, gravacoes).
RAIZ="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DADOS="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"

for pid in $(pgrep -f "$RAIZ/janela/argos-epi-servidor" 2>/dev/null); do kill -TERM "$pid" 2>/dev/null || true; done
i=0
while pgrep -f "$RAIZ/(janela|motor|python)/" >/dev/null 2>&1 && [ $i -lt 45 ]; do sleep 1; i=$((i+1)); done
if [ -x "$RAIZ/bin/pgsql/bin/pg_ctl" ] && [ -d "$RAIZ/dados/pgdata" ]; then
  "$RAIZ/bin/pgsql/bin/pg_ctl" -D "$RAIZ/dados/pgdata" stop -m fast >/dev/null 2>&1 || true
fi

rm -f "$DADOS/applications/argos-epi-servidor.desktop" "$DADOS/icons/hicolor/512x512/apps/argos-epi-servidor.png" \
      "$CONFIG/autostart/argos-epi-servidor.desktop"
MESA="$(command -v xdg-user-dir >/dev/null 2>&1 && xdg-user-dir DESKTOP || echo "$HOME/Desktop")"
rm -f "$MESA/argos-epi-servidor.desktop"

printf "Apagar tambem os dados (banco, fotos de rostos, gravacoes) em %s/dados? [s/N] " "$RAIZ"
read -r resp || resp=""
case "$resp" in
  s|S|sim|SIM)
    rm -rf "${RAIZ:?}"
    echo "Argos EPI Servidor removido, com os dados." ;;
  *)
    for item in "$RAIZ"/* "$RAIZ"/.[!.]*; do
      [ -e "$item" ] || continue
      case "$(basename "$item")" in dados|.env) continue ;; esac
      rm -rf "$item"
    done
    echo "Programa removido. Os dados ficaram em $RAIZ/dados (apague a pasta quando quiser)." ;;
esac
