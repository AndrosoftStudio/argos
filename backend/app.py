"""
Backend - Argos EPI v16.0 | AndrosoftStudio
FIXES:
  - Remover câmera: poll não readiciona stream removido
  - Alterar modelo: usa stream_id correto (não index)
  - FPS: processor usa predict() em vez de track()
NOVIDADES:
  - Cadastro de EPIs com fotos (dados/users/<uid>/epis/)
  - Cadastro de funcionários com fotos de rosto
  - Treinamento de modelos YOLO customizados por EPI e por funcionários
  - Botão de parar câmera em /cam (via endpoint /streams/stop_external)
"""
import os, sys, time, threading, subprocess, socket, base64, re, json, shutil, uuid, importlib.util
import numpy as np, cv2, torch, psutil
from flask import Flask, jsonify, request, Response, stream_with_context
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Fallback for bundled/offline venvs where python-dotenv was not installed.
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass

sys.path.insert(0, os.path.dirname(__file__))
from processor import VideoProcessor
import db
import face_id
import seguranca
import users as user_mgr
import areas as areas_mod
import auditoria
from camera_discovery import discover_cameras, list_local_webcams
from analysis_jobs import MODES as ANALYSIS_MODES, JobManager, valid_job_id
import ppe_taxonomy as tax
from ppe_taxonomy import item_label, default_required
from live_pipeline import (FPS_OPCOES, MODOS, RES_OPCOES, config_publica, normalizar_config, pack_frame,
                           unpack_frame)

try:
    from flask_sock import Sock
except ImportError:  # sem flask-sock o tempo real usa so HTTP binario
    Sock = None

app = Flask(__name__, static_folder='../frontend', static_url_path='/')
sock = Sock(app) if Sock else None

# Tamanho maximo de um envio (video, modelo .pt, zip de dataset). Sem limite, um
# envio so enchia o disco do servidor.
MAX_UPLOAD_MB = int(os.environ.get('ARGOS_MAX_UPLOAD_MB', '2048') or '2048')
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_MB * 1024 * 1024


def _ligado(nome, padrao='1'):
    return os.environ.get(nome, padrao).strip().lower() not in ('0', 'false', 'no', 'off', 'nao', 'não')


# Cadastro aberto: qualquer pessoa que chegue ao endereco do servidor cria conta.
# Bom para demonstracao; numa empresa, desligue (ARGOS_CADASTRO_ABERTO=0).
CADASTRO_ABERTO = _ligado('ARGOS_CADASTRO_ABERTO')
# Envio de modelo .pt: o arquivo e carregado pelo PyTorch, que executa codigo
# embutido nele. 'todos' (padrao), 'admin' (so administradores) ou 'desligado'.
UPLOAD_MODELOS = os.environ.get('ARGOS_UPLOAD_MODELOS', 'todos').strip().lower()
tentativas_login = seguranca.LimiteTentativas()


# ── Erros com mensagem que a pessoa entende ─────────────────────
# Sem isto o Flask devolvia uma pagina HTML, e o painel mostrava
# "Unexpected token <" no lugar de uma explicacao.
@app.errorhandler(413)
def _grande_demais(_e):
    return jsonify({'error': f'Arquivo grande demais. O limite é {MAX_UPLOAD_MB} MB.'}), 413


@app.errorhandler(404)
def _nao_encontrado(_e):
    return jsonify({'error': 'Endereço não encontrado.'}), 404


@app.errorhandler(405)
def _metodo_invalido(_e):
    return jsonify({'error': 'Operação não permitida neste endereço.'}), 405


@app.errorhandler(500)
def _erro_interno(e):
    print(f'[erro] {request.method} {request.path}: {e}')
    return jsonify({'error': 'Algo deu errado no servidor. Tente de novo em instantes.'}), 500

DEFAULT_CORS_ORIGINS = 'https://argosepi.vercel.app,http://localhost:8088,http://127.0.0.1:8088'
CORS_ORIGINS = {o.strip().rstrip('/') for o in os.environ.get('CORS_ORIGINS', DEFAULT_CORS_ORIGINS).split(',') if o.strip()}
# Previews da Vercel: argosepi-<hash>-<equipe>.vercel.app
VERCEL_PREVIEW_ORIGIN = re.compile(r'^https://argosepi(-[a-z0-9-]+)?\.vercel\.app$')
CORS_ALLOW_HEADERS = "Content-Type,Authorization,ngrok-skip-browser-warning,X-Stream-Id,X-Frame-Seq"
CORS_ALLOW_METHODS = "GET,POST,PUT,DELETE,OPTIONS"

def _allowed_origin(origin):
    if not origin:
        return None
    if '*' in CORS_ORIGINS:
        return '*'
    origin = origin.rstrip('/')
    if origin in CORS_ORIGINS or VERCEL_PREVIEW_ORIGIN.match(origin):
        return origin
    return None

@app.after_request
def cors_h(r):
    origin = _allowed_origin(request.headers.get("Origin"))
    if origin:
        r.headers["Access-Control-Allow-Origin"] = origin
        r.headers["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
        r.headers["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
        r.headers["Access-Control-Max-Age"] = "600"
    if origin != '*':
        r.vary.add("Origin")
    return r

@app.before_request
def pre():
    if request.method == "OPTIONS":
        return app.make_default_options_response()

# ── Globals ─────────────────────────────────────────────────────
streams: dict = {}
streams_lock = threading.Lock()
user_streams: dict = {}
user_streams_lock = threading.Lock()
# Pausa da IA: e de cada usuario, nao do servidor. Enquanto era uma variavel so,
# um usuario apertando "Pausar IA" congelava as cameras de todo mundo -- e o outro
# nao via aviso nenhum, so o quadro que nunca chegava.
paused_por_uid: dict = {}
paused_lock = threading.Lock()


def _pausado(uid) -> bool:
    if not uid:
        return False
    with paused_lock:
        return bool(paused_por_uid.get(uid))


def _definir_pausa(uid, valor=None) -> bool:
    with paused_lock:
        novo = (not paused_por_uid.get(uid)) if valor is None else bool(valor)
        if novo:
            paused_por_uid[uid] = True
        else:
            paused_por_uid.pop(uid, None)
        return novo
cloudflare_url = None
local_ip = None
_device_global = 'cpu'
# FIX remover câmera: conjunto de streams que o usuário explicitamente removeu
removed_streams: set = set()
removed_streams_lock = threading.Lock()
optimization_jobs: dict = {}
optimization_jobs_lock = threading.Lock()
hub_thread_started = False
hub_thread_lock = threading.Lock()
hub_event = threading.Event()
camera_scan_jobs: dict = {}
camera_scan_jobs_lock = threading.Lock()
TENSORRT_JOB_TIMEOUT_SEC = int(os.environ.get('EPI_TENSORRT_JOB_TIMEOUT_SEC', '2700') or '2700')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(BASE_DIR, 'models')
DADOS_DIR = os.path.join(BASE_DIR, 'dados', 'users')
HUB_URL = os.environ.get('BACKEND_HUB_URL', '').strip().rstrip('/')
PUBLIC_BACKEND_URL = os.environ.get('PUBLIC_BACKEND_URL', '').strip().rstrip('/')
BACKEND_NODE_ID = os.environ.get('BACKEND_NODE_ID') or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
HUB_API_KEY = os.environ.get('HUB_API_KEY', '').strip()

# ── Helpers ──────────────────────────────────────────────────────
def _token():
    auth = request.headers.get('Authorization','')
    if auth.startswith('Bearer '): return auth[7:]
    return request.args.get('token') or (request.get_json(silent=True) or {}).get('token')

def _auth():
    t = _token()
    if not t: return None, (jsonify({'error':'Faça login para continuar.'}), 401)
    if t.startswith('cam_'):
        cam_info = user_mgr.validate_cam_token(t)
        if not cam_info: return None, (jsonify({'error':'Este link de celular foi removido. Peça um link novo no painel.'}), 401)
        u = user_mgr.get_user(cam_info['uid'])
        if not u or u.get('blocked'):
            return None, (jsonify({'error':'A conta dona deste celular não está ativa.'}), 401)
        u['_cam_session'] = cam_info
        return u, None
    u = user_mgr.get_user_by_token(t)
    if not u: return None, (jsonify({'error':'Sua sessão expirou. Entre de novo.'}), 401)
    return u, None


def _stream_permitido(u, sid) -> bool:
    """A camera `sid` e de quem esta pedindo?

    Antes as rotas de camera confiavam no stream_id enviado pelo cliente: qualquer
    conta via, trocava o modelo ou removia a camera de outra conta, e /streams/notify
    ainda deixava "adotar" a camera alheia. Agora:
      - o celular (token cam_) so fala com o proprio stream (remote_<token>);
      - o painel so com os streams que criou (<uid>_N) e os dos celulares da conta."""
    if not seguranca.sid_valido(sid):
        return False
    if '_cam_session' in u:
        return sid == seguranca.sid_do_token_camera(u['_cam_session'].get('token'))
    uid = u.get('id')
    if _owns_stream(uid, sid):
        return True
    tokens = [t['token'] for t in user_mgr.list_cam_tokens(uid)] if sid.startswith('remote_') else ()
    return seguranca.stream_da_conta(sid, uid, tokens)


def _stream_negado():
    return jsonify({'error': 'Esta câmera pertence a outra conta.'}), 403

def _auth_admin():
    """Sessao de administrador. As rotas /admin expoem a lista de contas, o bloqueio
    de usuarios e o quadro ao vivo de qualquer camera: sem esta checagem, quem
    descobrisse o endereco do tunel fazia tudo isso sem senha."""
    u, err = _auth()
    if err:
        return None, err
    if '_cam_session' in u or (u.get('role') or 'user') != 'admin':
        return None, (jsonify({'error': 'Acesso restrito a administradores'}), 403)
    return u, None


def _get_model_path(model_name: str) -> str:
    """Modelo global de models/ (usado como base do treino de EPI)."""
    mp = os.path.join(MODELS_DIR, os.path.basename(model_name or ''))
    if os.path.exists(mp): return mp
    for fname in ['yolo26n.pt', 'yolo26s.pt']:
        fb = os.path.join(MODELS_DIR, fname)
        if os.path.exists(fb): return fb
    return mp

def _stream(stream_id, user_id=None):
    novo = None
    with streams_lock:
        if stream_id not in streams:
            # FIX remover câmera: não recriar streams que foram explicitamente removidos
            with removed_streams_lock:
                if stream_id in removed_streams:
                    return None
            p = VideoProcessor(client_id=stream_id)
            streams[stream_id] = p
            novo = p
            if user_id:
                with user_streams_lock:
                    user_streams.setdefault(user_id, [])
                    if stream_id not in user_streams[user_id]:
                        user_streams[user_id].append(stream_id)
        p = streams.get(stream_id)
    # fora do lock: le arquivos do usuario (areas, zonas, funcionarios)
    if novo is not None and user_id:
        _preparar_auditoria(user_id, novo)
    return p

def _user_epi_dir(uid: str) -> str:
    d = os.path.join(DADOS_DIR, uid, 'epis')
    os.makedirs(d, exist_ok=True)
    return d

def _user_func_dir(uid: str) -> str:
    d = os.path.join(DADOS_DIR, uid, 'funcionarios')
    os.makedirs(d, exist_ok=True)
    return d

def _user_models_dir(uid: str) -> str:
    d = os.path.join(DADOS_DIR, uid, 'models')
    os.makedirs(d, exist_ok=True)
    return d

class _Colecao:
    """Referencia a uma colecao no Postgres.

    Os metadados de EPIs e funcionarios sairam dos arquivos JSON e foram para o
    banco. Em vez de trocar as dezenas de chamadas espalhadas, _epi_meta_path e
    _func_meta_path devolvem esta referencia, e _load_json / _save_json
    reconhecem: com _Colecao vao ao banco, com uma string continuam lendo
    arquivo (ainda ha JSON solto em outras partes do sistema)."""
    __slots__ = ('tipo', 'uid')

    def __init__(self, tipo, uid):
        self.tipo, self.uid = tipo, uid


def _epi_meta_path(uid: str):
    return _Colecao('epis', uid)


def _func_meta_path(uid: str):
    return _Colecao('funcionarios', uid)


def _carregar_epis(uid: str) -> list:
    epis = []
    for e in db.consultar('SELECT * FROM epis WHERE uid=%s ORDER BY criado_em', (uid,)):
        extra = e.get('extra') or {}
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except ValueError:
                extra = {}
        fotos = [{'id': f['id'], 'path': f['caminho']} for f in db.consultar(
            'SELECT id, caminho FROM epi_fotos WHERE epi_id=%s ORDER BY id', (e['id'],))]
        epis.append({'id': e['id'], 'nome': e['nome'], 'descricao': e.get('descricao') or '',
                     'cor': e.get('cor') or '', 'fotos': fotos,
                     'modelo_treinado': bool(e.get('modelo_treinado')),
                     'criado_em': e.get('criado_em'), **extra})
    return epis


def _salvar_epis(uid: str, lista):
    """Substitui a colecao inteira do usuario, como o arquivo JSON fazia."""
    conhecidos = {'id', 'nome', 'descricao', 'cor', 'fotos', 'modelo_treinado', 'criado_em'}
    with db.pool().connection() as c:
        ids = [e['id'] for e in (lista or []) if e.get('id')]
        if ids:
            c.execute('DELETE FROM epis WHERE uid=%s AND NOT (id = ANY(%s))', (uid, ids))
        else:
            c.execute('DELETE FROM epis WHERE uid=%s', (uid,))
        for e in lista or []:
            extra = {k: v for k, v in e.items() if k not in conhecidos}
            c.execute(
                'INSERT INTO epis (id,uid,nome,descricao,cor,modelo_treinado,extra,criado_em)'
                ' VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)'
                ' ON CONFLICT (id) DO UPDATE SET nome=EXCLUDED.nome, descricao=EXCLUDED.descricao,'
                ' cor=EXCLUDED.cor, modelo_treinado=EXCLUDED.modelo_treinado, extra=EXCLUDED.extra',
                (e['id'], uid, e.get('nome') or '', e.get('descricao') or '', e.get('cor') or '',
                 bool(e.get('modelo_treinado')), json.dumps(extra),
                 float(e.get('criado_em') or time.time())))
            fotos = e.get('fotos') or []
            fids = [f['id'] for f in fotos if f.get('id')]
            if fids:
                c.execute('DELETE FROM epi_fotos WHERE epi_id=%s AND NOT (id = ANY(%s))', (e['id'], fids))
            else:
                c.execute('DELETE FROM epi_fotos WHERE epi_id=%s', (e['id'],))
            for f in fotos:
                c.execute('INSERT INTO epi_fotos (id,epi_id,caminho) VALUES (%s,%s,%s)'
                          ' ON CONFLICT (id) DO UPDATE SET caminho=EXCLUDED.caminho',
                          (f['id'], e['id'], f.get('path') or ''))


def _carregar_funcs(uid: str) -> list:
    funcs = []
    for f in db.consultar('SELECT * FROM funcionarios WHERE uid=%s ORDER BY criado_em', (uid,)):
        fotos = [{'id': r['id'], 'path': r['caminho'], 'tem_rosto': bool(r['embedding'])}
                 for r in db.consultar(
                     'SELECT id, caminho, embedding FROM func_fotos WHERE func_id=%s ORDER BY id',
                     (f['id'],))]
        funcs.append({'id': f['id'], 'nome': f['nome'], 'cargo': f.get('cargo') or '',
                      'matricula': f.get('matricula') or '', 'fotos_rosto': fotos,
                      'criado_em': f.get('criado_em'),
                      # com embedding a pessoa ja e reconhecivel: nao existe mais treino
                      'modelo_treinado': any(x['tem_rosto'] for x in fotos)})
    return funcs


def _salvar_funcs(uid: str, lista):
    with db.pool().connection() as c:
        ids = [f['id'] for f in (lista or []) if f.get('id')]
        if ids:
            c.execute('DELETE FROM funcionarios WHERE uid=%s AND NOT (id = ANY(%s))', (uid, ids))
        else:
            c.execute('DELETE FROM funcionarios WHERE uid=%s', (uid,))
        for f in lista or []:
            c.execute(
                'INSERT INTO funcionarios (id,uid,nome,cargo,matricula,criado_em)'
                ' VALUES (%s,%s,%s,%s,%s,%s)'
                ' ON CONFLICT (id) DO UPDATE SET nome=EXCLUDED.nome, cargo=EXCLUDED.cargo,'
                ' matricula=EXCLUDED.matricula',
                (f['id'], uid, f.get('nome') or '', f.get('cargo') or '',
                 f.get('matricula') or '', float(f.get('criado_em') or time.time())))
            fotos = f.get('fotos_rosto') or []
            fids = [x['id'] for x in fotos if x.get('id')]
            if fids:
                c.execute('DELETE FROM func_fotos WHERE func_id=%s AND NOT (id = ANY(%s))',
                          (f['id'], fids))
            else:
                c.execute('DELETE FROM func_fotos WHERE func_id=%s', (f['id'],))
            for x in fotos:
                # o embedding e gravado no upload da foto; aqui vai so o cadastro
                c.execute('INSERT INTO func_fotos (id,func_id,caminho) VALUES (%s,%s,%s)'
                          ' ON CONFLICT (id) DO UPDATE SET caminho=EXCLUDED.caminho',
                          (x['id'], f['id'], x.get('path') or ''))


def _load_json(ref) -> list:
    if isinstance(ref, _Colecao):
        try:
            return _carregar_epis(ref.uid) if ref.tipo == 'epis' else _carregar_funcs(ref.uid)
        except Exception as e:
            print(f'[db] falha ao ler {ref.tipo} de {ref.uid}: {e}')
            return []
    if os.path.exists(ref):
        try:
            with open(ref) as f: return json.load(f)
        except: return []
    return []

def _save_json(ref, data):
    if isinstance(ref, _Colecao):
        (_salvar_epis if ref.tipo == 'epis' else _salvar_funcs)(ref.uid, data)
        return
    with open(ref, 'w') as f: json.dump(data, f, ensure_ascii=False, indent=2)


def _is_cuda_backend() -> bool:
    try:
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _server_kind() -> str:
    if _is_cuda_backend():
        return 'nvidia'
    try:
        import torch_directml  # noqa: F401
        return 'dml'
    except Exception:
        return 'cpu'


def _engine_path_for_model(model_path: str) -> str:
    root, _ = os.path.splitext(model_path)
    return root + '.engine'


def _tensorrt_dependency_error() -> str | None:
    if not _is_cuda_backend():
        return 'TensorRT requer GPU NVIDIA/CUDA neste backend.'
    missing = []
    for module_name in ('onnx', 'tensorrt'):
        if importlib.util.find_spec(module_name) is None:
            missing.append(module_name)
    if missing:
        return 'Dependencias ausentes para TensorRT: ' + ', '.join(missing) + '.'
    return None


def _tensorrt_export_available() -> bool:
    return _tensorrt_dependency_error() is None


def _mark_tensorrt_job_error(engine_path: str, uid: str | None, model_name: str | None, data: dict, error: str) -> dict:
    updated = dict(data or {})
    updated.update({
        'status': 'error',
        'model': model_name or updated.get('model'),
        'engine_name': os.path.basename(engine_path),
        'error': error,
        'finished_at': time.time(),
    })
    with optimization_jobs_lock:
        optimization_jobs[engine_path] = updated
    if uid and model_name:
        _set_tensorrt_meta(uid, model_name, updated)
    return updated


def _tensorrt_status_for_path(model_path: str, uid=None, model_name=None) -> dict:
    engine_path = _engine_path_for_model(model_path)
    key = engine_path
    engine_exists = os.path.exists(engine_path)
    with optimization_jobs_lock:
        job = dict(optimization_jobs.get(key, {}))
    meta_rec = {}
    if uid and model_name:
        meta_rec = _load_model_meta(uid).get(model_name, {}) or {}
    trt_meta = meta_rec.get('tensorrt') if isinstance(meta_rec, dict) else None
    dep_error = _tensorrt_dependency_error()
    if job.get('status') == 'running' and not engine_exists:
        started_at = float(job.get('started_at') or 0)
        if started_at and time.time() - started_at > TENSORRT_JOB_TIMEOUT_SEC:
            job = _mark_tensorrt_job_error(
                engine_path, uid, model_name, job,
                'Conversao TensorRT excedeu o tempo limite. Verifique as dependencias e tente novamente.'
            )
    if not job and isinstance(trt_meta, dict) and trt_meta.get('status') == 'running' and not engine_exists:
        trt_meta = _mark_tensorrt_job_error(
            engine_path, uid, model_name, trt_meta,
            'Conversao TensorRT foi interrompida antes de terminar. Tente novamente.'
        )
    return {
        'available': _is_cuda_backend() and not dep_error,
        'enabled': os.environ.get('EPI_USE_TENSORRT', '1').strip().lower() not in ('0', 'false', 'no', 'off'),
        'dependency_error': dep_error,
        'engine_exists': engine_exists,
        'engine_name': os.path.basename(engine_path),
        'engine_path': engine_path if engine_exists else None,
        'job': job or trt_meta or None,
    }


def _set_tensorrt_meta(uid: str, model_name: str, data: dict):
    if not uid or not model_name:
        return
    meta = _load_model_meta(uid)
    rec = dict(meta.get(model_name, {}))
    rec['tensorrt'] = data
    rec['updated_at'] = time.time()
    meta[model_name] = rec
    _save_model_meta(uid, meta)


def _export_tensorrt_async(uid: str, model_name: str, model_path: str, reason='manual') -> dict:
    dep_error = _tensorrt_dependency_error()
    if dep_error:
        return {'started': False, 'status': 'missing_dependency', 'error': dep_error}
    if not model_path or not os.path.exists(model_path):
        return {'started': False, 'status': 'missing_model', 'error': 'Modelo nao encontrado.'}
    if not model_path.lower().endswith('.pt'):
        return {'started': False, 'status': 'unsupported', 'error': 'A conversao TensorRT parte de um arquivo .pt.'}

    engine_path = _engine_path_for_model(model_path)
    key = engine_path
    with optimization_jobs_lock:
        current = optimization_jobs.get(key)
        if current and current.get('status') == 'running':
            return {'started': False, 'status': 'running', 'engine_name': os.path.basename(engine_path)}
        optimization_jobs[key] = {
            'status': 'running',
            'model': model_name,
            'reason': reason,
            'started_at': time.time(),
            'engine_name': os.path.basename(engine_path),
        }
    _set_tensorrt_meta(uid, model_name, dict(optimization_jobs[key]))

    def _job():
        try:
            from ultralytics import YOLO as _YOLO
            model = _YOLO(model_path)
            model.export(format='engine', device=0, half=True, workspace=4, simplify=False, verbose=False)
            if not os.path.exists(engine_path):
                raise RuntimeError('Exportacao finalizou, mas o arquivo .engine nao foi criado.')
            data = {
                'status': 'ready',
                'model': model_name,
                'reason': reason,
                'engine_name': os.path.basename(engine_path),
                'engine_path': engine_path,
                'finished_at': time.time(),
            }
            with optimization_jobs_lock:
                optimization_jobs[key] = data
            _set_tensorrt_meta(uid, model_name, data)
            print(f"[TensorRT] Engine pronto: {engine_path}")
        except Exception as ex:
            data = {
                'status': 'error',
                'model': model_name,
                'reason': reason,
                'engine_name': os.path.basename(engine_path),
                'error': str(ex),
                'finished_at': time.time(),
            }
            with optimization_jobs_lock:
                optimization_jobs[key] = data
            _set_tensorrt_meta(uid, model_name, data)
            print(f"[TensorRT] ERRO {model_name}: {ex}")

    threading.Thread(target=_job, daemon=True, name=f"tensorrt-{os.path.basename(model_path)}").start()
    return {'started': True, 'status': 'running', 'engine_name': os.path.basename(engine_path)}


def _load_employee_model_for_stream(uid: str, processor):
    if not uid or not processor:
        return
    face_model = os.path.join(_user_models_dir(uid), 'funcionarios.pt')
    if os.path.exists(face_model):
        processor.load_extra_models([face_model])
    else:
        processor.load_extra_models([])
    _preparar_auditoria(uid, processor)


def _galeria_de(uid: str):
    """Embeddings cadastrados deste usuario, prontos para comparacao."""
    linhas = db.consultar(
        'SELECT ff.func_id, ff.embedding, f.nome FROM func_fotos ff'
        ' JOIN funcionarios f ON f.id = ff.func_id'
        ' WHERE f.uid = %s AND ff.embedding IS NOT NULL', (uid,))
    return face_id.galeria_de_linhas(linhas)


def _atualizar_galeria(uid: str):
    """Cadastro mudou: recarrega a galeria nos streams que estao rodando."""
    try:
        g = _galeria_de(uid)
    except Exception as e:
        print(f'[face] nao foi possivel montar a galeria de {uid}: {e}')
        return
    with streams_lock:
        alvos = list(streams.items())
    for sid, proc in alvos:
        if _owns_stream(uid, sid):
            try:
                proc.set_galeria(g)
            except Exception:
                pass


def _preparar_auditoria(uid: str, processor):
    """Liga o stream ao dono: sem isso o historico nao e gravado e as zonas nao valem."""
    if not uid or not processor:
        return
    try:
        processor.set_auditoria(uid, DADOS_DIR, _load_json(_func_meta_path(uid)))
        processor.set_zonas(areas_mod.carregar_zonas(uid, processor.client_id))
        processor.set_galeria(_galeria_de(uid))
    except Exception as e:
        print(f"[auditoria] nao foi possivel preparar {processor.client_id}: {e}")


def _aplicar_zonas_em_streams(uid: str, sid: str = None):
    """Propaga as zonas salvas para os processadores em execucao."""
    with streams_lock:
        alvos = [(s, p) for s, p in streams.items() if sid is None or s == sid]
    for s, p in alvos:
        if _owns_stream(uid, s):
            try:
                p.set_zonas(areas_mod.carregar_zonas(uid, s))
            except Exception:
                pass


def _atualizar_funcionarios_em_streams(uid: str):
    """Cadastro de funcionario mudou: atualiza o mapa nome -> id nos streams ativos."""
    try:
        funcs = _load_json(_func_meta_path(uid))
    except Exception:
        return
    with streams_lock:
        alvos = [(s, p) for s, p in streams.items()]
    for s, p in alvos:
        if _owns_stream(uid, s):
            try:
                p.set_funcionarios(funcs)
            except Exception:
                pass


def _safe_model_name(name: str) -> str:
    name = os.path.basename((name or '').strip())
    name = re.sub(r'[^a-zA-Z0-9._-]+', '_', name)
    if not name.lower().endswith('.pt'):
        name += '.pt'
    return name


def _read_model_info(model_path: str) -> dict:
    """{'classes', 'arquitetura'}; modelo ilegivel volta sem classes e como YOLO."""
    try:
        return VideoProcessor.read_model_info(model_path)
    except Exception:
        return {'classes': [], 'arquitetura': 'yolo'}


def _read_model_classes(model_path: str) -> list:
    return _read_model_info(model_path).get('classes', [])


def _guess_required_items(classes: list) -> list:
    known = default_required(classes or [])
    if known:
        return known
    if len(classes or []) >= 80:  # COCO puro: nenhuma classe e EPI (antes a lista trazia 'car', 'dog'...)
        return []
    return [str(c) for c in (classes or []) if str(c).strip() and str(c).strip().lower() != 'person']


def _base_model_display_name(name: str) -> str:
    """Nome amigavel dos modelos globais de models/, que aparecem para todos os usuarios."""
    stem = name[:-3] if name.endswith('.pt') else name
    tamanhos = {'n': 'nano, mais rápido', 's': 'small, mais preciso', 'm': 'medium', 'l': 'large',
                'x': 'extra large'}
    m = re.fullmatch(r'yolo(\d+)([nsmlx])', stem)
    if m:
        return f"YOLO{m.group(1)}{m.group(2)} ({tamanhos[m.group(2)]}, só pessoas)"
    m = re.fullmatch(r'rtdetr-([lx])', stem)
    if m:
        return f"RT-DETR {m.group(1)} (transformer, {tamanhos[m.group(1)]}, só pessoas)"
    if stem.startswith('argos_epi'):
        info = {}
        try:
            with open(os.path.join(MODELS_DIR, stem + '.json'), encoding='utf-8') as f:
                info = json.load(f)
        except (OSError, ValueError):
            pass
        versao = stem[len('argos_epi'):].strip('_') or 'v1'
        extra = f", mAP50 {info['map50_teste']:.2f}" if isinstance(info.get('map50_teste'), (int, float)) else ''
        if info.get('arquitetura') == 'detr':
            extra += ', DETR'
        return f"Argos EPI {versao} (treinado, {len(info.get('classes') or []) or 15} classes{extra})"
    return name


def _load_model_meta(uid: str) -> dict:
    if not uid:
        return {}
    try:
        fora = {}
        for l in db.consultar('SELECT nome, dados FROM modelos WHERE uid=%s', (uid,)):
            d = l['dados']
            if isinstance(d, str):
                try:
                    d = json.loads(d)
                except ValueError:
                    d = {}
            fora[l['nome']] = d or {}
        return fora
    except Exception as e:
        print(f'[db] falha ao ler modelos de {uid}: {e}')
        return {}


def _save_model_meta(uid: str, data: dict):
    if not uid:
        return
    with db.pool().connection() as c:
        nomes = list((data or {}).keys())
        if nomes:
            c.execute('DELETE FROM modelos WHERE uid=%s AND NOT (nome = ANY(%s))', (uid, nomes))
        else:
            c.execute('DELETE FROM modelos WHERE uid=%s', (uid,))
        for nome, rec in (data or {}).items():
            c.execute('INSERT INTO modelos (uid, nome, dados) VALUES (%s,%s,%s::jsonb)'
                      ' ON CONFLICT (uid, nome) DO UPDATE SET dados=EXCLUDED.dados',
                      (uid, nome, json.dumps(rec or {})))


def _upsert_model_meta(uid: str, model_name: str, classes=None, required_items=None, extra=None):
    meta = _load_model_meta(uid)
    rec = dict(meta.get(model_name, {}))
    if classes is not None:
        rec['classes'] = classes
    if required_items is not None:
        rec['required_items'] = required_items
    if extra:
        rec.update(extra)
    meta[model_name] = rec
    _save_model_meta(uid, meta)
    return rec


def _resolve_model_path_for_user(uid, model_name: str) -> str:
    """Modelo pelo nome: primeiro os globais de models/, depois os da propria conta.
    Antes aceitava caminho absoluto e procurava nas pastas de TODAS as contas: bastava
    saber o nome do .pt de outra empresa para usar o modelo treinado dela."""
    name = os.path.basename((model_name or '').strip())
    mp = os.path.join(MODELS_DIR, name)
    if name and os.path.exists(mp):
        return mp
    if uid and name:
        up = os.path.join(_user_models_dir(uid), name)
        if os.path.exists(up):
            return up
    for fname in ['yolo26n.pt', 'yolo26s.pt']:
        fb = os.path.join(MODELS_DIR, fname)
        if os.path.exists(fb):
            return fb
    return mp


def _base_default_model() -> str:
    """Modelo Argos treinado mais recente em models/ (treinamento/pipeline.py), senao o YOLO base."""
    argos = []
    if os.path.isdir(MODELS_DIR):
        argos = sorted((f for f in os.listdir(MODELS_DIR) if f.startswith('argos_epi') and f.endswith('.pt')),
                       key=lambda f: os.path.getmtime(os.path.join(MODELS_DIR, f)), reverse=True)
    return argos[0] if argos else 'yolo26n.pt'


def _default_model_for(uid):
    """(caminho, epis_exigidos) do modelo padrao do usuario."""
    meta = _load_model_meta(uid) if uid else {}
    name = next((n for n, rec in meta.items() if isinstance(rec, dict) and rec.get('default')), None)
    mp = _resolve_model_path_for_user(uid, name or _base_default_model())
    if not os.path.exists(mp):
        mp = _resolve_model_path_for_user(uid, _base_default_model())
    return mp, (meta.get(os.path.basename(mp)) or {}).get('required_items')

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8",80))
        ip = s.getsockname()[0]; s.close(); return ip
    except: return "127.0.0.1"

def _start_cf(port=8088):
    global cloudflare_url
    cmds = ["cloudflared",
            os.path.join(BASE_DIR,"cloudflared.exe"),
            os.path.join(BASE_DIR,"cloudflared")]
    cmd = None
    for c in cmds:
        try:
            if subprocess.run([c,"--version"],capture_output=True,timeout=3).returncode == 0:
                cmd = c; break
        except: pass
    if not cmd: print("[CF] cloudflared não encontrado"); return
    print(f"[CF] Iniciando tunnel → porta {port} (frontend)...")
    proc = subprocess.Popen([cmd,"tunnel","--url",f"http://localhost:{port}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    pat = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com")
    for line in iter(proc.stdout.readline,''):
        print(f"[CF] {line.strip()}")
        m = pat.search(line)
        if m:
            cloudflare_url = m.group(0)
            print(f"[CF] ✓ {cloudflare_url}")
            hub_event.set()
            break
    threading.Thread(target=lambda: [_ for _ in iter(proc.stdout.readline,'')], daemon=True).start()

def _sys_metrics():
    m = {"cpu": psutil.cpu_percent(0.1), "ram_pct": psutil.virtual_memory().percent,
         "ram_used": psutil.virtual_memory().used, "ram_total": psutil.virtual_memory().total,
         "disk_pct": psutil.disk_usage('/').percent, "disk_used": psutil.disk_usage('/').used,
         "disk_total": psutil.disk_usage('/').total, "gpu_name": None,
         "gpu_pct": None, "gpu_mem_used": None, "gpu_mem_total": None}
    try:
        if torch.cuda.is_available():
            m["gpu_name"] = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            m["gpu_mem_total"] = props.total_memory
            m["gpu_mem_used"] = torch.cuda.memory_allocated(0)
            m["gpu_pct"] = round(m["gpu_mem_used"]/m["gpu_mem_total"]*100,1)
    except: pass
    return m


def _availability_score(metrics=None) -> int:
    metrics = metrics or _sys_metrics()
    gpu_pct = metrics.get('gpu_pct')
    pressure = max(
        float(metrics.get('cpu') or 0),
        float(metrics.get('ram_pct') or 0),
        float(gpu_pct if gpu_pct is not None else 0),
    )
    with streams_lock:
        stream_penalty = min(len(streams) * 8, 35)
    return max(0, min(100, int(round(100 - pressure - stream_penalty))))


def _public_backend_url():
    return PUBLIC_BACKEND_URL or cloudflare_url


def _hub_headers():
    headers = {'Content-Type': 'application/json'}
    if HUB_API_KEY:
        headers['Authorization'] = 'Bearer ' + HUB_API_KEY
    return headers


def _hub_payload():
    metrics = _sys_metrics()
    with streams_lock:
        active_streams = len(streams)
    return {
        'node_id': BACKEND_NODE_ID,
        'url': _public_backend_url(),
        'server_kind': _server_kind(),
        'availability': _availability_score(metrics),
        'active_streams': active_streams,
        'hardware': {
            'cpu_count': psutil.cpu_count(logical=True),
            'gpu_name': metrics.get('gpu_name'),
            'ram_total': metrics.get('ram_total'),
            'tensorrt': _tensorrt_export_available(),
            'tensorrt_error': _tensorrt_dependency_error(),
        },
        'metrics': metrics,
        'version': 'v17',
        'ts': time.time(),
    }


def _hub_loop():
    if not HUB_URL:
        return
    registered = False
    last_url = None
    first_success = True
    while True:
        try:
            payload = _hub_payload()
            public_url = payload.get('url')
            if public_url:
                path = '/nodes/heartbeat' if registered and public_url == last_url else '/nodes/register'
                r = requests.post(HUB_URL + path, json=payload, headers=_hub_headers(), timeout=8)
                registered = r.ok
                if registered:
                    last_url = public_url
                if registered and (first_success or path == '/nodes/register'):
                    print(f"[Hub] ✓ URL sincronizada com sucesso: {payload.get('url')}")
                    first_success = False
        except Exception as ex:
            print(f"[Hub] Falha no heartbeat: {ex}")
        hub_event.wait(60)
        hub_event.clear()


def _auditoria_loop():
    """Fecha episodios que ja acabaram e apaga as evidencias vencidas."""
    ultima_limpeza = 0.0
    while True:
        time.sleep(15)
        try:
            auditoria.encerrar_vencidos()
        except Exception as e:
            print(f"[auditoria] erro ao fechar episodios: {e}")
        # a faxina das fotos roda uma vez por hora, para todos os usuarios
        if time.time() - ultima_limpeza < 3600:
            continue
        ultima_limpeza = time.time()
        n = user_mgr.limpar_sessoes_vencidas()
        if n:
            print(f"[sessoes] {n} sessao(oes) vencida(s) removida(s)")
        try:
            uids = [u['uid'] for u in db.consultar('SELECT uid FROM usuarios')]
        except Exception:
            continue
        for uid in uids:
            try:
                n = auditoria.limpar_antigas(DADOS_DIR, uid)
                if n:
                    print(f"[auditoria] {uid}: {n} evidencia(s) vencida(s) removida(s)")
            except Exception as e:
                print(f"[auditoria] limpeza de {uid} falhou: {e}")


def iniciar_banco():
    """Sobe o esquema antes de aceitar requisicao. O container do Postgres costuma
    ficar pronto depois do backend, entao esperamos ele responder."""
    db.esperar_banco()
    db.criar_esquema()


def start_auditoria_manutencao():
    threading.Thread(target=_auditoria_loop, daemon=True, name='auditoria-manutencao').start()


def start_hub_registration():
    global hub_thread_started
    if not HUB_URL:
        return False
    with hub_thread_lock:
        if hub_thread_started:
            return True
        hub_thread_started = True
        threading.Thread(target=_hub_loop, daemon=True, name='backend-hub-heartbeat').start()
        print(f"[Hub] Registro habilitado: {HUB_URL}")
        return True

# ═══════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════
CAMPOS_CADASTRO = {'nome': 'o nome completo', 'doc': 'o CPF ou CNPJ', 'email': 'o e-mail', 'senha': 'a senha'}


@app.route('/auth/register', methods=['POST'])
def register():
    if not CADASTRO_ABERTO:
        return jsonify({'error':'O cadastro está fechado neste servidor. Peça uma conta ao administrador.'}), 403
    d = request.get_json(force=True, silent=True) or {}
    # telefone e setor sao opcionais na tela de cadastro; exigi-los aqui quebrava o cadastro
    for f, nome in CAMPOS_CADASTRO.items():
        if not str(d.get(f) or '').strip(): return jsonify({'error':f'Preencha {nome}.'}), 400
    if d.get('confirmar_senha') and d['senha'] != d['confirmar_senha']:
        return jsonify({'error':'As duas senhas não são iguais.'}), 400
    ok, res = user_mgr.register(d['nome'],d['doc'],d.get('telefone','') or '',d['email'],
                                d.get('setor','') or '',d['senha'])
    if not ok: return jsonify({'error':res}), 409 if 'Já existe' in res else 400
    return jsonify({'message':'Cadastro realizado!','uid':res}), 201

@app.route('/auth/login', methods=['POST'])
def do_login():
    d = request.get_json(force=True, silent=True) or {}
    cred = str(d.get('credencial') or d.get('email') or d.get('doc') or '').strip()
    if not cred or not d.get('senha'):
        return jsonify({'error':'Preencha o CPF (ou e-mail) e a senha.'}), 400
    chave = cred.lower()
    espera = tentativas_login.bloqueado(chave)
    if espera:
        minutos = max(1, round(espera / 60))
        return jsonify({'error':f'Muitas tentativas com senha errada. Tente de novo em {minutos} min.'}), 429
    ok, token, user, motivo = user_mgr.login(cred, d.get('senha',''))
    if not ok:
        if motivo == 'bloqueado':
            return jsonify({'error':'Esta conta está bloqueada. Fale com o administrador.'}), 403
        tentativas_login.falhou(chave)
        return jsonify({'error':'CPF/e-mail ou senha incorretos.'}), 401
    tentativas_login.acertou(chave)
    return jsonify({'token':token,'user':user})

@app.route('/auth/logout', methods=['POST'])
def do_logout():
    t = _token(); user_mgr.logout(t) if t else None
    return jsonify({'message':'Logout realizado'})

@app.route('/auth/me', methods=['GET'])
def me():
    u, err = _auth()
    if err: return err
    return jsonify({'user':u})

# ═══════════════════════════════════════════════════════════════
# MODELOS disponíveis
# ═══════════════════════════════════════════════════════════════

@app.route('/models', methods=['GET'])
def list_models():
    """Lista modelos globais e, quando autenticado, também os modelos do próprio usuário."""
    user = None
    if _token():
        try:
            user, _ = _auth()
        except Exception:
            user = None
    models = []

    user_meta = _load_model_meta(user['id']) if user and '_cam_session' not in user else {}
    user_has_default = any(isinstance(rec, dict) and rec.get('default') for rec in user_meta.values())
    base_default = _base_default_model()

    def add_model(full: str, name: str, tipo: str, owner_uid=None, display_name=None, editable=False):
        info = _read_model_info(full)
        classes = info['classes']
        meta_owner = owner_uid if owner_uid else (user['id'] if user and '_cam_session' not in user else None)
        meta = _load_model_meta(meta_owner).get(name, {}) if meta_owner else {}
        required_items = meta.get('required_items') or _guess_required_items(classes)
        tensorrt = _tensorrt_status_for_path(full, meta_owner, name)
        models.append({
            'name': name,
            'display_name': display_name or name,
            'size_mb': round(os.path.getsize(full) / 1024 / 1024, 1),
            'tipo': tipo,
            'arquitetura': info['arquitetura'],
            'editable': editable,
            'configurable': bool(user and '_cam_session' not in user),
            'user_override': bool(user_meta.get(name)) if user and '_cam_session' not in user else False,
            'classes': classes,
            'required_items': required_items,
            'owner_uid': owner_uid,
            # sem escolha do usuario, o padrao e o mesmo de _default_model_for (modelo Argos mais recente)
            'is_default': bool(meta.get('default')) or (
                tipo == 'base' and not user_has_default and name == base_default),
            'tensorrt': tensorrt,
        })

    if os.path.isdir(MODELS_DIR):
        for f in sorted(os.listdir(MODELS_DIR)):
            if f.endswith('.pt') and not f.endswith('-pose.pt'):  # modelos de pose sao internos
                full = os.path.join(MODELS_DIR, f)
                add_model(full, f, 'base', display_name=_base_model_display_name(f))

    if user and '_cam_session' not in user:
        uid = user['id']
        epis_meta = {e['id']: e.get('nome', '') for e in _load_json(_epi_meta_path(uid))}
        for f in sorted(os.listdir(_user_models_dir(uid))):
            if not f.endswith('.pt'):
                continue
            full = os.path.join(_user_models_dir(uid), f)
            display_name = f
            if f.startswith('epi_') and f.endswith('.pt'):
                epi_id = f[4:-3]
                epi_nome = epis_meta.get(epi_id)
                if epi_nome:
                    display_name = f"EPI: {epi_nome} ({f})"
            elif f == 'funcionarios.pt':
                display_name = 'Funcionários (reconhecimento facial)'
            add_model(full, f, 'customizado', owner_uid=uid, display_name=display_name, editable=True)

    return jsonify({'models': models})


@app.route('/models/upload', methods=['POST'])
def upload_model():
    u, err = _auth()
    if err: return err
    if '_cam_session' in u: return jsonify({'error':'Câmeras não podem enviar modelos'}), 403
    if UPLOAD_MODELOS in ('desligado', 'off', '0') or (
            UPLOAD_MODELOS == 'admin' and (u.get('role') or 'user') != 'admin'):
        return jsonify({'error':'O envio de modelos está restrito ao administrador deste servidor.'}), 403
    if 'model' not in request.files:
        return jsonify({'error':'Escolha um arquivo .pt para enviar.'}), 400
    f = request.files['model']
    if not f or not (f.filename or '').lower().endswith('.pt'):
        return jsonify({'error':'O modelo precisa ser um arquivo .pt (YOLO).'}), 400
    safe_name = _safe_model_name(f.filename)
    dest = os.path.join(_user_models_dir(u['id']), safe_name)
    f.save(dest)
    classes = _read_model_classes(dest)
    required_items = request.form.get('required_items')
    if required_items:
        try:
            required_items = json.loads(required_items)
        except Exception:
            required_items = [x.strip() for x in required_items.split(',') if x.strip()]
    else:
        required_items = _guess_required_items(classes)
    _upsert_model_meta(u['id'], safe_name, classes=classes, required_items=required_items,
                       extra={'uploaded_at': time.time()})
    trt = {'started': False, 'status': 'skipped'}
    auto_trt = request.form.get('auto_tensorrt', '1').strip().lower() not in ('0', 'false', 'no', 'off')
    if auto_trt:
        trt = _export_tensorrt_async(u['id'], safe_name, dest, reason='upload')
    return jsonify({'message':'Modelo enviado com sucesso','name':safe_name,'classes':classes,
                    'required_items':required_items,'size_mb':round(os.path.getsize(dest)/1024/1024,1),
                    'tensorrt': trt})


@app.route('/models/<name>', methods=['DELETE'])
def delete_model(name):
    u, err = _auth()
    if err: return err
    if '_cam_session' in u: return jsonify({'error':'Câmeras não podem remover modelos'}), 403
    safe_name = os.path.basename(name)
    path = os.path.join(_user_models_dir(u['id']), safe_name)
    if not os.path.exists(path):
        return jsonify({'error':'Modelo não encontrado'}), 404
    os.remove(path)
    meta = _load_model_meta(u['id'])
    meta.pop(safe_name, None)
    _save_model_meta(u['id'], meta)
    return jsonify({'message':'Modelo removido'})


@app.route('/models/<name>/default', methods=['POST'])
def set_model_default(name):
    u, err = _auth()
    if err: return err
    if '_cam_session' in u: return jsonify({'error':'Câmeras não podem alterar modelos'}), 403
    safe_name = os.path.basename(name)
    user_path = os.path.join(_user_models_dir(u['id']), safe_name)
    base_path = os.path.join(MODELS_DIR, safe_name)
    if not os.path.exists(user_path) and not os.path.exists(base_path):
        return jsonify({'error':'Modelo não encontrado'}), 404
    meta = _load_model_meta(u['id'])
    for mk in list(meta.keys()):
        if isinstance(meta[mk], dict) and meta[mk].get('default'):
            meta[mk]['default'] = False
    rec = dict(meta.get(safe_name, {}))
    rec['default'] = True
    if 'required_items' not in rec:
        classes = rec.get('classes') or _read_model_classes(user_path if os.path.exists(user_path) else base_path)
        rec['classes'] = classes
        rec['required_items'] = _guess_required_items(classes)
    meta[safe_name] = rec
    _save_model_meta(u['id'], meta)
    return jsonify({'message':'Modelo padrão salvo','model':safe_name})

@app.route('/models/<name>/config', methods=['PUT'])
def update_model_config(name):
    u, err = _auth()
    if err: return err
    if '_cam_session' in u: return jsonify({'error':'Câmeras não podem alterar modelos'}), 403
    safe_name = os.path.basename(name)
    user_path = os.path.join(_user_models_dir(u['id']), safe_name)
    base_path = os.path.join(MODELS_DIR, safe_name)
    if not os.path.exists(user_path) and not os.path.exists(base_path):
        return jsonify({'error':'Modelo não encontrado'}), 404
    d = request.get_json(force=True, silent=True) or {}
    required_items = d.get('required_items')
    if required_items is None or not isinstance(required_items, list):
        return jsonify({'error':'required_items deve ser lista'}), 400
    classes = d.get('classes') or _read_model_classes(user_path if os.path.exists(user_path) else base_path)
    rec = _upsert_model_meta(u['id'], safe_name, classes=classes, required_items=required_items,
                             extra={'updated_at': time.time()})
    return jsonify({'message':'Configuração salva','model':safe_name,'config':rec})


@app.route('/models/<name>/tensorrt', methods=['GET'])
def get_model_tensorrt(name):
    u, err = _auth()
    if err: return err
    if '_cam_session' in u: return jsonify({'error':'Cameras nao podem consultar otimizacao'}), 403
    safe_name = os.path.basename(name)
    model_path = _resolve_model_path_for_user(u['id'], safe_name)
    if not os.path.exists(model_path):
        return jsonify({'error':'Modelo nao encontrado'}), 404
    return jsonify({'model': safe_name, 'tensorrt': _tensorrt_status_for_path(model_path, u['id'], safe_name)})


@app.route('/models/<name>/tensorrt', methods=['POST'])
@app.route('/models/<name>/convert_tensorrt', methods=['POST'])
def convert_model_tensorrt(name):
    u, err = _auth()
    if err: return err
    if '_cam_session' in u: return jsonify({'error':'Cameras nao podem converter modelos'}), 403
    safe_name = os.path.basename(name)
    model_path = _resolve_model_path_for_user(u['id'], safe_name)
    if not os.path.exists(model_path):
        return jsonify({'error':'Modelo nao encontrado'}), 404
    result = _export_tensorrt_async(u['id'], safe_name, model_path, reason='manual')
    status_code = 202 if result.get('started') or result.get('status') == 'running' else 400
    return jsonify({'model': safe_name, 'tensorrt': result}), status_code


@app.route('/hardware', methods=['GET'])
def hardware_status():
    _u, err = _auth()
    if err: return err
    metrics = _sys_metrics()
    return jsonify({
        'server_kind': _server_kind(),
        'device_default': _device_global,
        'tensorrt_available': _tensorrt_export_available(),
        'tensorrt_error': _tensorrt_dependency_error(),
        'tensorrt_enabled': os.environ.get('EPI_USE_TENSORRT', '1').strip().lower() not in ('0', 'false', 'no', 'off'),
        'availability': _availability_score(metrics),
        'metrics': metrics,
        'hub_url': HUB_URL or None,
        'public_backend_url': _public_backend_url(),
    })


# ═══════════════════════════════════════════════════════════════
# STREAMS
# ═══════════════════════════════════════════════════════════════
@app.route('/frame', methods=['GET'])
def get_frame():
    u, err = _auth()
    if err: return err
    sid = request.args.get('stream', f"{u['id']}_0")
    if not _stream_permitido(u, sid): return _stream_negado()
    p = _stream(sid, u['id'])
    if not p: return ('', 204)
    fb = p.get_frame_b64()
    if not fb: return ('', 204)
    st = p.get_status(); st["paused"] = _pausado(u['id'])
    return jsonify({"frame":fb,"status":st})

# ── Configuracao por stream (modo, fps, resolucao), escolhida no painel ──
stream_configs: dict = {}   # sid -> config
stream_configs_lock = threading.Lock()


def _owner_uid(u):
    if isinstance(u, dict) and '_cam_session' in u:
        return u['_cam_session'].get('uid')
    return u.get('id') if isinstance(u, dict) else None


def _cameras_config_path_legado(uid):
    return os.path.join(DADOS_DIR, uid, 'cameras.json')


def _stream_config(owner_uid, sid):
    with stream_configs_lock:
        cfg = stream_configs.get(sid)
    if cfg is None and owner_uid:
        try:
            linha = db.consultar_um('SELECT config FROM cameras WHERE uid=%s AND stream_id=%s',
                                    (owner_uid, sid))
            cfg = linha['config'] if linha else None
            if isinstance(cfg, str):
                cfg = json.loads(cfg or '{}')
        except Exception:
            cfg = None
    cfg = normalizar_config(cfg)
    with stream_configs_lock:
        stream_configs[sid] = cfg
    return cfg


def _save_stream_config(owner_uid, sid, cfg):
    with stream_configs_lock:
        stream_configs[sid] = cfg
    if not owner_uid:
        return
    try:
        db.executar('INSERT INTO cameras (uid, stream_id, config) VALUES (%s,%s,%s::jsonb)'
                    ' ON CONFLICT (uid, stream_id) DO UPDATE SET config=EXCLUDED.config',
                    (owner_uid, sid, json.dumps(cfg or {})))
    except Exception as e:
        print(f'[db] nao foi possivel gravar a config de {sid}: {e}')


def _owns_stream(uid, sid):
    with user_streams_lock:
        return sid in user_streams.get(uid, [])


def _ensure_external_stream_ready(u, sid: str):
    owner_uid = _owner_uid(u)
    if not owner_uid:
        return None
    with removed_streams_lock:
        if sid in removed_streams:
            return None
    with streams_lock:
        p = streams.get(sid)
    if p and p.running and p.use_external:
        return p
    mp, req = _default_model_for(owner_uid)
    if p and p.model_path and os.path.exists(p.model_path):  # mantem o modelo escolhido no painel
        mp, req = p.model_path, p.required_items or req
    p = _stream(sid, owner_uid)
    if p:
        p.update_settings(mp, 'auto', camera_source='webcam', required_items=req,
                          config=_stream_config(owner_uid, sid))
        _load_employee_model_for_stream(owner_uid, p)
    return p


@app.route('/stream_frame', methods=['POST'])
@app.route('/stream_frame2', methods=['POST'], endpoint='recv_frame2')
def recv_frame():
    """Rotas antigas (JPEG em base64 dentro de JSON). Sem horario de captura."""
    u, err = _auth()
    if err: return err
    if _pausado(_owner_uid(u)): return jsonify({"success":True})
    d = request.get_json(force=True)
    fb = d.get('frame')
    sid = d.get('stream_id', f"{u['id']}_0")
    if not _stream_permitido(u, sid): return _stream_negado()
    if not fb: return jsonify({"error":"Frame não fornecido"}), 400
    try:
        raw = fb.split(',')[1] if ',' in fb else fb
        img = _decode_jpeg(base64.b64decode(raw))
        if img is None: return jsonify({"error":"Frame inválido"}), 400
        p = _ensure_external_stream_ready(u, sid)
        if not p: return jsonify({"error":"Stream removido"}), 410
        p.set_external_frame(img)
        return jsonify({"success":True,"stream_id":sid})
    except Exception as e:
        return jsonify({"error":str(e)}), 500


def _decode_jpeg(data: bytes):
    if not data:
        return None
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def _result_payload(p, sid):
    r = p.get_result() or {}
    return {'type': 'resultado', 'stream_id': sid, **{k: v for k, v in r.items() if k != 'ts'},
            'required_items': list(p.required_items), 'required_labels': [item_label(i) for i in p.required_items],
            'classes': list(p.model_classes), 'model': p.model_name,
            'paused': _pausado(getattr(p, 'owner_uid', None))}


def _stream_info(p, sid):
    """Dados do stream que mudam pouco (vao em 'pronto' e 'estado', nao em todo quadro)."""
    st = p.get_status()
    return {'stream_id': sid, 'config': p.public_config(), 'model': p.model_name, 'arquitetura': st.get('arquitetura'),
            'required_items': st['required_items'], 'required_labels': st['required_labels'],
            'classes': st['classes'], 'pose': st['pose'], 'pipeline': st['pipeline'],
            'runtime_backend': st['runtime_backend'], 'camera': st['camera'],
            'paused': _pausado(getattr(p, 'owner_uid', None))}


@app.route('/stream_frame_bin', methods=['POST'])
def recv_frame_bin():
    """Envio por HTTP quando o WebSocket nao conecta. Corpo: JPEG cru.
    Cabecalhos: X-Stream-Id, X-Frame-Seq, X-Frame-T (ms de captura) e X-Sessao.
    Resposta: maior seq recebido, config atual e os resultados exibidos desde a ultima chamada."""
    u, err = _auth()
    if err: return err
    sid = request.headers.get('X-Stream-Id') or request.args.get('stream_id') or f"{u['id']}_0"
    if not _stream_permitido(u, sid): return _stream_negado()
    p = _ensure_external_stream_ready(u, sid)
    if not p: return jsonify({"error":"Stream removido"}), 410
    session = (request.headers.get('X-Sessao') or 'http')[:64]
    t_ms = request.headers.get('X-Frame-T', type=float)
    seq = request.headers.get('X-Frame-Seq', type=int)
    body = request.get_data(cache=False)
    if body and not _pausado(_owner_uid(u)):
        p.receive_jpeg(seq, t_ms / 1000.0 if t_ms is not None else None, body, session if t_ms is not None else None)
    results = [item[4] for item in p.pipeline.assinante_http(session).pegar(timeout=0)]
    return jsonify({**_result_payload(p, sid), 'type': 'lote', 'ack': p.pipeline.ultimo_seq(session),
                    'config': p.public_config(), 'janela_s': p.pipeline.janela_reenvio(), 'resultados': results})


class _WsSender:
    """ws.send usado por varias threads, com trava."""

    def __init__(self, ws):
        self.ws = ws
        self.lock = threading.Lock()

    def json(self, payload):
        self.text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))

    def text(self, text):
        with self.lock:
            self.ws.send(text)

    def binary(self, data):
        with self.lock:
            self.ws.send(data)


if sock:
    @sock.route('/ws/stream')
    def ws_stream(ws):
        """Camera externa (?token=...&stream_id=...&sessao=...).
        Cliente -> servidor: quadros binarios (live_pipeline.pack_frame); texto {"type":"ping","t":...}
        ou {"type":"parar"}.
        Servidor -> cliente: 'pronto' (config, ultimo seq recebido desta sessao, janela de reenvio),
        'quadro' (resultado de cada quadro exibido), 'ack' (maior seq recebido), 'config' (mudou no
        painel), 'pong', 'removido', 'erro'."""
        out = _WsSender(ws)
        u, err = _auth()
        if err:
            out.json({'type': 'erro', 'error': 'Sessão inválida'})
            return
        sid = request.args.get('stream_id') or f"{u['id']}_0"
        if not _stream_permitido(u, sid):
            out.json({'type': 'erro', 'error': 'Esta câmera pertence a outra conta.'})
            return
        session = (request.args.get('sessao') or uuid.uuid4().hex)[:64]
        p = _ensure_external_stream_ready(u, sid)
        if not p:
            out.json({'type': 'removido'})
            return
        stop = threading.Event()
        ultimo_res = request.args.get('ultimo_res', type=int)
        state = {'p': p, 'sub': p.pipeline.assinar_retomando(session, ultimo_res), 'cfg': p.config_version}

        def writer():
            last_ack = 0.0
            while not stop.is_set():
                cur, sub = state['p'], state['sub']
                items = sub.pegar(timeout=0.25)
                try:
                    for item in items:
                        out.text(item[2])
                    now = time.monotonic()
                    if now - last_ack >= 0.4:
                        last_ack = now
                        st = cur.pipeline.estatisticas()
                        out.json({'type': 'ack', 'seq': cur.pipeline.ultimo_seq(session),
                                  'atraso_ms': st['atraso_ms'], 'buffer': st['buffer'],
                                  'fps_analise': st['fps_analise'], 'fps_exibido': st['fps_exibido']})
                    if cur.config_version != state['cfg']:
                        state['cfg'] = cur.config_version
                        out.json({'type': 'config', 'config': cur.public_config(),
                                  'janela_s': cur.pipeline.janela_reenvio()})
                    if sub.fechado and sub is state['sub']:
                        with removed_streams_lock:
                            removed = sid in removed_streams
                        if removed:
                            out.json({'type': 'removido'})
                            stop.set()
                except Exception:
                    stop.set()

        out.json({'type': 'pronto', **_stream_info(p, sid), 'sessao': session,
                  'ultimo_seq': p.pipeline.ultimo_seq(session), 'janela_s': p.pipeline.janela_reenvio()})
        threading.Thread(target=writer, daemon=True, name=f"ws-{sid}").start()
        try:
            while not stop.is_set():
                msg = ws.receive(timeout=12)
                if msg is None:
                    break  # 12 s sem nada (nem ping): conexao morta
                if isinstance(msg, (bytes, bytearray)):
                    frame = unpack_frame(msg)
                    if frame is None or _pausado(_owner_uid(u)):
                        continue
                    cur = state['p']
                    if not cur.running or not cur.use_external:
                        cur = _ensure_external_stream_ready(u, sid)
                        if not cur:
                            out.json({'type': 'removido'})
                            break
                        if cur is not state['p'] or state['sub'].fechado:
                            old_p, old_sub = state['p'], state['sub']
                            state['p'], state['sub'] = cur, cur.pipeline.assinar(video=False, maxlen=600)
                            old_p.pipeline.cancelar(old_sub)
                    seq, t, jpeg = frame
                    cur.receive_jpeg(seq, t, jpeg, session if t is not None else None)
                    continue
                d = json.loads(msg) or {}
                if d.get('type') == 'ping':
                    out.json({'type': 'pong', 't': d.get('t')})
                elif d.get('type') == 'parar':
                    break
        except Exception:
            pass  # conexao fechada pelo cliente
        finally:
            stop.set()
            state['p'].pipeline.cancelar(state['sub'])

    @sock.route('/ws/view')
    def ws_view(ws):
        """Painel assistindo um stream (?token=...&stream_id=...&video=1).
        Servidor -> painel: 'pronto'/'estado' (info do stream, 1 por segundo), 'quadro' (resultado)
        seguido do quadro binario com o mesmo seq, 'aguardando' (stream ainda nao existe), 'removido'.
        O painel manda {"type":"ping"}; sem nada por 20 s a conexao e fechada."""
        out = _WsSender(ws)
        u, err = _auth()
        if err or '_cam_session' in u:
            out.json({'type': 'erro', 'error': 'Sessão inválida'})
            return
        sid = request.args.get('stream_id') or ''
        want_video = request.args.get('video', '1') != '0'
        stop = threading.Event()

        def reader():
            try:
                while not stop.is_set():
                    msg = ws.receive(timeout=20)
                    if msg is None:
                        break
                    if isinstance(msg, str) and (json.loads(msg) or {}).get('type') == 'ping':
                        out.json({'type': 'pong'})
            except Exception:
                pass
            stop.set()

        threading.Thread(target=reader, daemon=True, name=f"view-rx-{sid}").start()
        p = sub = None
        last_info = 0.0
        try:
            while not stop.is_set():
                with streams_lock:
                    atual = streams.get(sid)
                if atual is not None and not _owns_stream(u['id'], sid):
                    atual = None  # ainda nao e (ou nunca sera) deste usuario: fica aguardando
                with removed_streams_lock:
                    removed = sid in removed_streams
                if removed:
                    out.json({'type': 'removido'})
                    break
                if atual is not p:
                    if p is not None:
                        p.pipeline.cancelar(sub)
                    p, sub = atual, (atual.pipeline.assinar(video=want_video, maxlen=240) if atual else None)
                    out.json({'type': 'aguardando', 'stream_id': sid} if p is None
                             else {'type': 'pronto', **_stream_info(p, sid)})
                if p is None:
                    stop.wait(1.0)
                    continue
                items = sub.pegar(timeout=0.25)
                if len(items) > 45:  # painel lento: pula para o presente
                    items = items[-8:]
                for seq, t, texto, jpeg, _ in items:
                    out.text(texto)
                    if want_video and jpeg:
                        out.binary(pack_frame(seq or 0, t * 1000.0, jpeg))
                now = time.monotonic()
                if now - last_info >= 1.0:
                    last_info = now
                    out.json({'type': 'estado', **_stream_info(p, sid)})
        except Exception as ex:
            if 'ConnectionClosed' not in type(ex).__name__:
                print(f"[ws/view {sid}] {type(ex).__name__}: {ex}")
        finally:
            stop.set()
            if p is not None:
                p.pipeline.cancelar(sub)


@app.route('/streams/<sid>/config', methods=['GET', 'POST'])
def stream_config(sid):
    """Modo de análise, fps e resolução de um stream. Só o painel (usuário) altera."""
    u, err = _auth()
    if err: return err
    if not _stream_permitido(u, sid): return _stream_negado()
    owner = _owner_uid(u)
    cfg = _stream_config(owner, sid)
    if request.method == 'POST':
        if '_cam_session' in u:
            return jsonify({'error': 'O modo é escolhido no painel de controle'}), 403
        cfg = normalizar_config(request.get_json(force=True, silent=True) or {}, cfg)
        _save_stream_config(owner, sid, cfg)
    with streams_lock:
        p = streams.get(sid)
    if p is not None and (_owns_stream(owner, sid) or '_cam_session' in u):
        pub = p.set_config(cfg) if request.method == 'POST' else p.public_config()
    else:
        pub = config_publica(cfg)
    return jsonify({'config': pub,
                    'modos': {k: {'nome': v['nome'], 'atraso_s': v['atraso']} for k, v in MODOS.items()},
                    'fps': list(FPS_OPCOES), 'resolucoes': list(RES_OPCOES)})


@app.route('/streams', methods=['GET'])
def list_streams():
    u, err = _auth()
    if err: return err
    if '_cam_session' in u: return jsonify({"error":"Câmeras não podem listar"}), 403
    with user_streams_lock: sids = list(user_streams.get(u['id'],[]))
    result = []
    # FIX: não retornar streams removidos
    with removed_streams_lock:
        removed = set(removed_streams)
    for sid in sids:
        if sid in removed: continue
        with streams_lock: p = streams.get(sid)
        if p: result.append({"stream_id":sid,"status":p.get_status()})
    return jsonify({"streams":result})


@app.route('/streams/notify', methods=['POST'])
def notify_stream():
    """Chamado pelo cam.html quando inicia transmissão.
    Registra o stream na lista do dono do token para aparecer no painel."""
    u, err = _auth()
    if err: return err
    d = request.get_json(force=True)
    sid = d.get('stream_id')
    if not sid: return jsonify({"error":"stream_id obrigatório"}), 400
    # antes qualquer conta "adotava" o stream de outra por aqui e depois assistia pelo /ws/view
    if not _stream_permitido(u, sid): return _stream_negado()
    # Garante que está na lista do usuário dono da câmera
    uid = u.get('_cam_session', {}).get('uid') if '_cam_session' in u else u['id']
    with user_streams_lock:
        user_streams.setdefault(uid, [])
        if sid not in user_streams[uid]:
            user_streams[uid].append(sid)
    # Remove dos removidos (é uma nova conexão intencional)
    with removed_streams_lock:
        removed_streams.discard(sid)
    return jsonify({"ok": True, "stream_id": sid, "owner_uid": uid})

@app.route('/streams/add', methods=['POST'])
def add_stream():
    u, err = _auth()
    if err: return err
    d = request.get_json(force=True)
    model_name = d.get('model','yolo26n.pt')
    device = d.get('device','auto')
    camera = d.get('camera','webcam')
    sid = d.get('stream_id')
    if not sid:
        sid = f"{u['id']}_{d.get('index', 0)}"
    if not _stream_permitido(u, sid): return _stream_negado()
    if not seguranca.fonte_camera_valida(camera):
        return jsonify({"error":"Endereço de câmera inválido. Use rtsp://..., http://... ou a câmera do aparelho."}), 400
    # FIX: limpar da lista de removidos se estiver sendo re-adicionado intencionalmente
    with removed_streams_lock:
        removed_streams.discard(sid)
    required = d.get('required_items')
    if d.get('model'):
        mp = _resolve_model_path_for_user(u.get('id') if isinstance(u, dict) else None, model_name)
    else:  # cam.html nao escolhe modelo: usa o padrao do dono da camera
        mp, required = _default_model_for(u['id'])
    if not os.path.exists(mp): return jsonify({"error":f"Modelo não encontrado: {model_name}"}), 404
    owner_uid = _owner_uid(u)
    if '_cam_session' in u:
        with streams_lock:
            atual = streams.get(sid)
        if atual and atual.running and atual.use_external:  # camera reconectando: nao reinicia a IA
            return jsonify({"message":"Stream ativo","stream_id":sid,"config":atual.public_config()})
    p = _stream(sid, owner_uid)
    if p:
        p.update_settings(mp, device, camera_source=camera, required_items=required,
                          config=_stream_config(owner_uid, sid))
        _load_employee_model_for_stream(owner_uid, p)
    return jsonify({"message":"Stream adicionado","stream_id":sid,
                    "config":p.public_config() if p else config_publica(_stream_config(owner_uid, sid))})

@app.route('/streams/remove', methods=['POST'])
def remove_stream():
    u, err = _auth()
    if err: return err
    d = request.get_json(force=True)
    sid = d.get('stream_id', f"{u['id']}_0")
    if not _stream_permitido(u, sid): return _stream_negado()
    # FIX remover câmera: marcar como removido ANTES de parar o processor
    with removed_streams_lock:
        removed_streams.add(sid)
    with streams_lock:
        p = streams.pop(sid, None)
    if p: p.close()
    with stream_configs_lock:
        stream_configs.pop(sid, None)
    with user_streams_lock:
        lst = user_streams.get(u['id'],[])
        if sid in lst: lst.remove(sid)
    return jsonify({"message":"Stream removido"})

@app.route('/streams/stop_external', methods=['POST'])
def stop_external_stream():
    """Para o envio de frames externos (câmera do navegador), sem remover o stream.
    Usado pelo botão 'Desligar câmera' em /cam."""
    u, err = _auth()
    if err: return err
    d = request.get_json(force=True)
    sid = d.get('stream_id')
    if not sid: return jsonify({"error":"stream_id obrigatório"}), 400
    if not _stream_permitido(u, sid): return _stream_negado()
    with streams_lock:
        p = streams.get(sid)
    if p:
        p.stop_external()
    return jsonify({"message":"Câmera parada"})

@app.route('/update', methods=['POST'])
def update_engine():
    """Atualiza configurações de um stream (modelo, câmera, device).
    FIX: usa stream_id diretamente, não index."""
    u, err = _auth()
    if err: return err
    d = request.get_json(force=True)
    model_name = d.get('model','yolo26n.pt')
    device = d.get('device','auto')
    camera = d.get('camera','webcam')
    # FIX alterar modelo: prioriza stream_id enviado, fallback para index
    sid = d.get('stream_id')
    if not sid:
        sid = f"{u['id']}_{d.get('index', 0)}"
    if not _stream_permitido(u, sid): return _stream_negado()
    if not seguranca.fonte_camera_valida(camera):
        return jsonify({"error":"Endereço de câmera inválido. Use rtsp://..., http://... ou a câmera do aparelho."}), 400
    # FIX: não atualizar streams removidos
    with removed_streams_lock:
        if sid in removed_streams:
            return jsonify({"error":"Stream foi removido"}), 410
    mp = _resolve_model_path_for_user(u.get('id') if isinstance(u, dict) else None, model_name)
    if not os.path.exists(mp): return jsonify({"error":"Modelo não encontrado"}), 404
    owner_uid = _owner_uid(u)
    cfg = _stream_config(owner_uid, sid)
    if '_cam_session' not in u and any(k in d for k in ('modo', 'fps', 'resolucao')):
        cfg = normalizar_config(d, cfg)
        _save_stream_config(owner_uid, sid, cfg)
    p = _stream(sid, owner_uid)
    if p:
        p.update_settings(mp, device, camera_source=camera, required_items=d.get('required_items'), config=cfg)
        _load_employee_model_for_stream(owner_uid, p)
    return jsonify({"message":"OK","stream_id":sid,"config":p.public_config() if p else config_publica(cfg)})

@app.route('/pause', methods=['POST'])
def toggle_pause():
    u, err = _auth()
    if err: return err
    d = request.get_json(force=True, silent=True) or {}
    novo = _definir_pausa(_owner_uid(u), d.get("paused"))
    return jsonify({"success":True,"paused":novo})

@app.route('/upload_video', methods=['POST'])
def upload_video():
    u, err = _auth()
    if err: return err
    if 'video' not in request.files: return jsonify({"error":"Nenhum arquivo"}), 400
    vf = request.files['video']
    model_name = request.form.get('model','yolo26n.pt')
    device = request.form.get('device','auto')
    sid = request.form.get('stream_id')
    if not sid:
        sid = f"{u['id']}_{request.form.get('index', 0)}"
    if not _stream_permitido(u, sid): return _stream_negado()
    # confere o modelo antes de gravar: senao o video ficava perdido no disco
    mp = _resolve_model_path_for_user(u.get('id') if isinstance(u, dict) else None, model_name)
    if not os.path.exists(mp): return jsonify({"error":"Modelo não encontrado"}), 404
    # O nome que o navegador manda NAO vira caminho: "../../backend/app.py" gravava
    # por cima do codigo do servidor. Cada conta tem a sua pasta e o nome e sorteado.
    upload_dir = os.path.join(BASE_DIR, 'uploads', _owner_uid(u))
    os.makedirs(upload_dir, exist_ok=True)
    ext = seguranca.extensao_permitida(vf.filename, seguranca.EXTENSOES_VIDEO, '.mp4')
    vpath = os.path.join(upload_dir, uuid.uuid4().hex[:12] + ext)
    vf.save(vpath)
    p = _stream(sid, u['id'])
    if p:
        p.update_settings(mp, device, camera_source=vpath, required_items=request.form.getlist('required_items') or None,
                          config=_stream_config(u['id'], sid))
        _load_employee_model_for_stream(u['id'], p)
    return jsonify({"message":"Vídeo carregado","stream_id":sid})

@app.route('/video_feed', methods=['GET'])
def video_feed():
    u, err = _auth()
    if err: return err
    sid = request.args.get('stream', f"{u['id']}_0")
    if not _stream_permitido(u, sid): return _stream_negado()
    p = _stream(sid, u['id'])
    if not p: return jsonify({"error":"Stream não encontrado"}), 404
    return Response(stream_with_context(p.generate_frames()), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/cameras', methods=['GET'])
def list_cameras():
    _u, err = _auth()
    if err: return err
    cams = list_local_webcams(limit=10)
    return jsonify({"local":cams})

def _scan_request_payload():
    d = request.get_json(force=True, silent=True) or {}
    subnet = d.get('subnet') or request.args.get('subnet') or None
    username = d.get('username') or request.args.get('username') or ''
    password = d.get('password') or request.args.get('password') or ''
    timeout = d.get('timeout') or request.args.get('timeout') or 0.35
    include_onvif = str(d.get('include_onvif', request.args.get('include_onvif', '1'))).lower() not in ('0','false','no','off')
    return {
        'subnet': subnet,
        'username': username,
        'password': password,
        'timeout': timeout,
        'include_onvif': include_onvif,
    }

def _public_scan_job(job):
    data = dict(job or {})
    data.pop('password', None)
    return data

def _prune_scan_jobs():
    cutoff = time.time() - 1800
    with camera_scan_jobs_lock:
        for jid, job in list(camera_scan_jobs.items()):
            if float(job.get('finished_at') or job.get('started_at') or 0) < cutoff:
                camera_scan_jobs.pop(jid, None)

@app.route('/cameras/scan', methods=['GET','POST'])
def scan_network_cameras():
    u, err = _auth()
    if err: return err
    if '_cam_session' in u:
        return jsonify({"error":"Cameras remotas nao podem varrer a rede"}), 403
    payload = _scan_request_payload()
    result = discover_cameras(**payload)
    return jsonify(result)

@app.route('/cameras/scan/start', methods=['POST'])
def start_network_camera_scan():
    u, err = _auth()
    if err: return err
    if '_cam_session' in u:
        return jsonify({"error":"Cameras remotas nao podem varrer a rede"}), 403
    _prune_scan_jobs()
    payload = _scan_request_payload()
    job_id = uuid.uuid4().hex[:12]
    job = {
        'id': job_id,
        'owner_uid': u['id'],
        'status': 'running',
        'message': 'Analisando rede em segundo plano',
        'started_at': time.time(),
        'finished_at': None,
        'result': None,
        'error': None,
        **payload,
    }
    with camera_scan_jobs_lock:
        camera_scan_jobs[job_id] = job

    def _job():
        try:
            result = discover_cameras(**payload)
            with camera_scan_jobs_lock:
                current = camera_scan_jobs.get(job_id)
                if current:
                    current.update({
                        'status': 'done',
                        'message': 'Analise concluida',
                        'finished_at': time.time(),
                        'result': result,
                    })
        except Exception as ex:
            with camera_scan_jobs_lock:
                current = camera_scan_jobs.get(job_id)
                if current:
                    current.update({
                        'status': 'error',
                        'message': 'Falha ao analisar a rede',
                        'finished_at': time.time(),
                        'error': str(ex),
                    })

    threading.Thread(target=_job, daemon=True, name=f"camera-scan-{job_id}").start()
    return jsonify({'job': _public_scan_job(job), 'job_id': job_id}), 202

@app.route('/cameras/scan/jobs/<job_id>', methods=['GET'])
def get_network_camera_scan(job_id):
    u, err = _auth()
    if err: return err
    with camera_scan_jobs_lock:
        job = camera_scan_jobs.get(job_id)
    if not job:
        return jsonify({'error':'Analise nao encontrada'}), 404
    if job.get('owner_uid') != u['id']:
        return jsonify({'error':'Analise fora da conta autenticada'}), 403
    return jsonify({'job': _public_scan_job(job)})

# ═══════════════════════════════════════════════════════════════
# EPIs — Cadastro com fotos
# ═══════════════════════════════════════════════════════════════
@app.route('/epis', methods=['GET'])
def list_epis():
    u, err = _auth()
    if err: return err
    epis = _load_json(_epi_meta_path(u['id']))
    return jsonify({"epis": epis})

@app.route('/epis', methods=['POST'])
def create_epi():
    u, err = _auth()
    if err: return err
    d = request.get_json(force=True)
    nome = d.get('nome','').strip()
    descricao = d.get('descricao','').strip()
    cor = d.get('cor','#6366f1')
    if not nome: return jsonify({"error":"Nome do EPI é obrigatório"}), 400

    import uuid as _uuid
    epi_id = str(_uuid.uuid4())
    epi = {
        "id": epi_id,
        "nome": nome,
        "descricao": descricao,
        "cor": cor,
        "fotos": [],
        "modelo_treinado": False,
        "modelo_path": None,
        "criado_em": time.time()
    }
    # Criar pasta para as fotos
    epi_dir = os.path.join(_user_epi_dir(u['id']), epi_id)
    os.makedirs(epi_dir, exist_ok=True)

    epis = _load_json(_epi_meta_path(u['id']))
    epis.append(epi)
    _save_json(_epi_meta_path(u['id']), epis)
    return jsonify({"epi": epi}), 201

@app.route('/epis/<epi_id>', methods=['PUT'])
def update_epi(epi_id):
    u, err = _auth()
    if err: return err
    d = request.get_json(force=True)
    epis = _load_json(_epi_meta_path(u['id']))
    for epi in epis:
        if epi['id'] == epi_id:
            epi['nome'] = d.get('nome', epi['nome'])
            epi['descricao'] = d.get('descricao', epi['descricao'])
            epi['cor'] = d.get('cor', epi['cor'])
            _save_json(_epi_meta_path(u['id']), epis)
            return jsonify({"epi": epi})
    return jsonify({"error":"EPI não encontrado"}), 404

def _pasta_de_item(base_dir, item_id):
    """Pasta de um EPI ou funcionario. None se o id vier com '..', '/' e afins:
    DELETE /funcionarios/.. apagava a pasta de dados inteira da conta."""
    if not seguranca.id_valido(item_id):
        return None
    return os.path.join(base_dir, item_id)


@app.route('/epis/<epi_id>', methods=['DELETE'])
def delete_epi(epi_id):
    u, err = _auth()
    if err: return err
    epi_dir = _pasta_de_item(_user_epi_dir(u['id']), epi_id)
    if not epi_dir: return jsonify({"error":"EPI não encontrado"}), 404
    epis = _load_json(_epi_meta_path(u['id']))
    epis = [e for e in epis if e['id'] != epi_id]
    _save_json(_epi_meta_path(u['id']), epis)
    # Remover pasta de fotos
    if os.path.exists(epi_dir):
        shutil.rmtree(epi_dir)
    return jsonify({"message":"EPI removido"})

@app.route('/epis/<epi_id>/fotos', methods=['POST'])
def upload_epi_foto(epi_id):
    """Upload de uma foto do EPI (multipart/form-data, campo 'foto')"""
    u, err = _auth()
    if err: return err
    if 'foto' not in request.files: return jsonify({"error":"Escolha uma foto para enviar."}), 400
    epi_dir = _pasta_de_item(_user_epi_dir(u['id']), epi_id)
    epis = _load_json(_epi_meta_path(u['id']))
    if not epi_dir or not any(e['id'] == epi_id for e in epis):
        return jsonify({"error":"EPI não encontrado"}), 404

    foto_file = request.files['foto']
    os.makedirs(epi_dir, exist_ok=True)

    import uuid as _uuid
    foto_id = str(_uuid.uuid4())[:8]
    # so extensao de imagem: um .html enviado como "foto" seria servido como pagina
    ext = seguranca.extensao_permitida(foto_file.filename, seguranca.EXTENSOES_FOTO, '.jpg')
    foto_path = os.path.join(epi_dir, f"{foto_id}{ext}")
    foto_file.save(foto_path)

    for epi in epis:
        if epi['id'] == epi_id:
            epi.setdefault('fotos', [])
            epi['fotos'].append({"id": foto_id, "path": foto_path, "ext": ext})
            epi['modelo_treinado'] = False  # modelo precisa ser re-treinado
            _save_json(_epi_meta_path(u['id']), epis)
            return jsonify({"foto_id": foto_id, "total": len(epi['fotos'])})
    return jsonify({"error":"EPI não encontrado"}), 404

@app.route('/epis/<epi_id>/fotos/<foto_id>', methods=['DELETE'])
def delete_epi_foto(epi_id, foto_id):
    u, err = _auth()
    if err: return err
    epis = _load_json(_epi_meta_path(u['id']))
    for epi in epis:
        if epi['id'] == epi_id:
            fotos_antes = epi.get('fotos', [])
            foto_rem = next((f for f in fotos_antes if f['id'] == foto_id), None)
            if foto_rem and os.path.exists(foto_rem['path']):
                os.remove(foto_rem['path'])
            epi['fotos'] = [f for f in fotos_antes if f['id'] != foto_id]
            epi['modelo_treinado'] = False
            _save_json(_epi_meta_path(u['id']), epis)
            return jsonify({"message":"Foto removida"})
    return jsonify({"error":"EPI não encontrado"}), 404

@app.route('/epis/<epi_id>/treinar', methods=['POST'])
def train_epi_model(epi_id):
    """
    Inicia treinamento YOLO para este EPI em thread background.
    Retorna imediatamente com status 'treinando'.
    As fotos enviadas são usadas como dataset de treino.
    Requer: Ultralytics YOLO + pelo menos 10 fotos.
    """
    u, err = _auth()
    if err: return err
    epis = _load_json(_epi_meta_path(u['id']))
    epi = next((e for e in epis if e['id'] == epi_id), None)
    if not epi: return jsonify({"error":"EPI não encontrado"}), 404

    fotos = epi.get('fotos', [])
    if len(fotos) < 5:
        return jsonify({"error":f"Envie pelo menos 5 fotos (você tem {len(fotos)})"}), 400

    def _train():
        try:
            import uuid as _uuid
            from ultralytics import YOLO as _YOLO

            uid = u['id']
            run_id = str(_uuid.uuid4())[:8]
            models_dir = _user_models_dir(uid)
            epi_dir = os.path.join(_user_epi_dir(uid), epi_id)

            # Montar dataset YOLO no formato correto
            dataset_dir = os.path.join(epi_dir, 'dataset')
            train_img_dir = os.path.join(dataset_dir, 'images', 'train')
            train_lbl_dir = os.path.join(dataset_dir, 'labels', 'train')
            os.makedirs(train_img_dir, exist_ok=True)
            os.makedirs(train_lbl_dir, exist_ok=True)

            # Copiar fotos e gerar labels (bbox de tela cheia = objeto inteiro na foto)
            valid_count = 0
            for foto in fotos:
                if not os.path.exists(foto['path']): continue
                img = cv2.imread(foto['path'])
                if img is None: continue
                h, w = img.shape[:2]
                dest_img = os.path.join(train_img_dir, f"{foto['id']}.jpg")
                cv2.imwrite(dest_img, img, [cv2.IMWRITE_JPEG_QUALITY, 90])
                # Label: usa anotação real se existir, senão bbox de tela cheia
                dest_lbl = os.path.join(train_lbl_dir, f"{foto['id']}.txt")
                # Verifica se existe label anotado salvo junto à foto
                label_anotado = os.path.join(epi_dir, f"{foto['id']}.txt")
                labels_dict = epi.get('labels_anotadas', {})
                label_path_salvo = labels_dict.get(foto['id'])
                if label_path_salvo and os.path.exists(label_path_salvo):
                    # Usa label anotado (Roboflow/LabelImg) — muito mais preciso
                    import shutil as _sh2
                    _sh2.copy2(label_path_salvo, dest_lbl)
                elif os.path.exists(label_anotado):
                    import shutil as _sh2
                    _sh2.copy2(label_anotado, dest_lbl)
                else:
                    # Fallback: bbox de tela cheia (menos preciso, mas funciona)
                    with open(dest_lbl, 'w') as lf:
                        lf.write("0 0.5 0.5 1.0 1.0\n")
                valid_count += 1

            if valid_count < 5:
                print(f"[Train:{epi_id}] Fotos válidas insuficientes: {valid_count}")
                return

            # Arquivo de configuração do dataset
            yaml_path = os.path.join(dataset_dir, 'data.yaml')
            nome_classe = epi['nome'].replace(' ', '_').lower()
            with open(yaml_path, 'w') as yf:
                yf.write(f"path: {dataset_dir}\n")
                yf.write("train: images/train\n")
                yf.write("val: images/train\n")  # sem val separado pois dataset é pequeno
                yf.write("nc: 1\n")
                yf.write(f"names: ['{nome_classe}']\n")

            # Treinar usando yolo26n como base (mais rápido)
            base_model = _get_model_path('yolo26n.pt')
            model = _YOLO(base_model)
            model.train(
                data=yaml_path,
                epochs=30,
                imgsz=416,
                batch=4,
                project=models_dir,
                name=f"epi_{epi_id}_{run_id}",
                exist_ok=True,
                device=0 if _is_cuda_backend() else 'cpu',
                verbose=False,
                patience=10
            )

            # Localizar o best.pt gerado
            best_path = os.path.join(models_dir, f"epi_{epi_id}_{run_id}", 'weights', 'best.pt')
            if not os.path.exists(best_path):
                print(f"[Train:{epi_id}] best.pt não encontrado")
                return

            # Salvar modelo final em models/
            model_name = f"epi_{epi_id}.pt"
            dest_model = os.path.join(models_dir, model_name)
            shutil.copy(best_path, dest_model)
            print(f"[Train:{epi_id}] ✓ Modelo salvo: {dest_model}")

            # Atualizar metadados
            epis2 = _load_json(_epi_meta_path(uid))
            for e2 in epis2:
                if e2['id'] == epi_id:
                    e2['modelo_treinado'] = True
                    e2['modelo_path'] = dest_model
                    e2['treinado_em'] = time.time()
                    break
            _save_json(_epi_meta_path(uid), epis2)
            _upsert_model_meta(uid, model_name, classes=[nome_classe], required_items=[nome_classe],
                               extra={'trained_at': time.time(), 'source': 'epi', 'epi_id': epi_id})
            _export_tensorrt_async(uid, model_name, dest_model, reason='train_epi')

        except Exception as ex:
            print(f"[Train:{epi_id}] ERRO: {ex}")

    threading.Thread(target=_train, daemon=True, name=f"train-epi-{epi_id}").start()
    return jsonify({"message":"Treinamento iniciado em background","epi_id":epi_id}), 202


@app.route('/epis/foto/<foto_id>', methods=['GET'])
def serve_epi_foto(foto_id):
    """Serve uma foto de EPI pelo ID. Requer token."""
    u, err = _auth()
    if err: return err
    from flask import send_file
    epis = _load_json(_epi_meta_path(u['id']))
    for epi in epis:
        for foto in epi.get('fotos', []):
            if foto['id'] == foto_id:
                p = foto['path']
                if os.path.exists(p):
                    return send_file(p)
                return jsonify({"error":"Arquivo não encontrado"}), 404
    return jsonify({"error":"Foto não encontrada"}), 404

@app.route('/funcionarios/foto/<foto_id>', methods=['GET'])
def serve_func_foto(foto_id):
    """Serve uma foto de funcionário pelo ID. Requer token."""
    u, err = _auth()
    if err: return err
    from flask import send_file
    funcs = _load_json(_func_meta_path(u['id']))
    for func in funcs:
        for foto in func.get('fotos_rosto', []):
            if foto['id'] == foto_id:
                p = foto['path']
                if os.path.exists(p):
                    return send_file(p)
                return jsonify({"error":"Arquivo não encontrado"}), 404
    return jsonify({"error":"Foto não encontrada"}), 404


@app.route('/epis/<epi_id>/importar_dataset', methods=['POST'])
def importar_dataset_epi(epi_id):
    """Importa dataset anotado do Roboflow (zip com images/ e labels/ no formato YOLOv8).
    Isso substitui as fotos e labels do EPI, permitindo treino com anotações corretas."""
    u, err = _auth()
    if err: return err
    if 'dataset' not in request.files:
        return jsonify({"error": "Envie um arquivo .zip com images/ e labels/"}), 400
    epi_dir = _pasta_de_item(_user_epi_dir(u['id']), epi_id)
    # confere o EPI antes de mexer em arquivo: a limpeza abaixo apaga as fotos antigas
    if not epi_dir or not any(e['id'] == epi_id for e in _load_json(_epi_meta_path(u['id']))):
        return jsonify({"error": "EPI não encontrado"}), 404

    zip_file = request.files['dataset']
    os.makedirs(epi_dir, exist_ok=True)

    import zipfile as _zf, uuid as _uuid

    tmp_zip = os.path.join(epi_dir, '_import.zip')
    zip_file.save(tmp_zip)

    try:
        with _zf.ZipFile(tmp_zip) as z:
            names = z.namelist()
            # Procura arquivos de imagem e label correspondentes
            # limites contra "zip bomba": 5000 imagens de ate 25 MB cada
            tamanhos = {i.filename: i.file_size for i in z.infolist()}
            imgs   = [n for n in names if n.lower().endswith(('.jpg','.jpeg','.png'))
                      and tamanhos.get(n, 0) <= 25 * 1024 * 1024][:5000]
            labels = [n for n in names if n.endswith('.txt') and 'label' in n.lower()
                      and tamanhos.get(n, 0) <= 1024 * 1024]

            if not imgs:
                return jsonify({"error": "Nenhuma imagem encontrada no zip"}), 400

            # Limpar fotos antigas
            for f_old in os.listdir(epi_dir):
                if f_old.endswith(('.jpg','.jpeg','.png','.txt')) and not f_old.startswith('_'):
                    os.remove(os.path.join(epi_dir, f_old))

            fotos_novas = []
            for img_path in imgs:
                foto_id  = str(_uuid.uuid4())[:8]
                img_name = os.path.basename(img_path)
                stem     = os.path.splitext(img_name)[0]
                ext      = os.path.splitext(img_name)[1] or '.jpg'

                # Salvar imagem
                dest_img = os.path.join(epi_dir, f"{foto_id}{ext}")
                with z.open(img_path) as src, open(dest_img, 'wb') as dst:
                    dst.write(src.read())

                # Procurar label correspondente (mesmo nome, extensão .txt)
                label_path = next((l for l in labels
                                   if os.path.splitext(os.path.basename(l))[0] == stem), None)
                if label_path:
                    dest_lbl = os.path.join(epi_dir, f"{foto_id}.txt")
                    with z.open(label_path) as src, open(dest_lbl, 'w') as dst:
                        dst.write(src.read().decode('utf-8'))
                    fotos_novas.append({"id": foto_id, "path": dest_img,
                                        "label": dest_lbl, "anotada": True})
                else:
                    # Sem label: usa bbox de tela cheia (menos preciso)
                    fotos_novas.append({"id": foto_id, "path": dest_img,
                                        "label": None, "anotada": False})

    except Exception as e:
        return jsonify({"error": f"Erro ao processar zip: {e}"}), 500
    finally:
        try: os.remove(tmp_zip)
        except: pass

    # Atualizar metadados do EPI
    epis = _load_json(_epi_meta_path(u['id']))
    for epi in epis:
        if epi['id'] == epi_id:
            epi['fotos'] = [{"id": f["id"], "path": f["path"]} for f in fotos_novas]
            epi['labels_anotadas'] = {f["id"]: f["label"] for f in fotos_novas if f["label"]}
            epi['modelo_treinado'] = False
            n_anotadas = sum(1 for f in fotos_novas if f["anotada"])
            _save_json(_epi_meta_path(u['id']), epis)
            return jsonify({
                "message": f"Dataset importado: {len(fotos_novas)} imagens, {n_anotadas} anotadas",
                "total": len(fotos_novas),
                "anotadas": n_anotadas,
                "sem_label": len(fotos_novas) - n_anotadas
            })
    return jsonify({"error": "EPI nao encontrado"}), 404

# ═══════════════════════════════════════════════════════════════
# FUNCIONÁRIOS — Cadastro com fotos de rosto
# ═══════════════════════════════════════════════════════════════
@app.route('/funcionarios', methods=['GET'])
def list_funcionarios():
    u, err = _auth()
    if err: return err
    funcs = _load_json(_func_meta_path(u['id']))
    return jsonify({"funcionarios": funcs})

@app.route('/funcionarios', methods=['POST'])
def create_funcionario():
    u, err = _auth()
    if err: return err
    d = request.get_json(force=True)
    nome = d.get('nome','').strip()
    cargo = d.get('cargo','').strip()
    matricula = d.get('matricula','').strip()
    if not nome: return jsonify({"error":"Nome é obrigatório"}), 400

    import uuid as _uuid
    func_id = str(_uuid.uuid4())
    func = {
        "id": func_id,
        "nome": nome,
        "cargo": cargo,
        "matricula": matricula,
        "fotos_rosto": [],
        "criado_em": time.time()
    }
    func_dir = os.path.join(_user_func_dir(u['id']), func_id)
    os.makedirs(func_dir, exist_ok=True)

    funcs = _load_json(_func_meta_path(u['id']))
    funcs.append(func)
    _save_json(_func_meta_path(u['id']), funcs)
    _atualizar_funcionarios_em_streams(u['id'])
    _atualizar_galeria(u['id'])
    return jsonify({"funcionario": func}), 201

@app.route('/funcionarios/<func_id>', methods=['PUT'])
def update_funcionario(func_id):
    u, err = _auth()
    if err: return err
    d = request.get_json(force=True)
    funcs = _load_json(_func_meta_path(u['id']))
    for func in funcs:
        if func['id'] == func_id:
            func['nome'] = d.get('nome', func['nome'])
            func['cargo'] = d.get('cargo', func['cargo'])
            func['matricula'] = d.get('matricula', func['matricula'])
            _save_json(_func_meta_path(u['id']), funcs)
            _atualizar_funcionarios_em_streams(u['id'])
            return jsonify({"funcionario": func})
    return jsonify({"error":"Funcionário não encontrado"}), 404

@app.route('/funcionarios/<func_id>', methods=['DELETE'])
def delete_funcionario(func_id):
    u, err = _auth()
    if err: return err
    func_dir = _pasta_de_item(_user_func_dir(u['id']), func_id)
    if not func_dir: return jsonify({"error":"Funcionário não encontrado"}), 404
    funcs = _load_json(_func_meta_path(u['id']))
    funcs = [f for f in funcs if f['id'] != func_id]
    _save_json(_func_meta_path(u['id']), funcs)
    if os.path.exists(func_dir):
        shutil.rmtree(func_dir)
    # a galeria em memoria dos streams ainda tinha o rosto de quem saiu
    _atualizar_funcionarios_em_streams(u['id'])
    _atualizar_galeria(u['id'])
    return jsonify({"message":"Funcionário removido"})

@app.route('/funcionarios/<func_id>/fotos', methods=['POST'])
def upload_funcionario_foto(func_id):
    """Upload de foto de rosto do funcionário"""
    u, err = _auth()
    if err: return err
    if 'foto' not in request.files: return jsonify({"error":"Escolha uma foto para enviar."}), 400
    func_dir = _pasta_de_item(_user_func_dir(u['id']), func_id)
    dono = db.consultar_um('SELECT id FROM funcionarios WHERE id=%s AND uid=%s', (func_id, u['id'])) \
        if func_dir else None
    if not dono:
        return jsonify({"error":"Funcionário não encontrado"}), 404

    foto_file = request.files['foto']
    os.makedirs(func_dir, exist_ok=True)

    import uuid as _uuid
    foto_id = str(_uuid.uuid4())[:8]
    ext = seguranca.extensao_permitida(foto_file.filename, seguranca.EXTENSOES_FOTO, '.jpg')
    foto_path = os.path.join(func_dir, f"{foto_id}{ext}")
    foto_file.save(foto_path)

    # O rosto vira um vetor de 512 dimensoes agora, no cadastro. Nao existe mais
    # etapa de treino: reconhecer passa a ser comparar vetores.
    emb, motivo = face_id.embedding_de_foto(foto_path, _device_global)
    db.executar('INSERT INTO func_fotos (id, func_id, caminho, embedding, dim, extraido_em)'
                ' VALUES (%s,%s,%s,%s,%s,%s)',
                (foto_id, func_id, foto_path,
                 emb.tobytes() if emb is not None else None,
                 int(emb.size) if emb is not None else None,
                 time.time() if emb is not None else None))
    _atualizar_galeria(u['id'])
    total = db.consultar_um('SELECT COUNT(*) AS n FROM func_fotos WHERE func_id=%s', (func_id,))
    return jsonify({"foto_id": foto_id, "total": int(total['n']) if total else 1,
                    "rosto_detectado": emb is not None,
                    "aviso": '' if emb is not None else
                             f'Foto salva, mas nenhum rosto foi reconhecido nela ({motivo}). '
                             'Use uma foto de frente, com o rosto visível.'})

@app.route('/funcionarios/<func_id>/fotos/<foto_id>', methods=['DELETE'])
def delete_funcionario_foto(func_id, foto_id):
    u, err = _auth()
    if err: return err
    funcs = _load_json(_func_meta_path(u['id']))
    for func in funcs:
        if func['id'] == func_id:
            fotos_antes = func.get('fotos_rosto', [])
            foto_rem = next((f for f in fotos_antes if f['id'] == foto_id), None)
            if foto_rem and os.path.exists(foto_rem['path']):
                os.remove(foto_rem['path'])
            db.executar('DELETE FROM func_fotos WHERE id=%s AND func_id=%s', (foto_id, func_id))
            _atualizar_galeria(u['id'])
            return jsonify({"message":"Foto removida"})
    return jsonify({"error":"Funcionário não encontrado"}), 404

@app.route('/funcionarios/treinar_reconhecimento', methods=['POST'])
def recalcular_rostos():
    """Recalcula os embeddings de rosto de todos os funcionários.

    Antes isto treinava um YOLO classificador com uma classe por funcionário:
    exigia 3 fotos de cada um, demorava minutos e precisava rodar de novo a cada
    admissão. Agora o embedding sai no upload da foto, então esta rota só existe
    para reprocessar o que ficou para trás — fotos enviadas antes da mudança, ou
    que falharam quando o modelo de rosto não estava disponível."""
    u, err = _auth()
    if err: return err

    if not face_id.disponivel():
        # a mensagem antiga falava do pacote insightface, que o sistema nem usa mais
        return jsonify({'error': 'Reconhecimento facial indisponível neste servidor: '
                                 + (face_id.erro() or 'modelos de rosto não encontrados') + '.'}), 503

    fotos = db.consultar(
        'SELECT ff.id, ff.caminho, ff.embedding FROM func_fotos ff'
        ' JOIN funcionarios f ON f.id = ff.func_id WHERE f.uid=%s', (u['id'],))
    if not fotos:
        return jsonify({'error': 'Nenhuma foto de rosto cadastrada'}), 400

    refazer_tudo = bool((request.get_json(silent=True) or {}).get('tudo'))
    alvo = [f for f in fotos if refazer_tudo or not f['embedding']]
    if not alvo:
        return jsonify({'message': 'Todos os rostos já estão processados.',
                        'processadas': 0, 'total': len(fotos)})

    def _job():
        ok = falhou = 0
        for f in alvo:
            try:
                emb, motivo = face_id.embedding_de_foto(f['caminho'], _device_global)
                if emb is None:
                    falhou += 1
                    print(f"[face] {os.path.basename(f['caminho'])}: {motivo}")
                    continue
                db.executar('UPDATE func_fotos SET embedding=%s, dim=%s, extraido_em=%s WHERE id=%s',
                            (emb.tobytes(), int(emb.size), time.time(), f['id']))
                ok += 1
            except Exception as e:
                falhou += 1
                print(f'[face] falha em {f["id"]}: {e}')
        _atualizar_galeria(u['id'])
        print(f'[face] rostos processados: {ok} ok, {falhou} sem rosto reconhecível')

    threading.Thread(target=_job, daemon=True, name='rostos').start()
    return jsonify({'message': f'Processando {len(alvo)} foto(s) de rosto...',
                    'processando': len(alvo), 'total': len(fotos)}), 202


@app.route('/funcionarios/reconhecimento/status', methods=['GET'])
def status_reconhecimento():
    """Quantos rostos já estão prontos, para a tela de Funcionários mostrar."""
    u, err = _auth()
    if err: return err
    l = db.consultar_um(
        'SELECT COUNT(*) AS total, COUNT(ff.embedding) AS prontos FROM func_fotos ff'
        ' JOIN funcionarios f ON f.id = ff.func_id WHERE f.uid=%s', (u['id'],))
    total = int(l['total']) if l else 0
    prontos = int(l['prontos']) if l else 0
    return jsonify({'fotos': total, 'rostos_prontos': prontos, 'pendentes': total - prontos,
                    'motor': 'insightface/buffalo_l',
                    'disponivel': face_id.disponivel(), 'erro': face_id.erro()})



# ═══════════════════════════════════════════════════════════════
# ANALISES DE VIDEO — modos detalhado e super detalhado
# ═══════════════════════════════════════════════════════════════
def _resolve_job_model(uid, model_name):
    if model_name:
        mp = _resolve_model_path_for_user(uid, model_name)
        if os.path.exists(mp):
            return mp, (_load_model_meta(uid).get(os.path.basename(mp)) or {}).get('required_items')
    return _default_model_for(uid)


analysis_jobs = JobManager(DADOS_DIR, _resolve_job_model)


@app.route('/analises', methods=['GET'])
def list_analises():
    u, err = _auth()
    if err: return err
    return jsonify({'analises': analysis_jobs.list(u['id']),
                    'modos': {k: v['nome'] for k, v in ANALYSIS_MODES.items()}})


@app.route('/analises', methods=['POST'])
def create_analise():
    """Abre uma analise para receber a gravacao em partes (PUT /analises/<id>/partes/<n>)."""
    u, err = _auth()
    if err: return err
    d = request.get_json(force=True, silent=True) or {}
    try:
        job = analysis_jobs.create(u['id'], d.get('modo', 'detalhado'), name=str(d.get('nome') or '')[:120],
                                   model=d.get('modelo') or None, extra={
                                       'stream_id': d.get('stream_id'), 'fps_gravacao': d.get('fps'),
                                       'largura': d.get('largura'), 'altura': d.get('altura'),
                                       'mime': str(d.get('mime') or '')[:80],
                                       'dispositivo': 'câmera remota' if '_cam_session' in u else 'navegador'})
    except ValueError as ex:
        return jsonify({'error': str(ex)}), 400
    return jsonify({'analise': job}), 201


@app.route('/analises/upload', methods=['POST'])
def upload_analise():
    """Envia um video pronto (arquivo) direto para a fila."""
    u, err = _auth()
    if err: return err
    f = request.files.get('video')
    if not f: return jsonify({'error': 'Campo video obrigatório'}), 400
    try:
        job = analysis_jobs.create_from_file(u['id'], request.form.get('modo', 'detalhado'), f,
                                             name=request.form.get('nome', ''), model=request.form.get('modelo') or None)
    except ValueError as ex:
        return jsonify({'error': str(ex)}), 400
    return jsonify({'analise': job}), 201


@app.route('/analises/<jid>/partes/<int:seq>', methods=['PUT', 'POST'])
def chunk_analise(jid, seq):
    u, err = _auth()
    if err: return err
    if not valid_job_id(jid): return jsonify({'error': 'Análise não encontrada'}), 404
    try:
        return jsonify(analysis_jobs.add_chunk(u['id'], jid, seq, request.get_data(cache=False)))
    except KeyError as ex:
        return jsonify({'error': str(ex)}), 404
    except ValueError as ex:
        return jsonify({'error': str(ex)}), 409


@app.route('/analises/<jid>/finalizar', methods=['POST'])
def finish_analise(jid):
    u, err = _auth()
    if err: return err
    if not valid_job_id(jid): return jsonify({'error': 'Análise não encontrada'}), 404
    d = request.get_json(force=True, silent=True) or {}
    try:
        res = analysis_jobs.finish_upload(u['id'], jid, int(d.get('total_partes') or 0),
                                          ext=str(d.get('ext') or '.webm').lower(),
                                          duration_ms=d.get('duracao_ms'), fps=d.get('fps'))
    except KeyError as ex:
        return jsonify({'error': str(ex)}), 404
    except ValueError as ex:
        return jsonify({'error': str(ex)}), 409
    if not res['ok']:
        return jsonify({'error': 'Partes do vídeo faltando', 'faltando': res['faltando']}), 409
    return jsonify(res)


@app.route('/analises/<jid>', methods=['GET'])
def get_analise(jid):
    u, err = _auth()
    if err: return err
    job = analysis_jobs.get(u['id'], jid) if valid_job_id(jid) else None
    if not job: return jsonify({'error': 'Análise não encontrada'}), 404
    return jsonify({'analise': job})


@app.route('/analises/<jid>', methods=['DELETE'])
def delete_analise(jid):
    u, err = _auth()
    if err: return err
    if not valid_job_id(jid) or not analysis_jobs.remove(u['id'], jid):
        return jsonify({'error': 'Análise não encontrada'}), 404
    return jsonify({'message': 'Análise removida'})


@app.route('/analises/<jid>/arquivo/<path:relative>', methods=['GET'])
def analise_file(jid, relative):
    """Video resultado, miniaturas e quadros de revisao (?token= para usar em <video src>)."""
    u, err = _auth()
    if err: return err
    from flask import send_file
    path = analysis_jobs.file_path(u['id'], jid, relative) if valid_job_id(jid) else None
    if not path: return jsonify({'error': 'Arquivo não encontrado'}), 404
    return send_file(path, conditional=True, max_age=0)

# ═══════════════════════════════════════════════════════════════
# API V1 - integracao com empresas
@app.route('/api/v1/streams', methods=['GET'])
def api_v1_streams():
    u, err = _auth()
    if err: return err
    owner_uid = u.get('_cam_session', {}).get('uid') if isinstance(u, dict) and '_cam_session' in u else u['id']
    with user_streams_lock:
        sids = list(user_streams.get(owner_uid, []))
    data = []
    for sid in sids:
        with streams_lock:
            p = streams.get(sid)
        if p:
            data.append({'stream_id': sid, 'status': p.get_status()})
    return jsonify({'streams': data})


@app.route('/api/v1/streams/<stream_id>/detections', methods=['GET'])
def api_v1_stream_detections(stream_id):
    u, err = _auth()
    if err: return err
    owner_uid = u.get('_cam_session', {}).get('uid') if isinstance(u, dict) and '_cam_session' in u else u['id']
    with user_streams_lock:
        allowed = stream_id in user_streams.get(owner_uid, [])
    if not allowed and not stream_id.startswith(owner_uid):
        return jsonify({'error': 'Stream fora da conta autenticada'}), 403
    with streams_lock:
        p = streams.get(stream_id)
    if not p:
        return jsonify({'error': 'Stream nao encontrado'}), 404
    st = p.get_status()
    return jsonify({
        'stream_id': stream_id,
        'employee_detected': bool(st.get('employees')),
        'employees': st.get('employees', []),
        'missing_epi': st.get('missing_items', []),
        'missing_labels': st.get('missing', []),
        'alerts': st.get('alerts', []),
        'persons': st.get('persons', []),
        'detections': st.get('detections', []),
        'activity': st.get('activity'),
        'status': st.get('status'),
        'fps': st.get('fps'),
        'runtime_backend': st.get('runtime_backend'),
        'ts': time.time(),
    })


@app.route('/api/v1/funcionarios', methods=['GET'])
def api_v1_funcionarios():
    u, err = _auth()
    if err: return err
    funcs = []
    for f in _load_json(_func_meta_path(u['id'])):
        funcs.append({
            'id': f.get('id'),
            'nome': f.get('nome'),
            'cargo': f.get('cargo'),
            'matricula': f.get('matricula'),
            'modelo_treinado': bool(f.get('modelo_treinado')),
        })
    return jsonify({'funcionarios': funcs})


@app.route('/api/v1/epis', methods=['GET'])
def api_v1_epis():
    u, err = _auth()
    if err: return err
    epis = []
    for e in _load_json(_epi_meta_path(u['id'])):
        epis.append({
            'id': e.get('id'),
            'nome': e.get('nome'),
            'descricao': e.get('descricao'),
            'modelo_treinado': bool(e.get('modelo_treinado')),
            'fotos': len(e.get('fotos', [])),
        })
    return jsonify({'epis': epis})

# ADMIN
# ═══════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════
# ÁREAS DE RISCO — EPIs exigidos por local, mapeados sobre o vídeo
# ══════════════════════════════════════════════════════════════════

@app.route('/areas', methods=['GET'])
def list_areas():
    u, err = _auth()
    if err: return err
    itens = [{'key': k, 'label': v['label'], 'region': v['region']} for k, v in tax.ITEMS.items()]
    return jsonify({'areas': areas_mod.carregar(u['id']), 'epis_disponiveis': itens})


@app.route('/areas', methods=['POST'])
def create_area():
    u, err = _auth()
    if err: return err
    d = request.get_json(force=True, silent=True) or {}
    if not str(d.get('nome') or '').strip():
        return jsonify({'error': 'Nome da área é obrigatório'}), 400
    return jsonify({'area': areas_mod.criar(u['id'], d)}), 201


@app.route('/areas/<area_id>', methods=['PUT'])
def update_area(area_id):
    u, err = _auth()
    if err: return err
    d = request.get_json(force=True, silent=True) or {}
    area = areas_mod.atualizar(u['id'], area_id, d)
    if area is None:
        return jsonify({'error': 'Área não encontrada'}), 404
    _aplicar_zonas_em_streams(u['id'])
    return jsonify({'area': area})


@app.route('/areas/<area_id>', methods=['DELETE'])
def delete_area(area_id):
    u, err = _auth()
    if err: return err
    if not areas_mod.remover(u['id'], area_id):
        return jsonify({'error': 'Área não encontrada'}), 404
    areas_mod.remover_area_das_zonas(u['id'], area_id)
    _aplicar_zonas_em_streams(u['id'])
    return jsonify({'message': 'Área removida'})


@app.route('/streams/<sid>/zonas', methods=['GET', 'POST'])
def stream_zonas(sid):
    """Polígonos que marcam, na imagem desta câmera, onde fica cada área."""
    u, err = _auth()
    if err: return err
    owner = _owner_uid(u)
    if request.method == 'POST':
        if '_cam_session' in u:
            return jsonify({'error': 'As áreas são definidas no painel de controle'}), 403
        d = request.get_json(force=True, silent=True) or {}
        zonas = areas_mod.salvar_zonas(owner, sid, d.get('zonas'))
        _aplicar_zonas_em_streams(owner, sid)
    else:
        zonas = areas_mod.carregar_zonas(owner, sid)
    return jsonify({'zonas': zonas, 'areas': areas_mod.carregar(owner)})


# ══════════════════════════════════════════════════════════════════
# AUDITORIA — histórico de eventos, evidências e desempenho
# ══════════════════════════════════════════════════════════════════

@app.route('/auditoria/eventos', methods=['GET'])
def auditoria_eventos():
    u, err = _auth()
    if err: return err
    a = request.args
    dias = a.get('dias', type=float)
    return jsonify({'eventos': auditoria.eventos(
        u['id'],
        func_id=a.get('func_id'), tipo=a.get('tipo'), stream_id=a.get('stream_id'),
        area_id=a.get('area_id'),
        desde=(time.time() - dias * 86400) if dias else a.get('desde', type=float),
        ate=a.get('ate', type=float),
        limite=min(a.get('limite', default=200, type=int), 1000),
        offset=a.get('offset', default=0, type=int))})


@app.route('/auditoria/evidencia/<path:relativo>', methods=['GET'])
def auditoria_evidencia(relativo):
    u, err = _auth()
    if err: return err
    from flask import send_file
    p = auditoria.caminho_evidencia(DADOS_DIR, u['id'], relativo)
    if not p:
        return jsonify({'error': 'Evidência não encontrada ou já expirada'}), 404
    return send_file(p)


@app.route('/auditoria/ranking', methods=['GET'])
def auditoria_ranking():
    u, err = _auth()
    if err: return err
    dias = request.args.get('dias', default=30, type=int)
    return jsonify({'dias': dias, 'ranking': auditoria.ranking(u['id'], dias=dias)})


@app.route('/auditoria/limpar', methods=['POST'])
def auditoria_limpar():
    u, err = _auth()
    if err: return err
    dias = (request.get_json(silent=True) or {}).get('dias', auditoria.RETENCAO_DIAS)
    n = auditoria.limpar_antigas(DADOS_DIR, u['id'], dias=int(dias))
    return jsonify({'message': f'{n} evidência(s) removida(s)', 'removidas': n})


@app.route('/funcionarios/<func_id>/desempenho', methods=['GET'])
def funcionario_desempenho(func_id):
    """Dados dos gráficos de uso de EPI deste funcionário."""
    u, err = _auth()
    if err: return err
    funcs = _load_json(_func_meta_path(u['id']))
    func = next((f for f in funcs if f['id'] == func_id), None)
    if func is None:
        return jsonify({'error': 'Funcionário não encontrado'}), 404
    dias = request.args.get('dias', default=30, type=int)
    dados = auditoria.desempenho(u['id'], func_id=func_id, dias=dias)
    dados['funcionario'] = {'id': func['id'], 'nome': func['nome'], 'cargo': func.get('cargo', '')}
    # a linha do tempo usa o mesmo recorte dos graficos: fora disso os numeros
    # de cima e a lista de baixo se contradizem na tela
    dados['ultimos_eventos'] = auditoria.eventos(
        u['id'], func_id=func_id, desde=time.time() - dias * 86400, limite=30)
    return jsonify(dados)


@app.route('/admin/users', methods=['GET'])
def admin_users():
    _u, err = _auth_admin()
    if err: return err
    all_u = user_mgr.get_all_users()
    with user_streams_lock:
        for u in all_u:
            sids = user_streams.get(u['id'],[])
            u['streams_ativos'] = len(sids)
            u['stream_ids'] = sids
    return jsonify({"users":all_u})

@app.route('/admin/users/<uid>', methods=['GET'])
def admin_user(uid):
    _a, err = _auth_admin()
    if err: return err
    u = user_mgr.get_user(uid)
    if not u: return jsonify({"error":"Não encontrado"}), 404
    with user_streams_lock: sids = list(user_streams.get(uid,[]))
    u['streams_ativos'] = len(sids); u['stream_ids'] = sids
    return jsonify({"user":u})

@app.route('/admin/users/<uid>/block', methods=['POST'])
def admin_block(uid):
    _a, err = _auth_admin()
    if err: return err
    d = request.get_json(force=True, silent=True) or {}
    return jsonify({"success": user_mgr.block_user(uid, d.get('block',True))})

@app.route('/admin/users/<uid>/restrict', methods=['POST'])
def admin_restrict(uid):
    _a, err = _auth_admin()
    if err: return err
    d = request.get_json(force=True, silent=True) or {}
    return jsonify({"success": user_mgr.set_restrictions(uid, d.get('restricoes',[]))})

@app.route('/admin/frame', methods=['GET'])
def admin_frame():
    u, err = _auth()
    if err: return err
    sid = request.args.get('stream','')
    if not sid: return jsonify({"frame":None}), 400
    # o quadro e da camera de alguem: so o dono (ou um admin) pode ver
    if (u.get('role') or 'user') != 'admin' and not _owns_stream(u['id'], sid):
        return jsonify({"error":"Câmera de outro usuário"}), 403
    with streams_lock: p = streams.get(sid)
    if not p: return jsonify({"frame":None}), 204
    fb = p.get_frame_b64()
    if not fb: return jsonify({"frame":None}), 204
    return jsonify({"frame":fb,"status":p.get_status()})

@app.route('/dashboard/metrics', methods=['GET'])
def metrics():
    _u, err = _auth_admin()
    if err: return err
    m = _sys_metrics()
    with streams_lock: m['active_streams'] = len(streams)
    with user_streams_lock: m['active_users'] = len([u for u,s in user_streams.items() if s])
    m['total_users'] = len(user_mgr.get_all_users())
    return jsonify(m)

@app.route('/status', methods=['GET'])
def status():
    u, _err = _auth()
    return jsonify({"paused":_pausado(_owner_uid(u)) if u else False,
                    "cloudflare_url":cloudflare_url,
                    "local_ip":local_ip,"active_streams":len(streams),
                    "server_kind":_server_kind(),
                    "availability":_availability_score(),
                    "node_id":BACKEND_NODE_ID,
                    "public_backend_url":_public_backend_url(),
                    "tensorrt_available":_tensorrt_export_available(),
                    "tensorrt_error":_tensorrt_dependency_error()})

@app.route('/tunnels', methods=['GET'])
def tunnels():
    return jsonify({
        "local_ip_url": f"http://{local_ip}:8088" if local_ip else None,
        "cloudflare_url": cloudflare_url,
        "frontend_local": f"http://{local_ip}:8088" if local_ip else None,
        "frontend_cloudflare": cloudflare_url
    })

# ═══════════════════════════════════════════════════════════════
# CAM TOKENS
# ═══════════════════════════════════════════════════════════════
@app.route('/cam_tokens', methods=['GET'])
def get_cam_tokens():
    u, err = _auth()
    if err: return err
    if '_cam_session' in u: return jsonify({"error":"Câmeras não podem acessar"}), 403
    return jsonify({"tokens": user_mgr.list_cam_tokens(u['id'])})

@app.route('/cam_tokens', methods=['POST'])
def create_cam_token_route():
    u, err = _auth()
    if err: return err
    if '_cam_session' in u: return jsonify({"error":"Câmeras não podem acessar"}), 403
    d = request.get_json(force=True)
    nome = d.get('nome', 'Câmera')
    t = user_mgr.create_cam_token(u['id'], nome)
    if not t: return jsonify({"error":"Falha ao criar token"}), 500
    return jsonify({"token": t})

@app.route('/cam_tokens/<token>', methods=['DELETE'])
def delete_cam_token_route(token):
    u, err = _auth()
    if err: return err
    if '_cam_session' in u: return jsonify({"error":"Câmeras não podem acessar"}), 403
    return jsonify({"success": user_mgr.delete_cam_token(u['id'], token)})

# ═══════════════════════════════════════════════════════════════
# FRONTEND SERVING
# ═══════════════════════════════════════════════════════════════
# sem esta rota o Flask serviria frontend/favicon.ico sozinho; ela existia
# devolvendo 204 e apagava o icone da marca no acesso local
@app.route('/favicon.ico')
def favicon():
    return app.send_static_file('favicon.ico')

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/cam')
def cam_page():
    return app.send_static_file('cam.html')

if __name__ == '__main__':
    if torch.cuda.is_available(): _device_global = '0'
    else:
        try: import torch_directml; _device_global = 'dml'
        except: _device_global = 'cpu'
    local_ip = get_local_ip()
    iniciar_banco()
    threading.Thread(target=_start_cf, args=(8088,), daemon=True).start()
    start_hub_registration()
    start_auditoria_manutencao()
    app.run(host='0.0.0.0', port=8088, threaded=True, debug=False)
