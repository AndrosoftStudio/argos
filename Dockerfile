# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

# cu124 = GPU NVIDIA | cpu = https://download.pytorch.org/whl/cpu
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124
ARG INSTALL_TENSORRT=0
ARG TARGETARCH

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 ca-certificates ffmpeg \
 && rm -rf /var/lib/apt/lists/*

ADD --chmod=755 https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${TARGETARCH} /usr/local/bin/cloudflared

WORKDIR /app

RUN pip install torch torchvision --index-url ${TORCH_INDEX_URL}

COPY requirements.txt .
RUN pip install -r requirements.txt \
 && if [ "$INSTALL_TENSORRT" = "1" ]; then pip install onnx onnxslim tensorrt; fi

COPY backend/ backend/
COPY frontend/ frontend/
COPY scripts/ scripts/
COPY run.py .

RUN mkdir -p dados/users models uploads

EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=6s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/status', timeout=5)" || exit 1

CMD ["python", "run.py"]
