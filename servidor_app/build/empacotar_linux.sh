#!/bin/bash
# Gera o Argos EPI Servidor para Linux x64 (janela Electron + Python + PostgreSQL), um .tar.gz por placa:
#   $SAIDA/linux/argos-epi-servidor-<versao>-<placa>.tar.gz   (placa: nvidia | cpu)
#   $SAIDA/linux/linux.json                                   (url, tamanho e sha256: vai para o latest.json)
# Roda dentro de um container Ubuntu (do Windows, com o Docker Desktop). O trabalho fica no disco do
# container (ext4: os links simbolicos do Python funcionam), uma placa por vez e sem cache do uv, e o
# .tar.gz sai direto na pasta de saida (D:), para o disco do Docker no C: quase nao crescer:
#   docker run --rm -v "<pasta v20>:/fonte:ro" -v D:\argos-build\saida:/saida \
#     -e VERSAO=20.1.0 -e PLACAS="cpu nvidia" ubuntu:22.04 bash /fonte/servidor_app/build/empacotar_linux.sh
# (ARGOS_DISCO=/caminho/disco.img usa um ext4 em arquivo, se o container for --privileged)
# -e SO_PROGRAMA=1: so troca o programa nos .tar.gz que ja existem (o Python com o PyTorch e reaproveitado).
set -euo pipefail
VERSAO="${VERSAO:-20.1.0}"
PLACAS="${PLACAS:-cpu nvidia}"
FONTE="${FONTE:-/fonte}"
SAIDA="${SAIDA:-/saida}"
PG_VERSAO="16.15.0"
ONNX_NVIDIA="onnxruntime-gpu==1.23.2"     # CUDA 12 + cuDNN 9, os mesmos do torch cu126 (a 1.24+ pede CUDA 13)
passo() { printf '\n== %s\n' "$*"; }

passo "Ferramentas"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null
# libgl1/libglib2.0: o OpenCV pede (os Linux com interface grafica ja tem); so para conferir os imports aqui
apt-get install -y -qq --no-install-recommends curl ca-certificates unzip xz-utils pigz e2fsprogs libgl1 libglib2.0-0 >/dev/null

# disco de trabalho: ext4 dentro de um arquivo no D: (se /d existir); senao, /tmp do container
W=/tmp/argos-trabalho
if [ -n "${ARGOS_DISCO:-}" ]; then
  [ -f "$ARGOS_DISCO" ] || { truncate -s 60G "$ARGOS_DISCO"; mkfs.ext4 -q -F "$ARGOS_DISCO"; }
  mkdir -p /mnt/w
  mountpoint -q /mnt/w || mount -o loop "$ARGOS_DISCO" /mnt/w
  W=/mnt/w
fi
CACHE="$W/cache"; PALCO="$W/palco"
mkdir -p "$CACHE" "$PALCO" "$SAIDA/linux"
export UV_NO_CACHE=1 UV_LINK_MODE=copy UV_HTTP_TIMEOUT=900 UV_PYTHON_INSTALL_DIR="$W/uv-python"
baixar() { [ -s "$2" ] || { echo "   baixando $1"; curl -fL --retry 3 -s -o "$2.tmp" "$1"; mv "$2.tmp" "$2"; }; }

UV="$CACHE/uv"
if [ ! -x "$UV" ]; then
  baixar https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz "$CACHE/uv.tgz"
  tar -xzf "$CACHE/uv.tgz" -C "$CACHE" --strip-components=1 uv-x86_64-unknown-linux-gnu/uv
fi
ELECTRON_TAG="$(curl -fsI https://github.com/electron/electron/releases/latest | tr -d '\r' | sed -n 's#^location: .*/tag/##Ip')"
ELECTRON_ZIP="$CACHE/electron-$ELECTRON_TAG-linux-x64.zip"
baixar "https://github.com/electron/electron/releases/download/$ELECTRON_TAG/electron-$ELECTRON_TAG-linux-x64.zip" "$ELECTRON_ZIP"
PG_JAR="$CACHE/pg-$PG_VERSAO.jar"
baixar "https://repo1.maven.org/maven2/io/zonky/test/postgres/embedded-postgres-binaries-linux-amd64/$PG_VERSAO/embedded-postgres-binaries-linux-amd64-$PG_VERSAO.jar" "$PG_JAR"
CF="$CACHE/cloudflared"
baixar https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 "$CF"
chmod +x "$CF"

# ---------------------------------------------------------------- programa (igual para as placas)
passo "Montando o programa $VERSAO (Electron $ELECTRON_TAG, PostgreSQL $PG_VERSAO)"
APP="$PALCO/app"
rm -rf "$APP"; mkdir -p "$APP"
copiar() { # copiar <origem> <destino> [exclusoes...]
  local o="$1" d="$2"; shift 2
  local ex=(--exclude=__pycache__ --exclude='*.pyc')
  for e in "$@"; do ex+=("--exclude=$e"); done
  mkdir -p "$d"; tar -C "$o" "${ex[@]}" -cf - . | tar -C "$d" -xf -
}
copiar "$FONTE/backend" "$APP/backend" ./hub
copiar "$FONTE/frontend" "$APP/frontend" ./api
copiar "$FONTE/scripts" "$APP/scripts"
mkdir -p "$APP/servidor_app" "$APP/models" "$APP/bin"
cp "$FONTE/servidor_app/supervisor.py" "$APP/servidor_app/"
copiar "$FONTE/servidor_app/ui" "$APP/servidor_app/ui"
for m in argos_epi_v1.pt argos_epi_v1.json yolo26n.pt yolo26s.pt yolo26n-pose.pt yolo26s-pose.pt yolo26m-pose.pt; do
  cp "$FONTE/models/$m" "$APP/models/"; done
cp "$FONTE/run.py" "$FONTE/requirements.txt" "$FONTE/.env.example" "$APP/"
cp "$CF" "$APP/cloudflared"
cp "$UV" "$APP/bin/uv"                 # o botao "Instalar dependencias do TensorRT" usa
for f in argos-epi-servidor instalar.sh desinstalar.sh; do
  sed 's/\r$//' "$FONTE/servidor_app/linux/$f" > "$APP/$f"; chmod +x "$APP/$f"; done
echo "$VERSAO" > "$APP/VERSAO.txt"
cat > "$APP/LEIA-ME.txt" <<EOF
Argos EPI Servidor $VERSAO para Linux
Servidor de cameras com IA para EPIs (TCC SENAI Lauro de Freitas/BA).
Instalar:     ./instalar.sh   (sem sudo; fica em ~/.local/share/argos-epi-servidor e no menu de aplicativos)
Desinstalar:  ~/.local/share/argos-epi-servidor/desinstalar.sh
Os dados (banco, rostos, gravacoes) ficam na pasta dados e nao sao apagados ao atualizar.
EOF
# janela: Electron pronto + o app (main.js, preload.js)
mkdir -p "$APP/janela"
unzip -q "$ELECTRON_ZIP" -d "$APP/janela"
mv "$APP/janela/electron" "$APP/janela/argos-epi-servidor"
rm -f "$APP/janela/resources/default_app.asar"
mkdir -p "$APP/janela/resources/app"
for f in package.json main.js preload.js; do sed 's/\r$//' "$FONTE/servidor_app/linux/app/$f" > "$APP/janela/resources/app/$f"; done
sed -i "s/\"version\": \"[^\"]*\"/\"version\": \"$VERSAO\"/" "$APP/janela/resources/app/package.json"
# PostgreSQL portatil (zonky: bin, lib, share ja prontos)
mkdir -p "$APP/bin/pgsql"
( cd "$CACHE" && rm -rf pgjar && mkdir pgjar && cd pgjar && unzip -q "$PG_JAR" && tar -xJf ./*.txz -C "$APP/bin/pgsql" )
rm -rf "$CACHE/pgjar"
test -x "$APP/bin/pgsql/bin/initdb"

# ---------------------------------------------------------------- Python por placa + pacote
LINUX_JSON="$SAIDA/linux/linux.json"
[ -f "$LINUX_JSON" ] || echo '{}' > "$LINUX_JSON"
for PLACA in $PLACAS; do
  # SO_PROGRAMA=1: reaproveita o Python do .tar.gz que ja existe e so troca o programa (bem mais rapido)
  ANTIGO="$(ls "$SAIDA/linux/argos-epi-servidor-"*"-$PLACA.tar.gz" 2>/dev/null | head -1 || true)"
  if [ "${SO_PROGRAMA:-0}" = "1" ] && [ -n "$ANTIGO" ]; then
    passo "Python $PLACA reaproveitado de $(basename "$ANTIGO")"
    BASE="$PALCO/py-$PLACA"
    rm -rf "$BASE"; mkdir -p "$BASE"
    pigz -dc "$ANTIGO" | tar -C "$BASE" -xf - argos-epi-servidor/python
    mv "$BASE/argos-epi-servidor/python" "$BASE/python"; rm -rf "$BASE/argos-epi-servidor"
  else
  passo "Python 3.12 para $PLACA"
  case "$PLACA" in
    nvidia) INDICE=https://download.pytorch.org/whl/cu126; ONNX="$ONNX_NVIDIA" ;;
    cpu)    INDICE=https://download.pytorch.org/whl/cpu;   ONNX="" ;;
    *) echo "placa desconhecida: $PLACA"; exit 1 ;;
  esac
  BASE="$PALCO/py-$PLACA"
  rm -rf "$BASE"; mkdir -p "$BASE"
  "$UV" python install 3.12 --install-dir "$BASE/uvpython" --no-bin
  DIR="$(find "$BASE/uvpython" -maxdepth 1 -type d -name 'cpython-3.12*' ! -type l | head -1)"
  mv "$DIR" "$BASE/python"; rm -rf "$BASE/uvpython"
  rm -f "$BASE/python/lib/python3.12/EXTERNALLY-MANAGED"
  PY="$BASE/python/bin/python3"
  "$UV" pip install --python "$PY" torch torchvision --index-url "$INDICE"
  "$UV" pip install --python "$PY" -r "$FONTE/requirements.txt"
  if [ -n "$ONNX" ]; then
    "$UV" pip uninstall --python "$PY" onnxruntime
    "$UV" pip install --python "$PY" "$ONNX" "numpy<2"
  fi
  "$PY" -c "import torch, ultralytics, cv2, flask, psycopg, onnxruntime, cryptography; print('ok', torch.__version__, onnxruntime.get_available_providers())"
  # so serve para compilar extensoes: nao vai para o usuario
  rm -rf "$BASE/python/lib/python3.12/site-packages/torch/include"
  find "$BASE/python" -name __pycache__ -type d -prune -exec rm -rf {} +
  echo "$PLACA" > "$BASE/python/argos-variante.txt"
  fi

  passo "Pacote $PLACA"
  PKG="$PALCO/pkg-$PLACA/argos-epi-servidor"
  rm -rf "$PALCO/pkg-$PLACA"; mkdir -p "$PKG"
  cp -a "$APP/." "$PKG/"
  mv "$BASE/python" "$PKG/python"; rm -rf "$BASE"
  NOME="argos-epi-servidor-$VERSAO-$PLACA.tar.gz"
  rm -f "$SAIDA/linux/argos-epi-servidor-"*"-$PLACA.tar.gz"
  tar -C "$PALCO/pkg-$PLACA" --owner=0 --group=0 -cf - argos-epi-servidor | pigz -6 > "$SAIDA/linux/$NOME.tmp"
  mv "$SAIDA/linux/$NOME.tmp" "$SAIDA/linux/$NOME"
  TAM="$(stat -c %s "$SAIDA/linux/$NOME")"
  SHA="$(sha256sum "$SAIDA/linux/$NOME" | cut -d' ' -f1)"
  "$PKG/python/bin/python3" - "$LINUX_JSON" "$PLACA" "linux/$NOME" "$TAM" "$SHA" <<'EOF'
import json, sys
arq, placa, url, tam, sha = sys.argv[1:]
d = json.load(open(arq))
d[placa] = {'url': url, 'tamanho': int(tam), 'sha256': sha}
json.dump(d, open(arq, 'w'), indent=2)
EOF
  rm -rf "$PALCO/pkg-$PLACA"
  echo "   $NOME: $((TAM / 1048576)) MB"
done

passo "Pronto"
ls -la "$SAIDA/linux"
cat "$LINUX_JSON"
if mountpoint -q /mnt/w; then sync; umount /mnt/w || true; fi
