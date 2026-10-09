"""
avisos.py - Avisos para a TV: sirene e voz quando alguem fica sem EPI (ou cai).

A TV e uma pagina (tv.html) aberta com um link criado em Dispositivos. Ela fica conectada ao
servidor por WebSocket (/ws/tv). Quando a camera confirma uma falta, o processador chama
violacao(): daqui sai um aviso por pessoa (nao um por EPI), com a frase pronta e o audio dela
sendo sintetizado (voz.py). A TV toca a sirene, fala a frase e devolve o volume em que falou;
isso fica gravado na linha da falta (auditoria.avisos) e aparece no desempenho do funcionario.

Enquanto a pessoa continuar sem o EPI o aviso repete a cada REPETIR_S; se faltar mais um EPI,
avisa de novo na hora. Sem TV conectada nada e sintetizado nem gravado.
"""
import os
import threading
import time
import uuid
from collections import deque

import auditoria
import ppe_taxonomy as tax
import voz

REPETIR_S = float(os.environ.get('ARGOS_AVISO_REPETIR_S', '30') or 30)
ESQUECER_S = 25.0          # pessoa sem falta por este tempo: o proximo aviso volta a ser "o primeiro"
VALIDADE_S = 900.0         # tempo em que a TV ainda pode confirmar que falou

_lock = threading.Lock()
_tvs = {}                  # uid -> {id da conexao: _Tv}
_pessoas = {}              # (uid, stream, chave) -> {'itens': set, 'ultimo': t, 'vezes': n, 'visto': t}
_pendentes = {}            # id do aviso -> {'uid', 'rowids', 'frase', 'em'}
_recentes = {}             # uid -> deque dos ultimos avisos (a TV que acabou de abrir mostra o que esta valendo)


class _Tv:
    def __init__(self, uid, nome, token):
        self.id = uuid.uuid4().hex[:12]
        self.uid, self.nome, self.token = uid, nome or 'TV', token
        self.fila = deque(maxlen=50)
        self.cond = threading.Condition()
        self.fechada = False
        self.volume = None
        self.som = False        # a pagina so toca depois de alguem tocar em "Ativar som"
        self.desde = time.time()

    def empurrar(self, item):
        with self.cond:
            self.fila.append(item)
            self.cond.notify()

    def pegar(self, timeout=1.0):
        with self.cond:
            if not self.fila and not self.fechada:
                self.cond.wait(timeout)
            itens = list(self.fila)
            self.fila.clear()
            return itens


def conectar(uid, nome, token):
    tv = _Tv(uid, nome, token)
    with _lock:
        primeira = not _tvs.get(uid)
        _tvs.setdefault(uid, {})[tv.id] = tv
    if primeira:
        voz.preparar()
    return tv


def desconectar(tv):
    with _lock:
        d = _tvs.get(tv.uid) or {}
        d.pop(tv.id, None)
        if not d:
            _tvs.pop(tv.uid, None)
    with tv.cond:
        tv.fechada = True
        tv.cond.notify_all()


def tvs(uid) -> list:
    with _lock:
        return [{'nome': t.nome, 'token': t.token, 'volume': t.volume, 'som': t.som, 'desde': t.desde}
                for t in (_tvs.get(uid) or {}).values()]


def tem_tv(uid) -> bool:
    with _lock:
        return bool(_tvs.get(uid))


def _enviar(uid, aviso):
    with _lock:
        alvos = list((_tvs.get(uid) or {}).values())
        _recentes.setdefault(uid, deque(maxlen=20)).append(aviso)
    for tv in alvos:
        tv.empurrar(aviso)
    return len(alvos)


def violacao(uid, stream_id, chave, *, func_id=None, func_nome=None, itens=(), area='', rowids=(),
             queda=False, t=None):
    """Chamar a cada quadro-chave em que a pessoa esta com falta confirmada (ou caida)."""
    if not uid or not (itens or queda):
        return None
    t = time.time() if t is None else t
    itens = [i for i in itens if i]
    k = (uid, stream_id, str(chave))
    with _lock:
        if not _tvs.get(uid):
            _pessoas.pop(k, None)       # sem TV: quando uma abrir, o aviso sai como o primeiro
            return None
        est = _pessoas.get(k)
        if est is not None and t - est['visto'] > ESQUECER_S:
            est = None
        if est is None:
            est = _pessoas[k] = {'itens': set(), 'ultimo': 0.0, 'vezes': 0, 'visto': t, 'queda': False}
        est['visto'] = t
        novos = set(itens) - est['itens'] or (queda and not est['queda'])
        if not novos and t - est['ultimo'] < REPETIR_S:
            return None
        repeticao = 0 if novos else est['vezes']
        est['itens'] |= set(itens)
        est['queda'] = est['queda'] or queda
        est['ultimo'] = t
        est['vezes'] += 1
        if len(_pessoas) > 400:
            for velho in [c for c, v in _pessoas.items() if t - v['visto'] > 120]:
                _pessoas.pop(velho, None)
    texto = voz.frase(func_nome, itens, area, repeticao=repeticao, queda=queda and not itens)
    aviso = {
        'type': 'alerta', 'id': uuid.uuid4().hex[:16], 'ts': t,
        'tipo': 'queda' if queda and not itens else 'epi',
        'nome': func_nome or '', 'func_id': func_id or '', 'itens': list(itens),
        'rotulos': [tax.item_label(i) for i in itens], 'area': area or '', 'camera': stream_id,
        'frase': texto, 'audio': voz.sintetizar_depois(texto) if texto else '', 'repeticao': repeticao,
    }
    with _lock:
        _pendentes[aviso['id']] = {'uid': uid, 'rowids': [r for r in rowids if r], 'frase': texto, 'em': t}
        for velho in [a for a, v in _pendentes.items() if t - v['em'] > VALIDADE_S]:
            _pendentes.pop(velho, None)
    _enviar(uid, aviso)
    return aviso


def resolveu(uid, stream_id, chave):
    """A pessoa colocou o EPI (ou saiu de cena): a TV pode tirar o aviso dela da tela."""
    k = (uid, stream_id, str(chave))
    with _lock:
        est = _pessoas.pop(k, None)
        alvos = list((_tvs.get(uid) or {}).values()) if est else []
    for tv in alvos:
        tv.empurrar({'type': 'resolvido', 'camera': stream_id, 'chave': str(chave)})


def recentes(uid, segundos=20.0) -> list:
    agora = time.time()
    with _lock:
        return [a for a in (_recentes.get(uid) or ()) if agora - a['ts'] <= segundos]


def falou(uid, aviso_id, tv_nome, volume, tocou=True, sirene=True):
    """A TV confirmou que tocou o aviso: grava na falta (aparece no desempenho do funcionario)."""
    with _lock:
        p = _pendentes.get(str(aviso_id or ''))
        if not p or p['uid'] != uid:
            return False
        ja = p.setdefault('tvs', set())
        if tv_nome in ja:
            return True
        ja.add(tv_nome)
    try:
        volume = max(0, min(100, int(round(float(volume)))))
    except (TypeError, ValueError):
        volume = None
    registro = {'ts': round(time.time(), 1), 'frase': p['frase'], 'tv': str(tv_nome or 'TV')[:60],
                'volume': volume, 'falou': bool(tocou), 'sirene': bool(sirene)}
    if p['rowids']:
        try:
            auditoria.registrar_aviso(uid, p['rowids'], registro)
        except Exception as e:
            print(f'[avisos] nao gravou o aviso na auditoria: {e}')
    return True
