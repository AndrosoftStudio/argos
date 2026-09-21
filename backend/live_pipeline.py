"""
live_pipeline.py - Os tres modos de camera ao vivo, com atraso controlado.

Os tres modos sao em tempo real. Os quadros chegam (ate 60 q/s) e entram num buffer ordenado
pelo horario de captura. A GPU analisa todos os quadros que conseguir (quadros-chave), e o
resultado de CADA quadro sai no ritmo original da camera com um atraso fixo. O atraso permite:
  - interpolar pessoas, esqueletos e EPIs entre dois quadros-chave (caixas em todos os quadros);
  - decidir o estado de cada EPI olhando tambem um pouco do futuro (sem piscadas nem atraso);
  - absorver oscilacoes e quedas curtas da rede sem travar a imagem.

  tempo_real  ~0,35 s  pose nano,   detector 640 px,  quadro mais novo primeiro
  detalhado   ~1,5 s   pose small,  detector 960 px,  quadros em ordem, janela centrada
  super       ~4 s     pose medium, detector 1280 px + recortes ampliados de cada pessoa,
                       janela centrada maior

Protocolo binario dos quadros (cliente -> servidor e servidor -> painel):
  'AG' + versao (1 byte) + reservado (1 byte) + seq (uint32 BE) + horario de captura em ms
  (float64 BE) + JPEG. O formato antigo (4 bytes de seq + JPEG) continua aceito.
"""
import json
import struct
import threading
import time
from bisect import bisect_left, bisect_right
from collections import deque

import cv2
import numpy as np

from ppe_analyzer import WEIGHTS, DEFAULT_CONFIG, activity_text, decide_state, summarize
import ppe_taxonomy as tax

MODOS = {
    'tempo_real': {'nome': 'Tempo real', 'atraso': 0.35, 'atraso_max': 1.2, 'pose': 'tempo_real', 'imgsz': 640,
                   'conf': 0.35, 'recortes': False, 'passado': 1.5, 'futuro': 0.2, 'escolha': 'recente',
                   'intervalo_max': 0.25, 'largura_envio': 960},
    'detalhado': {'nome': 'Detalhado', 'atraso': 1.5, 'atraso_max': 3.0, 'pose': 'detalhado', 'imgsz': 960,
                  'conf': 0.3, 'recortes': False, 'passado': 0.9, 'futuro': 0.75, 'escolha': 'ordem',
                  'intervalo_max': 0.2, 'largura_envio': 1280},
    'super': {'nome': 'Super detalhado', 'atraso': 4.0, 'atraso_max': 7.0, 'pose': 'super', 'imgsz': 1280,
              'conf': 0.25, 'recortes': True, 'passado': 1.5, 'futuro': 1.5, 'escolha': 'ordem',
              'intervalo_max': 0.2, 'largura_envio': 1600},
}
MODO_PADRAO = 'tempo_real'
FPS_OPCOES = (15, 30, 60)
RES_OPCOES = (480, 720, 1080)
CONFIG_PADRAO = {'modo': MODO_PADRAO, 'fps': 60, 'resolucao': 720}

MAGIC = b'AG'
HEADER_LEN = 16


def normalizar_config(cfg, base=None):
    out = dict(base or CONFIG_PADRAO)
    cfg = cfg or {}
    if cfg.get('modo') in MODOS:
        out['modo'] = cfg['modo']
    for chave, opcoes in (('fps', FPS_OPCOES), ('resolucao', RES_OPCOES)):
        try:
            v = int(cfg.get(chave, out[chave]))
        except (TypeError, ValueError):
            continue
        out[chave] = min(opcoes, key=lambda o: abs(o - v))
    return out


def config_publica(cfg):
    perfil = MODOS[cfg['modo']]
    return {**cfg, 'modo_nome': perfil['nome'], 'atraso_s': perfil['atraso'],
            'largura_envio': perfil['largura_envio'],
            'janela_s': round(perfil['atraso_max'] + 0.5, 2)}


def pack_frame(seq, t_ms, jpeg):
    return MAGIC + b'\x01\x00' + struct.pack('>Id', int(seq) & 0xFFFFFFFF, float(t_ms)) + bytes(jpeg)


def unpack_frame(msg):
    """(seq, t em segundos ou None, jpeg) ou None."""
    msg = bytes(msg)
    if len(msg) > HEADER_LEN and msg[:2] == MAGIC:
        seq, t_ms = struct.unpack('>Id', msg[4:HEADER_LEN])
        return seq, t_ms / 1000.0, msg[HEADER_LEN:]
    if len(msg) > 8:  # formato antigo: sem horario de captura
        return int.from_bytes(msg[:4], 'big'), None, msg[4:]
    return None


def jpeg_size(data):
    """(largura, altura) lidos do cabecalho SOF, sem decodificar a imagem."""
    i, n = 2, len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = (data[i + 2] << 8) | data[i + 3]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            return (data[i + 7] << 8) | data[i + 8], (data[i + 5] << 8) | data[i + 6]
        i += 2 + length
    return 0, 0


def public_detection(d):
    return {
        'label': d['label'], 'item': d['item'], 'present': d['present'],
        'confidence': d['conf'], 'bbox': [round(float(v), 1) for v in d['box']],
        'source': d.get('source', 'epi'), 'person': d.get('person'),
    }


# ── Interpolacao entre quadros-chave ─────────────────────────────────
def _lerp_list(a, b, f):
    return [round(x + (y - x) * f, 1) for x, y in zip(a, b)]


def _copy_person(p):
    q = {k: v for k, v in p.items() if k != 'evidencias'}
    q['epis'] = {i: dict(s) for i, s in p.get('epis', {}).items()}
    q['faltando'] = list(p.get('faltando', []))
    q['alertas'] = list(p.get('alertas', []))
    return q


def _interp_persons(pa_list, pb_list, f):
    by_id = {p['track_id']: p for p in pb_list if p.get('track_id') is not None}
    out, usados = [], set()
    for pa in pa_list:
        tid = pa.get('track_id')
        pb = by_id.get(tid) if tid is not None else None
        if pb is None:
            if f < 0.5:
                out.append(_copy_person(pa))
            continue
        usados.add(tid)
        q = _copy_person(pa if f < 0.5 else pb)
        q['bbox'] = _lerp_list(pa['bbox'], pb['bbox'], f)
        ka, kb = pa.get('keypoints') or [], pb.get('keypoints') or []
        if len(ka) == len(kb) == 17:
            a, b = np.asarray(ka, dtype=np.float32), np.asarray(kb, dtype=np.float32)
            k = a + (b - a) * f
            k[:, 2] = np.minimum(a[:, 2], b[:, 2])
            q['keypoints'] = np.round(k, 1).tolist()
        out.append(q)
    if f >= 0.5:
        out.extend(_copy_person(pb) for pb in pb_list if pb.get('track_id') is None or pb['track_id'] not in usados)
    return out


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _interp_dets(da, db, f):
    base, other = (da, db) if f < 0.5 else (db, da)
    out = []
    for d in base:
        m = max((o for o in other if o['item'] == d['item'] and o['present'] == d['present']),
                key=lambda o: _iou(o['box'], d['box']), default=None)
        q = dict(d)
        if m is not None and _iou(m['box'], d['box']) > 0.15:
            a, b = (d, m) if f < 0.5 else (m, d)
            q['box'] = tuple(_lerp_list(a['box'], b['box'], f))
        out.append(q)
    return out


# ── Estados de EPI com janela passado + futuro ──────────────────────
class _Revisor:
    """Estado de cada EPI por pessoa no instante exibido, usando as evidencias dos quadros-chave
    de [t - passado, t + futuro]. Chamado em ordem de exibicao; as evidencias chegam em ordem de analise."""

    def __init__(self, perfil, cfg=None):
        self.cfg = {**DEFAULT_CONFIG, **(cfg or {})}
        self.passado, self.futuro = perfil['passado'], perfil['futuro']
        self.lock = threading.Lock()
        self.ev = {}          # (tid, item) -> deque[(t, peso)]
        self.estado = {}      # (tid, item) -> estado decidido no ultimo quadro exibido
        self.ultimo_nz = {}   # (tid, item) -> ultimo t exibido com evidencia na janela
        self.desde = {}       # (tid, item) -> t em que comecou a faltar

    def adicionar(self, t, analysis):
        with self.lock:
            for p in analysis.get('persons', []):
                tid = p.get('track_id')
                if tid is None:
                    continue
                for item, fonte in (p.get('evidencias') or {}).items():
                    peso = WEIGHTS.get(fonte, 0.0)
                    if peso:
                        self.ev.setdefault((tid, item), deque()).append((t, peso))

    def aplicar(self, persons, t):
        cfg = self.cfg
        with self.lock:
            for p in persons:
                tid = p.get('track_id')
                if tid is None:
                    continue
                faltando = []
                for item, s in p['epis'].items():
                    chave = (tid, item)
                    dq = self.ev.get(chave)
                    while dq and dq[0][0] < t - self.passado:
                        dq.popleft()
                    janela = [w for ti, w in dq if ti <= t + self.futuro] if dq else []
                    anterior = self.estado.get(chave, 'nao_visivel')
                    if janela:
                        estado = decide_state(anterior, janela, cfg)
                        self.ultimo_nz[chave] = t
                    elif t - self.ultimo_nz.get(chave, -1e9) > 3 * cfg['window_sec']:
                        estado = 'nao_visivel'
                    else:
                        estado = anterior
                    self.estado[chave] = estado
                    if estado == 'faltando':
                        desde = self.desde.setdefault(chave, t)
                        faltando.append(item)
                    else:
                        self.desde.pop(chave, None)
                        desde = None
                    dur = t - desde if desde is not None else 0.0
                    s.update(estado=estado, faltando_ha=round(dur, 1),
                             alerta=estado == 'faltando' and dur >= cfg['alert_sec'])
                quedas = [a for a in p.get('alertas', []) if a == 'Possível queda']
                p['faltando'] = faltando
                p['alertas'] = [f"Sem {tax.item_label(i).lower()} há {p['epis'][i]['faltando_ha']:.0f}s"
                                for i in faltando if p['epis'][i]['alerta']] + quedas
            vivos = {p.get('track_id') for p in persons}
            for chave in [c for c, v in self.ultimo_nz.items() if c[0] not in vivos and t - v > cfg['forget_sec'] * 4]:
                for d in (self.ev, self.estado, self.ultimo_nz, self.desde):
                    d.pop(chave, None)
        return persons


# ── Pecas internas ──────────────────────────────────────────────────
class _Quadro:
    __slots__ = ('seq', 't', 'jpeg', 'w', 'h', 'chegada')

    def __init__(self, seq, t, jpeg, w, h, chegada):
        self.seq, self.t, self.jpeg, self.w, self.h, self.chegada = seq, t, jpeg, w, h, chegada


class _Chave:
    __slots__ = ('seq', 't', 'analysis', 'dets', 'employees', 'w', 'h')

    def __init__(self, seq, t, analysis, dets, employees, w, h):
        self.seq, self.t, self.analysis, self.dets, self.employees, self.w, self.h = \
            seq, t, analysis, dets, employees, w, h


class _Taxa:
    """Eventos por segundo numa janela deslizante de 2 s."""

    def __init__(self):
        self.ts = deque(maxlen=512)

    def marcar(self, agora):
        self.ts.append(agora)

    def valor(self, agora):
        while self.ts and agora - self.ts[0] > 2.0:
            self.ts.popleft()
        return round(len(self.ts) / 2.0, 1)


class Assinante:
    """Fila de saida de um cliente (camera, painel ou envio por HTTP)."""

    def __init__(self, video=False, maxlen=240):
        self.video = video
        self.maxlen = maxlen
        self.q = deque()
        self.cond = threading.Condition()
        self.fechado = False
        self.descartados = 0
        self.usado_em = time.perf_counter()

    def empurrar(self, item):
        with self.cond:
            if len(self.q) >= self.maxlen:
                self.q.popleft()
                self.descartados += 1
            self.q.append(item)
            self.cond.notify()

    def pegar(self, timeout=0.5):
        """Tudo o que estiver na fila (espera ate timeout se estiver vazia)."""
        with self.cond:
            if not self.q and not self.fechado:
                self.cond.wait(timeout)
            itens = list(self.q)
            self.q.clear()
            self.usado_em = time.perf_counter()
            return itens

    def fechar(self):
        with self.cond:
            self.fechado = True
            self.cond.notify_all()


# ── Pipeline ────────────────────────────────────────────────────────
class LivePipeline:
    OCIOSO_RESET_S = 30.0     # sem quadros por este tempo: esquece rastreio e linha do tempo
    SEM_SINAL_S = 2.0
    ATRASO_TOLERADO_S = 0.4   # quadro que ja passou da hora por mais que isto e pulado
    LIMITE_QUADROS = 720

    def __init__(self, analisar, reiniciar_rastreio=None, nome='', log=print):
        """analisar(img, t, perfil) -> (analysis, deteccoes, funcionarios)."""
        self.analisar = analisar
        self._reiniciar = reiniciar_rastreio or (lambda: None)
        self._reset_pendente = False
        self.nome = nome
        self.log = log
        self.cond = threading.Condition()
        self.modo = MODO_PADRAO
        self.perfil = MODOS[self.modo]
        self.rodando = False
        self.threads = []
        self.assinantes = set()
        self.http = {}  # sessao -> Assinante
        self._zerar_linha_do_tempo()
        self.ultimo_resultado = None
        self.ultimo_jpeg = None
        self.exibidos = 0
        self.taxa_entrada, self.taxa_analise, self.taxa_saida = _Taxa(), _Taxa(), _Taxa()
        self.pulados = 0
        self.proc_s = 0.05
        self.ultima_entrada = 0.0

    # ── estado ──────────────────────────────────────────────────────
    def _zerar_linha_do_tempo(self):
        self.geracao = getattr(self, 'geracao', 0) + 1
        self.quadros, self.ts = [], []
        self.chaves, self.chaves_t = [], []
        self.revisor = _Revisor(self.perfil)
        self.base = None            # horario_local - t (menor atraso de rede visto)
        self.base_alvo = None
        self.offsets = deque(maxlen=600)
        self.atraso = self.perfil['atraso']
        self.atraso_alvo = self.atraso
        self.lag_proc = deque(maxlen=60)
        self._ultimo_ajuste = 0.0
        self._reset_pendente = True  # rastreio antigo nao vale para a nova linha do tempo
        self.ultima_chave_t = -1e18
        self.intervalo = 0.0
        self.ultimo_exibido_t = -1e18
        self.ultimo_exibido_em = 0.0
        self.ultima_exibicao = 0.0
        self.sessao = None
        self.seq_recebidos = {}     # sessao -> maior seq recebido
        self.recentes = deque(maxlen=900)  # itens exibidos, para quem reconectar
        self.ultimo_t_entrada = None
        self.relogio = time.perf_counter()

    def configurar(self, modo):
        modo = modo if modo in MODOS else MODO_PADRAO
        with self.cond:
            if modo == self.modo:
                return
            self.modo, self.perfil = modo, MODOS[modo]
            sessao, recebidos = self.sessao, self.seq_recebidos
            self._zerar_linha_do_tempo()
            self.sessao, self.seq_recebidos = sessao, recebidos
            self.cond.notify_all()
        self.log(f"modo {modo}: atraso {self.perfil['atraso']} s")

    def iniciar(self):
        with self.cond:
            if self.rodando:
                return
            self.rodando = True
        self.threads = [threading.Thread(target=self._analise, daemon=True, name=f'analise-{self.nome}'),
                        threading.Thread(target=self._exibicao, daemon=True, name=f'exibicao-{self.nome}')]
        for th in self.threads:
            th.start()

    def parar(self):
        with self.cond:
            self.rodando = False
            self.cond.notify_all()
        for th in self.threads:
            if th.is_alive() and th is not threading.current_thread():
                th.join(timeout=3)
        self.threads = []

    def limpar(self):
        """Camera desligada: descarta o que estava no buffer e o ultimo resultado."""
        with self.cond:
            self._zerar_linha_do_tempo()
            self.ultimo_resultado = None
            self.ultimo_jpeg = None
            self.cond.notify_all()

    def fechar(self):
        self.parar()
        with self.cond:
            subs = list(self.assinantes) + list(self.http.values())
            self.assinantes.clear()
            self.http.clear()
        for s in subs:
            s.fechar()

    # ── entrada ─────────────────────────────────────────────────────
    def receber(self, seq, t, jpeg, sessao=None, w=None, h=None):
        """Um quadro JPEG. t = horario de captura em segundos (None = usa a hora de chegada)."""
        agora = time.perf_counter()
        if not jpeg:
            return False
        if w is None or h is None:
            w, h = jpeg_size(jpeg)
        with self.cond:
            if t is None:
                t, sessao = agora, sessao or 'servidor'
            if seq is None:  # fonte do proprio servidor: numera aqui para o painel parear quadro e resultado
                self._seq_local = (getattr(self, '_seq_local', -1) + 1) & 0xFFFFFFFF
                seq = self._seq_local
            sessao = sessao or 'anonima'
            if sessao != self.sessao or (self.ultimo_t_entrada is not None and t < self.ultimo_t_entrada - 5.0):
                # outra pagina/aparelho (ou relogio reiniciado): nova linha do tempo
                recebidos = self.seq_recebidos
                self._zerar_linha_do_tempo()
                self.seq_recebidos = recebidos
                self.sessao = sessao
                self.cond.notify_all()
            if seq is not None:
                anterior = self.seq_recebidos.get(sessao, -1)
                self.seq_recebidos[sessao] = max(anterior, seq)
                if len(self.seq_recebidos) > 16:
                    self.seq_recebidos.pop(next(iter(self.seq_recebidos)))
            self.ultima_entrada = agora
            self.taxa_entrada.marcar(agora)
            self.ultimo_t_entrada = t if self.ultimo_t_entrada is None else max(self.ultimo_t_entrada, t)
            if t <= self.ultimo_exibido_t:
                self.pulados += 1   # chegou depois da hora de exibir
                return False
            i = bisect_left(self.ts, t)
            if i < len(self.ts) and self.ts[i] == t:
                return False        # reenvio de um quadro que ja esta no buffer
            offset = agora - t
            self.offsets.append((agora, offset))
            if self.base is None:
                self.base = self.base_alvo = offset
            elif offset < self.base_alvo:
                self.base_alvo = offset
            self.quadros.insert(i, _Quadro(seq, t, jpeg, w, h, agora))
            self.ts.insert(i, t)
            if len(self.quadros) > self.LIMITE_QUADROS:
                self.quadros.pop(0)
                self.ts.pop(0)
                self.pulados += 1
            self.cond.notify_all()
        return True

    def receber_imagem(self, img, t=None, qualidade=80, largura_max=1280):
        """Quadro de uma fonte do proprio servidor (RTSP, HTTP, webcam, arquivo)."""
        h, w = img.shape[:2]
        if w > largura_max:
            s = largura_max / w
            img = cv2.resize(img, (int(w * s) // 2 * 2, int(h * s) // 2 * 2), interpolation=cv2.INTER_AREA)
            h, w = img.shape[:2]
        ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, qualidade])
        if ok:
            self.receber(None, t, buf.tobytes(), sessao='servidor', w=w, h=h)

    def assinar_retomando(self, sessao, ultimo_resultado, video=False, maxlen=600):
        """Assina e ja coloca na fila os resultados exibidos enquanto o cliente estava fora."""
        s = Assinante(video, maxlen)
        with self.cond:
            if sessao == self.sessao and ultimo_resultado is not None:
                for item in self.recentes:
                    if item[0] is not None and item[0] > ultimo_resultado:
                        s.empurrar(item)
            self.assinantes.add(s)
        return s

    def ultimo_seq(self, sessao):
        with self.cond:
            return self.seq_recebidos.get(sessao, -1)

    def janela_reenvio(self):
        return round(self.perfil['atraso_max'] + 0.5, 2)

    # ── assinaturas ─────────────────────────────────────────────────
    def assinar(self, video=False, maxlen=240):
        s = Assinante(video, maxlen)
        with self.cond:
            self.assinantes.add(s)
        return s

    def cancelar(self, s):
        with self.cond:
            self.assinantes.discard(s)
        s.fechar()

    def assinante_http(self, sessao):
        agora = time.perf_counter()
        with self.cond:
            for k in [k for k, v in self.http.items() if agora - v.usado_em > 20]:
                self.http.pop(k).fechar()
            s = self.http.get(sessao)
            if s is None:
                s = self.http[sessao] = Assinante(False, 600)
            s.usado_em = agora
            return s

    # ── relogio de exibicao ─────────────────────────────────────────
    def _p90(self, valores):
        if not valores:
            return 0.0
        v = sorted(valores)
        return v[min(len(v) - 1, int(len(v) * 0.9))]

    def _ajustar_relogio(self, agora):
        if agora - self._ultimo_ajuste < 0.1:
            return
        self._ultimo_ajuste = agora
        dt = min(0.25, max(0.0, agora - self.relogio))
        self.relogio = agora
        if self.base is None:
            return
        recentes = [o for ts, o in self.offsets if agora - ts <= 8.0]
        if recentes:
            self.base_alvo = min(recentes)
        # a base anda devagar: mudancas bruscas fariam a imagem pular ou congelar
        passo = 0.03 * dt if abs(self.base_alvo - self.base) < 0.5 else abs(self.base_alvo - self.base)
        self.base += max(-passo, min(passo, self.base_alvo - self.base))
        jitter = self._p90([o - self.base_alvo for ts, o in self.offsets if agora - ts <= 5.0])
        alvo = self.perfil['atraso'] + min(jitter, 2.0) + max(0.0, self.base_alvo - self.base)
        if self.perfil['escolha'] == 'recente' and self.lag_proc:
            alvo = max(alvo, self._p90(list(self.lag_proc)) + 0.05)
        self.atraso_alvo = max(self.perfil['atraso'], min(self.perfil['atraso_max'], alvo))
        subir, descer = 0.12 * dt, 0.04 * dt
        self.atraso += max(-descer, min(subir, self.atraso_alvo - self.atraso))

    def _hora(self, t):
        return t + self.base + self.atraso

    # ── analise ─────────────────────────────────────────────────────
    def _escolher_chave(self, agora):
        i0 = bisect_right(self.ts, self.ultima_chave_t)
        n = len(self.quadros)
        if i0 >= n or self.base is None:
            return None
        if self.perfil['escolha'] == 'recente':
            return self.quadros[-1]
        folga = self.proc_s * 1.3 + 0.03
        alvo = self.ultima_chave_t + self.intervalo
        i = max(i0, bisect_left(self.ts, alvo))
        if i >= n:
            # nao chegou quadro depois do intervalo; se o mais novo esta ficando velho, usa ele
            ultimo = self.quadros[-1]
            return ultimo if self._hora(ultimo.t) - agora < folga * 2 else None
        q = self.quadros[i]
        while self._hora(q.t) - agora < folga and i + 1 < n:  # nao daria tempo: pula para frente
            i = max(i + 1, bisect_left(self.ts, q.t + self.intervalo))
            if i >= n:
                i = n - 1
            q = self.quadros[i]
        atraso_fila = self.ts[-1] - q.t
        if atraso_fila > 0.5 * self.atraso:
            self.intervalo = min(self.perfil['intervalo_max'], self.intervalo * 1.25 + 0.004)
        elif atraso_fila < 0.25 * self.atraso:
            self.intervalo = max(0.0, self.intervalo * 0.85 - 0.002)
        return q

    def _analise(self):
        while True:
            with self.cond:
                if not self.rodando:
                    return
                q = self._escolher_chave(time.perf_counter())
                if q is None:
                    self.cond.wait(0.05)
                    continue
                self.ultima_chave_t = q.t
                perfil, geracao, base = self.perfil, self.geracao, self.base
                reset, self._reset_pendente = self._reset_pendente, False
            try:
                if reset:
                    self._reiniciar()
                img = cv2.imdecode(np.frombuffer(q.jpeg, np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    continue
                t0 = time.perf_counter()
                analysis, dets, employees = self.analisar(img, q.t, perfil)
                fim = time.perf_counter()
            except Exception as ex:  # nunca derruba o stream por causa de um quadro
                self.log(f"erro na analise: {ex}")
                time.sleep(0.2)
                continue
            with self.cond:
                if geracao != self.geracao:
                    continue
                self.proc_s = 0.8 * self.proc_s + 0.2 * (fim - t0)
                self.lag_proc.append(fim - (q.t + base))
                chave = _Chave(q.seq, q.t, analysis, dets, employees, img.shape[1], img.shape[0])
                j = bisect_left(self.chaves_t, q.t)
                self.chaves.insert(j, chave)
                self.chaves_t.insert(j, q.t)
                self.revisor.adicionar(q.t, analysis)
                self.taxa_analise.marcar(fim)
                self.cond.notify_all()

    # ── exibicao ────────────────────────────────────────────────────
    def _exibicao(self):
        while True:
            with self.cond:
                if not self.rodando:
                    return
                agora = time.perf_counter()
                self._ajustar_relogio(agora)
                if not self.quadros:
                    if self.ultima_entrada and agora - self.ultima_entrada > self.OCIOSO_RESET_S:
                        self.ultima_entrada = 0.0
                        self._zerar_linha_do_tempo()
                    self.cond.wait(0.1)
                    continue
                q = self.quadros[0]
                hora = self._hora(q.t)
                if hora > agora:
                    self.cond.wait(min(0.05, hora - agora))
                    continue
                self.quadros.pop(0)
                self.ts.pop(0)
                atrasado = agora - hora > self.ATRASO_TOLERADO_S
                if atrasado and self.quadros and agora - self.ultima_exibicao < 0.1:
                    self.pulados += 1   # recuperando de uma travada: pula ate alcancar
                    continue
                self.ultimo_exibido_t = q.t
                j = bisect_right(self.chaves_t, q.t)
                ka = self.chaves[j - 1] if j > 0 else None
                kb = self.chaves[j] if j < len(self.chaves) else None
                if j > 1:  # chaves anteriores a ka nao servem mais
                    del self.chaves[:j - 1]
                    del self.chaves_t[:j - 1]
                perfil, revisor, atraso, geracao = self.perfil, self.revisor, self.atraso, self.geracao
                self.ultima_exibicao = agora
            try:
                resultado = self._montar(q, ka, kb, perfil, revisor, atraso)
            except Exception as ex:
                self.log(f"erro ao montar quadro: {ex}")
                continue
            texto = json.dumps({'type': 'quadro', **resultado}, ensure_ascii=False, separators=(',', ':'))
            item = (q.seq, q.t, texto, q.jpeg, resultado)
            with self.cond:
                if geracao != self.geracao:
                    continue
                self.recentes.append(item)
                self.ultimo_resultado = resultado
                self.ultimo_jpeg = q.jpeg
                self.exibidos += 1
                self.taxa_saida.marcar(agora)
                subs = list(self.assinantes) + list(self.http.values())
                self.cond.notify_all()
            for s in subs:
                s.empurrar(item)

    def _montar(self, q, ka, kb, perfil, revisor, atraso):
        agora = time.perf_counter()
        perto = ka if (kb is None or (ka is not None and q.t - ka.t <= kb.t - q.t)) else kb
        if ka is not None and kb is not None and kb.t > ka.t:
            f = (q.t - ka.t) / (kb.t - ka.t)
            persons = _interp_persons(ka.analysis.get('persons', []), kb.analysis.get('persons', []), f)
            dets = _interp_dets(ka.dets, kb.dets, f)
        elif perto is not None:
            persons = [_copy_person(p) for p in perto.analysis.get('persons', [])]
            dets = list(perto.dets)
        else:
            persons, dets = [], []
        if perto is not None and perto.analysis.get('_sem_pose'):
            resumo = {k: v for k, v in perto.analysis.items() if not k.startswith('_')}
        elif perto is None:
            resumo = {'persons': [], 'status': 'verificando', 'missing_items': [], 'missing': [], 'alerts': []}
        else:
            resumo = summarize(revisor.aplicar(persons, q.t))
        w, h = (q.w, q.h) if q.w else ((perto.w, perto.h) if perto else (0, 0))
        if perto is not None and (perto.w, perto.h) != (w, h) and perto.w:
            _escalar(resumo, dets, w / perto.w, h / perto.h)
        return {
            **resumo,
            'seq': q.seq,
            't': round(q.t, 4),
            'frame_w': w,
            'frame_h': h,
            'detections': [public_detection(d) for d in dets],
            'employees': list(perto.employees) if perto else [],
            'activity': activity_text(resumo) if perto else 'aguardando análise',
            'modo': self.modo,
            'quadro_chave': perto is not None and perto.seq == q.seq and perto.t == q.t,
            'estimado': kb is None,
            'fps': self.taxa_analise.valor(agora),
            'fps_exibido': self.taxa_saida.valor(agora),
            'latency_ms': round(atraso * 1000),
            'atraso_ms': round(atraso * 1000),
        }

    # ── leitura ─────────────────────────────────────────────────────
    def estatisticas(self):
        agora = time.perf_counter()
        with self.cond:
            return {
                'modo': self.modo,
                'modo_nome': self.perfil['nome'],
                'atraso_ms': round(self.atraso * 1000),
                'fps_entrada': self.taxa_entrada.valor(agora),
                'fps_analise': self.taxa_analise.valor(agora),
                'fps_exibido': self.taxa_saida.valor(agora),
                'buffer': len(self.quadros),
                'pulados': self.pulados,
                'intervalo_chave_ms': round(self.intervalo * 1000),
                'sem_sinal': bool(self.ultima_entrada) and agora - self.ultima_entrada > self.SEM_SINAL_S,
                'ultima_entrada_s': round(agora - self.ultima_entrada, 1) if self.ultima_entrada else None,
            }

    def esperar_saida(self, ultimo, timeout=1.0):
        with self.cond:
            if self.exibidos == ultimo and self.rodando:
                self.cond.wait(timeout)
            return self.exibidos, self.ultimo_resultado, self.ultimo_jpeg


def _escalar(resumo, dets, sx, sy):
    """Quadro exibido com tamanho diferente do quadro-chave (a resolucao mudou no meio)."""
    for p in resumo.get('persons', []):
        x1, y1, x2, y2 = p['bbox']
        p['bbox'] = [x1 * sx, y1 * sy, x2 * sx, y2 * sy]
        p['keypoints'] = [[x * sx, y * sy, c] for x, y, c in p.get('keypoints', [])]
    for i, d in enumerate(dets):
        x1, y1, x2, y2 = d['box']
        dets[i] = {**d, 'box': (x1 * sx, y1 * sy, x2 * sx, y2 * sy)}
