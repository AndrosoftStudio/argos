"""
ajuda.py - Servidores da mesma conta dividem o trabalho de uma camera.

A camera continua com um dono: o servidor que recebe o video faz o rastreio das pessoas e a
votacao por pessoa (isso tem memoria e nao pode ser repartido). O que e pesado e nao tem memoria
pode ser feito por outro servidor da conta, ao mesmo tempo:
  - rosto: achar os rostos do quadro e tirar o vetor de cada um (o dono compara com a galeria);
  - epi:   rodar o detector de EPIs no quadro (o mesmo modelo, conferido pelo hash do arquivo).

Para acelerar sem nunca atrapalhar:
  - so entre servidores da mesma rede local (o quadro nao viaja pela internet) e que provam ser
    da mesma conta (a credencial da malha);
  - todo pedido e cronometrado. O dono so conta com o par quando ele tem respondido dentro do
    tempo que o dono levaria sozinho; fora disso manda no maximo uma sonda de vez em quando;
  - se a resposta nao chega a tempo, o dono faz o trabalho aqui mesmo (como sempre fez) e o
    par fica um tempo de fora, cada vez maior se continuar atrasando;
  - quem ajuda atende primeiro as proprias cameras: ocupado, responde "ocupado" na hora.

ARGOS_AJUDA=0 desliga (nem pede nem atende).
"""
import base64
import hashlib
import ipaddress
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import cv2
import numpy as np
import requests

import conta

LIGADA = os.environ.get('ARGOS_AJUDA', '1').strip().lower() not in ('0', 'false', 'no', 'off')
CONFERIR_S = 30.0        # de quanto em quanto tempo pergunta aos pares o que sabem fazer
SONDA_S = 4.0            # par mais lento que o orcamento: uma sonda a cada tanto, para ver se melhorou
MAX_JPEG = 4 << 20
VAGAS = 2                # pedidos atendidos ao mesmo tempo por quem ajuda
LIMITE_S = 1.5           # resposta mais lenta que isto nao interessa a ninguem
CASTIGO_MAX_S = 60.0

_ctx = {'models_dir': '', 'device': lambda: 'cpu', 'livre': lambda: 100}


def configurar(models_dir, device, livre):
    """models_dir: pasta dos modelos globais; device(): onde este servidor processa;
    livre(): 0..100, quanto da maquina esta sobrando."""
    _ctx.update(models_dir=models_dir, device=device, livre=livre)


def _media(antiga, nova, peso=0.25):
    return nova if antiga is None else antiga * (1 - peso) + nova * peso


# ── Hash dos modelos: dono e ajudante precisam ter exatamente o mesmo arquivo ──
_sha = {}


def sha_do_modelo(caminho: str) -> str:
    try:
        st = os.stat(caminho)
    except OSError:
        return ''
    chave = (os.path.abspath(caminho), st.st_size, int(st.st_mtime))
    if chave not in _sha:
        h = hashlib.sha1()
        with open(caminho, 'rb') as f:
            for bloco in iter(lambda: f.read(1 << 20), b''):
                h.update(bloco)
        _sha[chave] = h.hexdigest()[:16]
    return _sha[chave]


# ══════════════════════════════════════════════════════════════════
# QUEM AJUDA
# ══════════════════════════════════════════════════════════════════
_vagas = threading.BoundedSemaphore(VAGAS)
_detectores = {}          # caminho -> {'det', 'fila', 'usado', 'erro'}
_det_lock = threading.Lock()
_atendidos = {'rosto': 0, 'epi': 0, 'ocupado': 0}


def capacidades() -> dict:
    modelos = {}
    pasta = _ctx['models_dir']
    try:
        for nome in os.listdir(pasta):
            if nome.endswith('.pt') and '-pose' not in nome:
                modelos[nome] = sha_do_modelo(os.path.join(pasta, nome))
    except OSError:
        pass
    try:
        import face_id
        rosto = face_id.disponivel()
    except Exception:
        rosto = False
    return {'ajuda': LIGADA, 'device': str(_ctx['device']()), 'modelos': modelos, 'rosto': rosto,
            'livre': int(_ctx['livre']()), 'atendidos': dict(_atendidos)}


def _carregar_detector(caminho, e):
    try:
        from epi_detector import EpiDetector, resolve_device
        det = EpiDetector(caminho, resolve_device('auto'), use_tensorrt=True,
                          log=lambda m: print(f'[ajuda] {m}', flush=True))
        det.detect(np.zeros((640, 640, 3), np.uint8), 640, 0.35)   # a primeira inferencia leva segundos: fica feita
        e['det'] = det
        print(f'[ajuda] detector pronto para ajudar outros servidores: {os.path.basename(caminho)}', flush=True)
    except Exception as ex:
        e['erro'] = str(ex)[:200]
    with _det_lock:   # no maximo 2 modelos carregados so para ajudar
        for velho in sorted(_detectores, key=lambda c: _detectores[c]['usado'])[:-2]:
            _detectores.pop(velho)['fila'].shutdown(wait=False)


def _detector(caminho):
    with _det_lock:
        e = _detectores.get(caminho)
        if e is None:
            # Cada detector tem a SUA thread, sempre a mesma: na GPU a primeira inferencia de cada thread
            # nova custa segundos (o cuDNN e preparado por thread), e o Flask atende cada pedido numa
            # thread diferente. Sem isto a ajuda levava 2 s por quadro em vez de 20 ms.
            e = _detectores[caminho] = {'det': None, 'usado': time.time(), 'erro': '',
                                        'fila': ThreadPoolExecutor(max_workers=1, thread_name_prefix='ajuda-det')}
            e['fila'].submit(_carregar_detector, caminho, e)
        e['usado'] = time.time()
        return e


def esquecer_detectores():
    """O servidor trocou de placa/CPU: os detectores carregados para ajudar sao refeitos."""
    with _det_lock:
        for e in _detectores.values():
            e['fila'].shutdown(wait=False)
        _detectores.clear()


def atender(jpeg: bytes, tarefas, args) -> dict:
    """Trabalho pedido por outro servidor da conta. Nunca espera: sem vaga, responde ocupado."""
    if not LIGADA:
        return {'erro': 'desligada'}
    if not jpeg or len(jpeg) > MAX_JPEG:
        return {'erro': 'quadro invalido'}
    if _ctx['livre']() < 8 or not _vagas.acquire(blocking=False):
        _atendidos['ocupado'] += 1
        return {'ocupado': True}
    try:
        t0 = time.perf_counter()
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return {'erro': 'quadro invalido'}
        out = {}
        if 'epi' in tarefas:
            caminho = os.path.join(_ctx['models_dir'], os.path.basename(str(args.get('modelo') or '')))
            if not os.path.isfile(caminho) or sha_do_modelo(caminho) != args.get('sha'):
                out['epi_erro'] = 'modelo'
            else:
                e = _detector(caminho)
                if e['det'] is None:
                    out['epi_erro'] = 'erro' if e['erro'] else 'carregando'
                else:
                    imgsz = max(320, min(1920, int(args.get('imgsz') or 640)))
                    conf = max(0.05, min(0.95, float(args.get('conf') or 0.35)))
                    dets = e['fila'].submit(e['det'].detect, img, imgsz, conf).result(timeout=LIMITE_S * 2)
                    out['dets'] = [dict(d, box=[round(float(v), 2) for v in d['box']]) for d in dets]
                    _atendidos['epi'] += 1
        if 'rosto' in tarefas:
            import face_id
            rostos = face_id.detectar(img, _ctx['device']())
            out['rostos'] = [{'bbox': [round(float(v), 2) for v in r['bbox']],
                              'emb': base64.b64encode(np.asarray(r['embedding'], np.float32).tobytes()).decode()}
                             for r in rostos]
            _atendidos['rosto'] += 1
        out['ms'] = round((time.perf_counter() - t0) * 1000, 1)
        return out
    except Exception as ex:
        return {'erro': str(ex)[:200]}
    finally:
        _vagas.release()


# ══════════════════════════════════════════════════════════════════
# QUEM PEDE (o dono da camera)
# ══════════════════════════════════════════════════════════════════
class _Par:
    def __init__(self, pid, nome, url):
        self.id, self.nome, self.url = pid, nome, url
        self.sessao = requests.Session()
        self.sessao.mount('http://', requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=8))
        self.caps = None
        self.erro = ''
        self.ms = {}            # tarefa -> tempo de ida e volta (ms), media movel
        self.castigo = {}       # tarefa -> ate quando fica de fora
        self.falhas = {}        # tarefa -> atrasos seguidos
        self.sonda = {}         # tarefa -> ultima sonda
        self.voando = 0         # pedidos em andamento
        self.usados = {'rosto': 0, 'epi': 0}
        self.atrasos = 0
        self.lock = threading.Lock()

    def pode(self, tarefa, agora):
        return self.caps is not None and self.caps.get('ajuda') and agora >= self.castigo.get(tarefa, 0) \
            and self.voando < VAGAS

    def castigar(self, tarefa, segundos=None):
        with self.lock:
            n = self.falhas[tarefa] = self.falhas.get(tarefa, 0) + 1
            self.castigo[tarefa] = time.monotonic() + (segundos if segundos else min(CASTIGO_MAX_S, 2.0 ** n))


_pares = {}
_pares_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix='ajuda')
_cred = {'valor': '', 'em': 0.0}
_iniciada = False


def _credencial():
    agora = time.monotonic()
    if not _cred['valor'] or agora - _cred['em'] > CONFERIR_S:
        try:
            _cred.update(valor=conta.credencial(), em=agora)
        except Exception:
            pass
    return _cred['valor']


def _rede_local(url: str) -> bool:
    """So fala com par em endereco de rede privada: o quadro da camera nao sai para a internet."""
    try:
        host = urlparse(url).hostname or ''
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


def _conferir_pares():
    if not (LIGADA and conta.vinculado()):
        with _pares_lock:
            _pares.clear()
        return
    vistos = set()
    for p in conta.pares():
        url = str(p.get('url_local') or '').rstrip('/')
        if not p.get('online') or not url or not _rede_local(url):
            continue
        vistos.add(p['id'])
        with _pares_lock:
            par = _pares.get(p['id'])
            if par is None or par.url != url:
                par = _pares[p['id']] = _Par(p['id'], p.get('nome') or 'servidor', url)
        try:
            r = par.sessao.get(url + '/malha/ajuda', headers={'Authorization': 'Bearer ' + _credencial()},
                               timeout=(0.8, 2.0))
            r.raise_for_status()
            antes = par.caps
            par.caps, par.erro = r.json(), ''
            if antes is None and par.caps.get('ajuda'):
                print(f"[ajuda] {par.nome} ({url}) pode ajudar nas cameras daqui "
                      f"({par.caps.get('device')}, {len(par.caps.get('modelos') or {})} modelo(s))", flush=True)
        except Exception as ex:
            if par.caps is not None:
                print(f'[ajuda] {par.nome} parou de responder na rede local: {type(ex).__name__}', flush=True)
            par.caps, par.erro = None, type(ex).__name__
    with _pares_lock:
        for pid in [k for k in _pares if k not in vistos]:
            _pares.pop(pid, None)


def _laco():
    time.sleep(8)
    while True:
        try:
            _conferir_pares()
        except Exception as ex:
            print(f'[ajuda] conferencia dos pares falhou: {ex}', flush=True)
        _acordar.wait(CONFERIR_S)
        _acordar.clear()


_acordar = threading.Event()


def iniciar():
    global _iniciada
    if _iniciada or not LIGADA:
        return
    _iniciada = True
    conta.ao_mudar(_acordar.set)
    threading.Thread(target=_laco, daemon=True, name='ajuda-pares').start()


def ativa() -> bool:
    return LIGADA and bool(_pares)


class Pedido:
    """Um trabalho que esta sendo feito por um par. contar=True: o dono esta contando com a resposta
    (nao fez o trabalho aqui); contar=False: e uma sonda, serve so para medir o par."""

    def __init__(self, par, tarefa, fut, contar):
        self.par, self.tarefa, self.fut, self.contar = par, tarefa, fut, contar

    def resultado(self, esperar_s=0.0):
        """A resposta do par, se chegar em ate esperar_s. None = nao deu: faca aqui."""
        try:
            r = self.fut.result(timeout=max(esperar_s, 0.0))
        except Exception:
            r = None
            if not self.fut.done():      # nao chegou a tempo
                self.par.atrasos += 1
                self.par.castigar(self.tarefa)
        if r is None:
            return None
        self.par.falhas[self.tarefa] = 0
        self.par.usados[self.tarefa.split(':')[0]] += 1
        return r


def _enviar(par, tarefa, jpeg, params):
    """Roda numa thread do pool. Devolve a resposta util ou None; mede o tempo de ida e volta."""
    t0 = time.perf_counter()
    try:
        r = par.sessao.post(par.url + '/malha/ajuda', params=params, data=jpeg,
                            headers={'Authorization': 'Bearer ' + _credencial(), 'Content-Type': 'image/jpeg'},
                            timeout=(0.5, LIMITE_S))
        r.raise_for_status()
        d = r.json()
    except Exception as ex:
        par.erro = type(ex).__name__
        par.castigar(tarefa)
        return None
    finally:
        with par.lock:
            par.voando -= 1
    ms = (time.perf_counter() - t0) * 1000
    if d.get('ocupado'):
        par.castigar(tarefa, 3.0)
        return None
    if d.get('erro'):
        par.castigar(tarefa, 30.0)
        return None
    if tarefa.startswith('epi') and 'dets' not in d:
        # 'carregando': o par esta abrindo o modelo, volta ja; 'modelo': ele nao tem esse arquivo
        par.castigar(tarefa, 4.0 if d.get('epi_erro') == 'carregando' else 300.0)
        return None
    par.ms[tarefa] = _media(par.ms.get(tarefa), ms)
    return d


def _escolher(tarefa, orcamento_ms, serve=lambda par: True):
    """(par, contar). contar=True quando o par tem respondido dentro do orcamento; False = sonda."""
    agora = time.monotonic()
    with _pares_lock:
        pares = [p for p in _pares.values() if p.pode(tarefa, agora) and serve(p)]
    melhor = min((p for p in pares if p.ms.get(tarefa) is not None and p.ms[tarefa] <= orcamento_ms),
                 key=lambda p: p.ms[tarefa], default=None)
    if melhor is not None:
        return melhor, True
    for p in pares:   # ainda sem medida, ou lento da ultima vez: uma sonda de vez em quando
        if agora - p.sonda.get(tarefa, 0) >= (0.5 if p.ms.get(tarefa) is None else SONDA_S):
            p.sonda[tarefa] = agora
            return p, False
    return None, False


def _pedir(tarefa, jpeg, params, orcamento_ms, serve=lambda par: True):
    if not ativa() or not jpeg or len(jpeg) > MAX_JPEG:
        return None
    par, contar = _escolher(tarefa, orcamento_ms, serve)
    if par is None:
        return None
    with par.lock:
        par.voando += 1
    return Pedido(par, tarefa, _pool.submit(_enviar, par, tarefa, jpeg, params), contar)


def pedir_rostos(jpeg, orcamento_ms):
    """Rostos do quadro por um par. orcamento_ms: em quanto tempo o dono teria o resultado sozinho."""
    return _pedir('rosto', jpeg, {'t': 'rosto'}, orcamento_ms, lambda par: bool(par.caps.get('rosto')))


def pedir_epis(jpeg, modelo, imgsz, conf, orcamento_ms):
    """Detector de EPIs por um par que tenha o mesmo arquivo de modelo."""
    nome, sha = os.path.basename(modelo or ''), sha_do_modelo(modelo or '')
    if not sha:
        return None
    return _pedir(f'epi:{sha}:{imgsz}', jpeg, {'t': 'epi', 'modelo': nome, 'sha': sha, 'imgsz': imgsz, 'conf': conf},
                  orcamento_ms, lambda par: (par.caps.get('modelos') or {}).get(nome) == sha)


def rostos_da_resposta(d) -> list:
    """[{'bbox', 'embedding'}] no formato de face_id.detectar."""
    saida = []
    for r in d.get('rostos') or []:
        try:
            emb = np.frombuffer(base64.b64decode(r['emb']), np.float32)
        except Exception:
            continue
        if emb.size:
            saida.append({'bbox': r.get('bbox'), 'embedding': emb})
    return saida


def dets_da_resposta(d) -> list:
    return [dict(x, box=tuple(x['box'])) for x in d.get('dets') or []]


def resumo() -> list:
    """Para o painel: quem esta ajudando este servidor e como esta indo."""
    agora = time.monotonic()
    with _pares_lock:
        pares = list(_pares.values())
    saida = []
    for p in pares:
        epi = [v for k, v in p.ms.items() if k.startswith('epi')]
        saida.append({'id': p.id, 'nome': p.nome, 'disponivel': p.caps is not None and bool(p.caps.get('ajuda')),
                      'device': (p.caps or {}).get('device'), 'erro': p.erro,
                      'ms_rosto': round(p.ms['rosto']) if p.ms.get('rosto') is not None else None,
                      'ms_epi': round(min(epi)) if epi else None,
                      'usados': dict(p.usados), 'atrasos': p.atrasos,
                      'de_fora': sorted(k.split(':')[0] for k, v in p.castigo.items() if v > agora)})
    return saida
