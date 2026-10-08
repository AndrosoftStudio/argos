#!/bin/bash
# Gera o Argos EPI Servidor para Linux x64 ja compilado, um .tar.gz por placa:
#   $SAIDA/linux/argos-epi-servidor-<versao>-<placa>.tar.gz   (placa: nvidia | cpu)
#   $SAIDA/linux/argos-tensorrt-nvidia-<id>.tar.gz            (opcional: bibliotecas do TensorRT, o botao do painel baixa)
#   $SAIDA/linux/linux.json                                   (url, tamanho e sha256: vai para o latest.json)
# O pacote nao leva codigo-fonte: o motor (motor/argos-motor) e o supervisor + backend compilados, com o
# Python e as bibliotecas em .so na mesma pasta; a janela e o Electron com o app em resources/app.asar;
# a interface vai no recursos.pak; os modelos (YOLO e rosto) ja vem em models/. Nada e baixado depois.
# Roda dentro de um container Ubuntu (do Windows, com o Docker Desktop). O trabalho fica no disco do
# container (ext4), uma placa por vez e sem cache do uv, e o .tar.gz sai direto na pasta de saida (D:),
# para o disco do Docker no C: quase nao crescer:
#   docker run --rm -v "<pasta v20>:/fonte:ro" -v D:\argos-build\saida:/saida \
#     -e VERSAO=20.3.0 -e PLACAS="cpu nvidia" ubuntu:22.04 bash /fonte/servidor_app/build/empacotar_linux.sh
set -euo pipefail
VERSAO="${VERSAO:-20.3.0}"
PLACAS="${PLACAS:-cpu nvidia}"
FONTE="${FONTE:-/fonte}"
SAIDA="${SAIDA:-/saida}"
PG_VERSAO="16.15.0"
ONNX_NVIDIA="onnxruntime-gpu==1.23.2"     # CUDA 12 + cuDNN 9, os mesmos do torch cu126 (a 1.24+ pede CUDA 13)
# NVIDIA: o onnx vai no motor. Com COM_TENSORRT=1 o TensorRT tambem e montado e sai como pacote opcional
# (ver abaixo); fica desligado por padrao porque dobra o disco usado na montagem (+8 GB no container).
EXTRAS_NVIDIA=("onnx>=1.12.0,<2.0.0")
[ "${COM_TENSORRT:-0}" = "1" ] && EXTRAS_NVIDIA+=("tensorrt-cu12>=10.3,!=10.1.0,!=10.2.0,<11")
passo() { printf '\n== %s\n' "$*"; }

passo "Ferramentas"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null
# libgl1/libglib2.0: o OpenCV pede (os Linux com interface grafica ja tem); binutils: o PyInstaller usa
apt-get install -y -qq --no-install-recommends curl ca-certificates unzip xz-utils pigz binutils libgl1 libglib2.0-0 >/dev/null

W=/tmp/argos-trabalho
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
# um Python so para montar o recursos.pak e o app.asar (nao vai para o pacote)
PYM() { "$UV" run --no-project --python 3.12 python "$@"; }

# copia de trabalho do codigo (o volume do Windows e lento e vem com fim de linha CRLF nos .sh)
SRC="$W/fonte"
rm -rf "$SRC"; mkdir -p "$SRC"
tar -C "$FONTE" --exclude=__pycache__ --exclude='*.pyc' --exclude=./backend/hub -cf - \
  backend frontend scripts servidor_app run.py requirements.txt | tar -C "$SRC" -xf -

# ---------------------------------------------------------------- programa (igual para as placas)
passo "Montando o programa $VERSAO (Electron $ELECTRON_TAG, PostgreSQL $PG_VERSAO)"
APP="$PALCO/app"
rm -rf "$APP"; mkdir -p "$APP/models/insightface/models/buffalo_l" "$APP/bin"
for m in argos_epi_v1.pt argos_epi_v1.json yolo26n.pt yolo26s.pt yolo26n-pose.pt yolo26s-pose.pt yolo26m-pose.pt; do
  cp "$FONTE/models/$m" "$APP/models/"; done
# modelos de rosto (SCRFD + ArcFace do InsightFace buffalo_l): vao no pacote, nada e baixado depois
ROSTO="$FONTE/models/insightface/models/buffalo_l"
if [ -s "$ROSTO/det_10g.onnx" ] && [ -s "$ROSTO/w600k_r50.onnx" ]; then
  cp "$ROSTO/det_10g.onnx" "$ROSTO/w600k_r50.onnx" "$APP/models/insightface/models/buffalo_l/"
else
  baixar https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip "$CACHE/buffalo_l.zip"
  unzip -q -j "$CACHE/buffalo_l.zip" det_10g.onnx w600k_r50.onnx -d "$APP/models/insightface/models/buffalo_l"
fi
cp "$CF" "$APP/bin/cloudflared"
PYM "$SRC/servidor_app/build/montar_recursos.py" "$SRC" "$APP/recursos.pak"
for f in argos-epi-servidor instalar.sh desinstalar.sh; do
  sed 's/\r$//' "$SRC/servidor_app/linux/$f" > "$APP/$f"; chmod +x "$APP/$f"; done
cp "$SRC/servidor_app/linux/argos-epi-servidor.png" "$APP/argos-epi-servidor.png"
echo "$VERSAO" > "$APP/VERSAO.txt"
cat > "$APP/LEIA-ME.txt" <<EOF
Argos EPI Servidor $VERSAO para Linux
Servidor de cameras com IA para EPIs (TCC SENAI Lauro de Freitas/BA).
Instalar:     ./instalar.sh   (sem sudo; fica em ~/.local/share/argos-epi-servidor e no menu de aplicativos)
Desinstalar:  ~/.local/share/argos-epi-servidor/desinstalar.sh
Os dados (banco, rostos, gravacoes) ficam na pasta dados e nao sao apagados ao atualizar.
EOF
# janela: Electron pronto + o app empacotado (app.asar)
mkdir -p "$APP/janela" "$W/asar"
unzip -q "$ELECTRON_ZIP" -d "$APP/janela"
mv "$APP/janela/electron" "$APP/janela/argos-epi-servidor"
rm -f "$APP/janela/resources/default_app.asar"
rm -rf "$W/asar"/*
for f in package.json main.js preload.js; do sed 's/\r$//' "$SRC/servidor_app/linux/app/$f" > "$W/asar/$f"; done
sed -i "s/\"version\": \"[^\"]*\"/\"version\": \"$VERSAO\"/" "$W/asar/package.json"
PYM "$SRC/servidor_app/build/montar_asar.py" "$W/asar" "$APP/janela/resources/app.asar"
# PostgreSQL portatil (zonky: bin, lib, share ja prontos)
mkdir -p "$APP/bin/pgsql"
( cd "$CACHE" && rm -rf pgjar && mkdir pgjar && cd pgjar && unzip -q "$PG_JAR" && tar -xJf ./*.txz -C "$APP/bin/pgsql" )
rm -rf "$CACHE/pgjar"
test -x "$APP/bin/pgsql/bin/initdb"

# ---------------------------------------------------------------- motor por placa + pacote
LINUX_JSON="$SAIDA/linux/linux.json"
[ -f "$LINUX_JSON" ] || echo '{}' > "$LINUX_JSON"
for PLACA in $PLACAS; do
  passo "Python 3.12 de montagem para $PLACA"
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
  # com placa, o onnxruntime comum da lugar ao da placa (os dois pacotes ocupam a mesma pasta)
  if [ -n "$ONNX" ]; then grep -vE '^\s*onnxruntime' "$SRC/requirements.txt" > "$BASE/requirements.txt"
  else cp "$SRC/requirements.txt" "$BASE/requirements.txt"; fi
  "$UV" pip install --python "$PY" -r "$BASE/requirements.txt" pyinstaller
  [ -z "$ONNX" ] || "$UV" pip install --python "$PY" "$ONNX" "numpy<2"
  if [ "$PLACA" = "nvidia" ]; then
    # pelo pip: o tensorrt-cu12 monta as dependencias com ele (o uv nao serve); numpy travado na versao instalada
    "$PY" -c "import numpy; print('numpy==' + numpy.__version__)" > "$BASE/restricoes.txt"
    "$PY" -m pip install --disable-pip-version-check --no-cache-dir -q -c "$BASE/restricoes.txt" "${EXTRAS_NVIDIA[@]}"
  fi
  "$PY" -c "import torch, ultralytics, cv2, flask, psycopg, onnxruntime, cryptography; print('ok', torch.__version__, onnxruntime.get_available_providers())"

  passo "Compilando o motor ($PLACA)"
  PYI="$PALCO/pyi-$PLACA"
  rm -rf "$PYI"; mkdir -p "$PYI"
  ARGOS_RAIZ="$SRC" ARGOS_PLACA="$PLACA" "$PY" -m PyInstaller --noconfirm --log-level WARN \
    --distpath "$PYI/dist" --workpath "$PYI/work" "$SRC/servidor_app/build/motor.spec"
  rm -rf "$BASE" "$PYI/work"            # o Python de montagem nao vai para o usuario (e libera disco)
  MOTOR="$PYI/dist/motor"
  # so serve para compilar extensoes (e scripts de exemplo das bibliotecas): nao vai para o usuario
  find "$MOTOR" \( -name '*.h' -o -name '*.hpp' -o -name '*.cmake' -o -name '*.pyi' -o -name '*.a' \
    -o -name '*.sh' -o -name '*.js' -o -name '*.html' -o -name '*.ipynb' \) -type f -delete
  if find "$MOTOR" \( -name '*.py' -o -name '*.pyw' \) -type f | grep -q .; then
    echo "sobrou codigo-fonte no motor:"; find "$MOTOR" -name '*.py' -type f | head -5; exit 1
  fi
  echo "$PLACA" > "$MOTOR/argos-variante.txt"
  TEM_TRT=0; [ -d "$MOTOR/tensorrt_libs" ] && TEM_TRT=1
  [ "$TEM_TRT" = 1 ] && echo montagem > "$MOTOR/argos-trt-id.txt"     # o conferir abre o TensorRT tambem
  # autoteste: abre o PyTorch, o OpenCV e o backend inteiro, detecta num quadro e procura rostos
  INSIGHTFACE_ROOT="$APP/models/insightface" "$MOTOR/argos-motor" conferir --rosto --detectar "$APP/models/yolo26n.pt"
  id_da_pasta() { ( cd "$1" && find . -type f ! -name argos-motor ! -name 'argos-*-id.txt' -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-10 ); }
  if [ "$TEM_TRT" = 1 ]; then
    # TensorRT: GBs de bibliotecas que poucos usam. Saem do motor para um pacote opcional, que o botao
    # "Instalar dependencias do TensorRT" do painel baixa ja compilado e extrai em motor/
    PT="$PALCO/trt-$PLACA"
    rm -rf "$PT"; mkdir -p "$PT/motor"
    rm -f "$MOTOR/argos-trt-id.txt"
    mv "$MOTOR/tensorrt_libs" "$MOTOR/tensorrt_bindings" "$PT/motor/"
    find "$MOTOR" -maxdepth 1 -type d -name 'tensorrt_cu12*.dist-info' -exec mv {} "$PT/motor/" \;
    "$MOTOR/argos-motor" conferir     # e continua abrindo sem ele
  fi
  ID_MOTOR="$(id_da_pasta "$MOTOR")"
  echo "$ID_MOTOR" > "$MOTOR/argos-motor-id.txt"
  if [ "$TEM_TRT" = 1 ]; then
    ID_TRT="$(id_da_pasta "$PT/motor")"
    echo "$ID_TRT" > "$PT/motor/argos-trt-id.txt"
    NOME_TRT="argos-tensorrt-$PLACA-$ID_TRT.tar.gz"
    rm -f "$SAIDA/linux/argos-tensorrt-$PLACA-"*.tar.gz
    tar -C "$PT" --owner=0 --group=0 -cf - motor | pigz -6 > "$SAIDA/linux/$NOME_TRT.tmp"
    mv "$SAIDA/linux/$NOME_TRT.tmp" "$SAIDA/linux/$NOME_TRT"
    PYM - "$LINUX_JSON" "$PLACA" "linux/$NOME_TRT" "$(stat -c %s "$SAIDA/linux/$NOME_TRT")" \
      "$(sha256sum "$SAIDA/linux/$NOME_TRT" | cut -d' ' -f1)" "$(du -sb "$PT" | cut -f1)" "$ID_TRT" "$ID_MOTOR" <<'EOF'
import json, sys
arq, placa, url, tam, sha, desc, id_, motor = sys.argv[1:]
d = json.load(open(arq))
d.setdefault('extras', {}).setdefault('tensorrt', {})[placa] = {
    'id': id_, 'url': url, 'tamanho': int(tam), 'sha256': sha, 'descompactado': int(desc), 'motor': motor}
json.dump(d, open(arq, 'w'), indent=2)
EOF
    rm -rf "$PT"
    echo "   $NOME_TRT pronto"
  fi

  passo "Pacote $PLACA"
  PKG="$PALCO/pkg-$PLACA/argos-epi-servidor"
  rm -rf "$PALCO/pkg-$PLACA"; mkdir -p "$PKG"
  cp -a "$APP/." "$PKG/"
  mv "$MOTOR" "$PKG/motor"; rm -rf "$PYI"
  NOME="argos-epi-servidor-$VERSAO-$PLACA.tar.gz"
  rm -f "$SAIDA/linux/argos-epi-servidor-"*"-$PLACA.tar.gz"
  tar -C "$PALCO/pkg-$PLACA" --owner=0 --group=0 -cf - argos-epi-servidor | pigz -6 > "$SAIDA/linux/$NOME.tmp"
  mv "$SAIDA/linux/$NOME.tmp" "$SAIDA/linux/$NOME"
  TAM="$(stat -c %s "$SAIDA/linux/$NOME")"
  SHA="$(sha256sum "$SAIDA/linux/$NOME" | cut -d' ' -f1)"
  DESC="$(du -sb "$PKG" | cut -f1)"
  PYM - "$LINUX_JSON" "$PLACA" "linux/$NOME" "$TAM" "$SHA" "$DESC" <<'EOF'
import json, sys
arq, placa, url, tam, sha, desc = sys.argv[1:]
d = json.load(open(arq))
d[placa] = {'url': url, 'tamanho': int(tam), 'sha256': sha, 'descompactado': int(desc)}
json.dump(d, open(arq, 'w'), indent=2)
EOF
  rm -rf "$PALCO/pkg-$PLACA"
  echo "   $NOME: $((TAM / 1048576)) MB"
done

passo "Pronto"
ls -la "$SAIDA/linux"
cat "$LINUX_JSON"
