"""Backend Hub - registry for Argos EPI worker backends.

Deploy this file on Render. Worker backends started behind Cloudflare send
their public URL and health metrics here. The Vercel frontend asks this hub
for the best available backend before calling the main API.

Two registries live here:

  /nodes/*     backends de inferencia (app.py na maquina do usuario)
  /training/*  paineis de treinamento (treinamento/acompanhar_treinamento/servidor.py --publico),
               usados pelo site https://github.com/AndrosoftStudio/treinamentoargosepi
               para achar o PC que esta treinando, sem ninguem digitar URL nenhuma.
"""
import hmac
import json
import os
import threading
import time
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS


app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

NODES = {}
TREINOS = {}
LOCK = threading.Lock()
HUB_API_KEY = os.environ.get("HUB_API_KEY", "").strip()
STALE_AFTER_SECONDS = int(os.environ.get("HUB_STALE_AFTER_SECONDS", "300"))
# o painel de treino manda heartbeat a cada 30 s, mas o PC de casa e o tunel oscilam:
# uma janela maior evita o site perder o no por causa de um heartbeat atrasado
TRAINING_STALE_AFTER_SECONDS = int(os.environ.get("HUB_TRAINING_STALE_AFTER_SECONDS", "180"))
PING_ON_HEARTBEAT = os.environ.get("HUB_PING_ON_HEARTBEAT", "0").strip().lower() in ("1", "true", "yes")
STORE_PATH = os.environ.get("HUB_STORE_PATH", os.path.join(os.getcwd(), "hub_nodes.json"))


def _now():
    return time.time()


if not HUB_API_KEY:
    # Sem chave, qualquer um registra um "backend" que responde 100% disponivel,
    # e o site da Vercel passa a mandar login e senha das pessoas para ele.
    print("[hub] AVISO: HUB_API_KEY vazia -- qualquer um pode registrar backend. "
          "Defina a chave no Render e no .env dos backends.")


def _chave_confere(recebida):
    # compare_digest: a comparacao com == deixava medir, pelo tempo, quantos
    # caracteres da chave estavam certos
    return bool(recebida) and hmac.compare_digest(str(recebida).encode(), HUB_API_KEY.encode())


def _authorized():
    if not HUB_API_KEY:
        return True
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and _chave_confere(auth[7:]):
        return True
    return _chave_confere(request.headers.get("X-Hub-Key"))


def _clean_url(url):
    url = str(url or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return url


def _load_store():
    global NODES, TREINOS
    if not os.path.exists(STORE_PATH):
        return
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    if "backends" in data or "training" in data:
        NODES = data.get("backends") or {}
        TREINOS = data.get("training") or {}
    else:  # formato antigo: o arquivo inteiro era o registro de backends
        NODES = data


def _save_store():
    try:
        folder = os.path.dirname(STORE_PATH)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(STORE_PATH, "w", encoding="utf-8") as f:
            json.dump({"backends": NODES, "training": TREINOS}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _prune_stale_nodes(registry=None, stale_after=None):
    registry = NODES if registry is None else registry
    stale_after = STALE_AFTER_SECONDS if stale_after is None else stale_after
    now = _now()
    removed = []
    with LOCK:
        for node_id, node in list(registry.items()):
            last_seen = float(node.get("last_seen") or 0)
            if now - last_seen > stale_after:
                removed.append(node_id)
                registry.pop(node_id, None)
        if removed:
            _save_store()
    return removed


def _measure_ping(url):
    if not url:
        return None
    start = time.perf_counter()
    try:
        r = requests.get(url.rstrip("/") + "/status", timeout=3)
        if not r.ok:
            return None
        return round((time.perf_counter() - start) * 1000, 1)
    except Exception:
        return None


def _score(node):
    availability = float(node.get("availability") or 0)
    ping = node.get("ping_ms")
    ping = 9999 if ping is None else float(ping)
    active_streams = float(node.get("active_streams") or 0)
    return round((100 - availability) * 3 + ping * 0.4 + active_streams * 8, 2)


def _public_node(node, stale_after=None, com_score=True):
    stale_after = STALE_AFTER_SECONDS if stale_after is None else stale_after
    item = dict(node)
    last_seen = float(item.get("last_seen") or 0)
    item["online"] = (_now() - last_seen) <= stale_after
    item["age_seconds"] = round(max(0, _now() - last_seen), 1)
    if com_score:  # so faz sentido para backends de inferencia
        item["score"] = _score(item)
    item.pop("headers", None)
    return item


def _active_nodes():
    _prune_stale_nodes()
    with LOCK:
        nodes = [_public_node(v) for v in NODES.values()]
    active = [n for n in nodes if n.get("online") and n.get("url")]
    active.sort(key=lambda n: (n.get("score", 999999), n.get("ping_ms") or 9999))
    return active


def _upsert_node(payload, mode):
    node_id = str(payload.get("node_id") or "").strip()
    url = _clean_url(payload.get("url"))
    if not node_id:
        return None, ("node_id obrigatorio", 400)
    if not url:
        return None, ("url publica obrigatoria", 400)

    ping_ms = payload.get("ping_ms")
    if PING_ON_HEARTBEAT or mode == "register":
        measured = _measure_ping(url)
        if measured is not None:
            ping_ms = measured

    record = {
        "node_id": node_id,
        "url": url,
        "server_kind": payload.get("server_kind") or "unknown",
        "availability": int(payload.get("availability") or 0),
        "active_streams": int(payload.get("active_streams") or 0),
        "hardware": payload.get("hardware") or {},
        "metrics": payload.get("metrics") or {},
        "version": payload.get("version"),
        "ping_ms": ping_ms,
        "registered_at": payload.get("registered_at"),
        "last_seen": _now(),
    }
    with LOCK:
        previous = NODES.get(node_id, {})
        if previous.get("registered_at") and not record.get("registered_at"):
            record["registered_at"] = previous["registered_at"]
        if not record.get("registered_at"):
            record["registered_at"] = _now()
        NODES[node_id] = record
        _save_store()
    return _public_node(record), None


# ── Paineis de treinamento ────────────────────────────────────────
def _upsert_training(payload):
    """Registra o painel de treino do PC. O site so precisa da URL publica;
    o resumo (situacao, progresso, ETA) vem junto para a lista ficar util sem abrir cada no."""
    node_id = str(payload.get("node_id") or "").strip()
    url = _clean_url(payload.get("url"))
    if not node_id:
        return None, ("node_id obrigatorio", 400)
    if not url:
        return None, ("url publica obrigatoria", 400)

    def _num(valor):
        try:
            return round(float(valor), 2)
        except (TypeError, ValueError):
            return None

    record = {
        "node_id": node_id,
        "url": url,
        "host": str(payload.get("host") or "")[:80] or None,
        "porta": payload.get("porta"),
        "nome": str(payload.get("nome") or "")[:80] or None,
        "situacao": str(payload.get("situacao") or "")[:40] or None,
        "etapa": str(payload.get("etapa") or "")[:40] or None,
        "progresso": _num(payload.get("progresso")),
        "eta_s": _num(payload.get("eta_s")),
        "epoca": payload.get("epoca"),
        "epocas": payload.get("epocas"),
        "protegido": bool(payload.get("protegido")),
        "version": payload.get("version"),
        "registered_at": payload.get("registered_at"),
        "last_seen": _now(),
    }
    with LOCK:
        previous = TREINOS.get(node_id, {})
        if previous.get("registered_at") and not record.get("registered_at"):
            record["registered_at"] = previous["registered_at"]
        if not record.get("registered_at"):
            record["registered_at"] = _now()
        TREINOS[node_id] = record
        _save_store()
    return _public_node(record, TRAINING_STALE_AFTER_SECONDS, com_score=False), None


def _active_training():
    _prune_stale_nodes(TREINOS, TRAINING_STALE_AFTER_SECONDS)
    with LOCK:
        nodes = [_public_node(v, TRAINING_STALE_AFTER_SECONDS, com_score=False) for v in TREINOS.values()]
    active = [n for n in nodes if n.get("online") and n.get("url")]
    # o painel mais recente ganha: quando o PC reinicia, o tunel muda de URL e o registro antigo
    # ainda fica alguns minutos dentro da janela de validade
    active.sort(key=lambda n: float(n.get("last_seen") or 0), reverse=True)
    return active


@app.route("/health", methods=["GET"])
def health():
    _prune_stale_nodes()
    _prune_stale_nodes(TREINOS, TRAINING_STALE_AFTER_SECONDS)
    with LOCK:
        node_count = len(NODES)
        training_count = len(TREINOS)
    return jsonify({"ok": True, "nodes": node_count, "training_nodes": training_count,
                    "stale_after_seconds": STALE_AFTER_SECONDS,
                    "training_stale_after_seconds": TRAINING_STALE_AFTER_SECONDS, "ts": _now()})


@app.route("/nodes/register", methods=["POST"])
def register_node():
    if not _authorized():
        return jsonify({"error": "Nao autorizado"}), 401
    node, err = _upsert_node(request.get_json(force=True, silent=True) or {}, "register")
    if err:
        msg, code = err
        return jsonify({"error": msg}), code
    return jsonify({"ok": True, "node": node}), 201


@app.route("/nodes/heartbeat", methods=["POST"])
def heartbeat_node():
    if not _authorized():
        return jsonify({"error": "Nao autorizado"}), 401
    node, err = _upsert_node(request.get_json(force=True, silent=True) or {}, "heartbeat")
    if err:
        msg, code = err
        return jsonify({"error": msg}), code
    return jsonify({"ok": True, "node": node})


@app.route("/nodes", methods=["GET"])
@app.route("/backends", methods=["GET"])
def list_nodes():
    return jsonify({"nodes": _active_nodes(), "stale_after_seconds": STALE_AFTER_SECONDS})


@app.route("/nodes/best", methods=["GET"])
@app.route("/backends/best", methods=["GET"])
def best_node():
    nodes = _active_nodes()
    if not nodes:
        return jsonify({"error": "Nenhum backend disponivel", "nodes": []}), 503
    return jsonify({"node": nodes[0], "nodes": nodes})


@app.route("/training/register", methods=["POST"])
def register_training():
    if not _authorized():
        return jsonify({"error": "Nao autorizado"}), 401
    node, err = _upsert_training(request.get_json(force=True, silent=True) or {})
    if err:
        msg, code = err
        return jsonify({"error": msg}), code
    return jsonify({"ok": True, "node": node}), 201


@app.route("/training/heartbeat", methods=["POST"])
def heartbeat_training():
    if not _authorized():
        return jsonify({"error": "Nao autorizado"}), 401
    node, err = _upsert_training(request.get_json(force=True, silent=True) or {})
    if err:
        msg, code = err
        return jsonify({"error": msg}), code
    return jsonify({"ok": True, "node": node})


@app.route("/training", methods=["GET"])
@app.route("/training/nodes", methods=["GET"])
def list_training():
    return jsonify({"nodes": _active_training(), "stale_after_seconds": TRAINING_STALE_AFTER_SECONDS})


@app.route("/training/best", methods=["GET"])
def best_training():
    nodes = _active_training()
    if not nodes:
        return jsonify({"error": "Nenhum painel de treino disponivel", "nodes": []}), 503
    return jsonify({"node": nodes[0], "nodes": nodes})


@app.route("/training/<node_id>", methods=["DELETE"])
def forget_training(node_id):
    """Tira um painel da lista na hora, sem esperar a janela de validade (usado ao fechar o painel)."""
    if not _authorized():
        return jsonify({"error": "Nao autorizado"}), 401
    with LOCK:
        existia = TREINOS.pop(str(node_id), None) is not None
        if existia:
            _save_store()
    return jsonify({"ok": True, "removido": existia})


_load_store()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
