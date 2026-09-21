#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

GREEN="\033[92m"
BOLD="\033[1m"
YELLOW="\033[93m"
RED="\033[91m"
RESET="\033[0m"

say() {
  printf "%b\n" "$*"
}

find_python() {
  if [[ -n "${PYTHON_BIN:-}" ]] && command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    printf "%s" "$PYTHON_BIN"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf "python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    printf "python"
    return 0
  fi
  return 1
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

say ""
say "${GREEN}${BOLD}Argos EPI v17 - configuracao${RESET}"
say "[bootstrap] Procurando Python e preparando estrutura do projeto..."
say ""

PYTHON_BIN="$(find_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  say "${RED}[ERRO] Python nao encontrado.${RESET}"
  say "Instale Python 3.11+ e execute este script novamente."
  exit 1
fi

mkdir -p bin models dados/users dados/epis dados/faces

"$PYTHON_BIN" scripts/banner.py --subtitle "Configuracao do ambiente - Linux/macOS, PyTorch, YOLO e modelos" || true
say "Projeto : $ROOT_DIR"
say "Python  : $("$PYTHON_BIN" --version 2>&1)"
say "Modo    : instalacao assistida para shell"
say ""

say "[1/5] Criando ambiente virtual..."
if [[ ! -d venv ]]; then
  "$PYTHON_BIN" -m venv venv
fi

VENV_PY="$(resolve_venv_python || true)"
if [[ -z "$VENV_PY" ]]; then
  say "${RED}[ERRO] Python do ambiente virtual nao foi criado.${RESET}"
  exit 1
fi
say "[OK] venv pronto."
say ""

say "[2/5] Atualizando pip..."
"$VENV_PY" -m pip install --upgrade pip
say ""

say "[3/5] Selecione o hardware para PyTorch:"
say "  [1] NVIDIA CUDA 12.1 - melhor performance em GPUs compativeis"
say "  [2] NVIDIA CUDA 12.4 - drivers mais recentes"
say "  [3] CPU apenas       - maior compatibilidade"
say ""

GPU_CHOICE="${GPU_CHOICE:-}"
if [[ -z "$GPU_CHOICE" && -t 0 ]]; then
  read -r -p "   Digite 1, 2 ou 3: " GPU_CHOICE
fi
GPU_CHOICE="${GPU_CHOICE:-3}"
say ""

case "$GPU_CHOICE" in
  1)
    TORCH_LABEL="PyTorch CUDA 12.1"
    "$VENV_PY" -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
    ;;
  2)
    TORCH_LABEL="PyTorch CUDA 12.4"
    "$VENV_PY" -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
    ;;
  *)
    TORCH_LABEL="PyTorch CPU"
    "$VENV_PY" -m pip install torch torchvision torchaudio
    ;;
esac
say "[OK] $TORCH_LABEL instalado."
say ""

say "[4/5] Instalando dependencias do projeto..."
"$VENV_PY" -m pip install -r requirements.txt
say "[OK] Dependencias instaladas."
say ""

say "[5/5] Verificando modelos e utilitarios..."
if [[ ! -f models/yolo26n.pt || ! -f models/yolo26s.pt ]]; then
  say "Baixando modelos YOLO..."
  "$VENV_PY" downloader.py bin modelos || say "${YELLOW}[AVISO] Nao foi possivel baixar os modelos agora.${RESET}"
else
  say "[OK] Modelos YOLO ja existem."
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  say "[OK] NVIDIA detectada. Depois de treinar/uploadar modelo, use a conversao TensorRT no painel quando disponivel."
else
  say "[INFO] NVIDIA nao detectada neste shell. O sistema ainda funciona em CPU."
fi

if command -v cloudflared >/dev/null 2>&1 || [[ -x ./cloudflared ]]; then
  say "[OK] cloudflared disponivel."
else
  say "[INFO] cloudflared nao encontrado. Instale pelo gerenciador do sistema se quiser tunel publico."
fi

say ""
"$VENV_PY" scripts/banner.py --subtitle "Configuracao concluida - verificacao final" --compact || true

"$VENV_PY" -c "import torch; print(f'  PyTorch     : v{torch.__version__} | CUDA: {torch.cuda.is_available()}')" 2>/dev/null || say "  [!!] PyTorch nao instalado"
"$VENV_PY" -c "import ultralytics; print('  Ultralytics : v' + ultralytics.__version__)" 2>/dev/null || say "  [!!] Ultralytics nao instalado"
"$VENV_PY" -c "import flask; print('  Flask       : v' + flask.__version__)" 2>/dev/null || say "  [!!] Flask nao instalado"
"$VENV_PY" -c "import cv2; print('  OpenCV      : v' + cv2.__version__)" 2>/dev/null || say "  [!!] OpenCV nao instalado"

say ""
say "====================================================="
say " Tudo pronto. Para iniciar o sistema, execute:"
say "   bash iniciar.sh"
say " Frontend local: http://localhost:8088"
say " Hub opcional  : configure BACKEND_HUB_URL no .env"
say "====================================================="
