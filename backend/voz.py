"""
voz.py - A voz dos avisos: transforma a frase em audio (WAV) com o Piper, um sintetizador
leve que roda no processador (meio segundo para uma frase de cinco).

O Piper fica aberto em segundo plano com a voz carregada; cada frase vira um arquivo em
dados/voz_cache/ com o nome tirado do proprio texto, entao frase repetida ("Fulano, coloque o
capacete") sai do disco na hora. Os nomes dos funcionarios e dos EPIs ja cadastrados sao
aquecidos quando a primeira TV se conecta.
"""
import hashlib
import json
import os
import subprocess
import threading
import time

import midia
import pastas
import ppe_taxonomy as tax

CACHE = os.path.join(pastas.RAIZ, 'dados', 'voz_cache')
MAX_ARQUIVOS = 600
MAX_TEXTO = 400
MAX_EPIS_FALADOS = 3

# Como cada EPI entra na frase ("coloque o capacete e os oculos de protecao")
COM_ARTIGO = {
    'capacete': 'o capacete', 'colete': 'o colete refletivo', 'luvas': 'as luvas',
    'botas': 'o calçado de segurança', 'oculos': 'os óculos de proteção', 'mascara': 'a máscara',
    'protetor_auricular': 'o protetor auricular', 'protetor_facial': 'o protetor facial',
    'vestimenta': 'a vestimenta de proteção',
}
SEM_ARTIGO = {k: v.split(' ', 1)[1] for k, v in COM_ARTIGO.items()}


def _lista(partes) -> str:
    partes = [p for p in partes if p]
    if len(partes) <= 1:
        return ''.join(partes)
    return ', '.join(partes[:-1]) + ' e ' + partes[-1]


def nome_epi(item, artigo=True) -> str:
    tabela = COM_ARTIGO if artigo else SEM_ARTIGO
    return tabela.get(item) or tax.item_label(item).lower()


def frase(nome, itens, area='', repeticao=0, queda=False) -> str:
    """A frase que a TV fala. repeticao > 0: a pessoa ja foi avisada e continua sem o EPI."""
    nome = ' '.join(str(nome or '').replace('_', ' ').split())[:80]
    area = ' '.join(str(area or '').split())[:60]
    onde = f', na área {area}' if area else ''
    if queda:
        quem = f'de {nome}' if nome else 'de uma pessoa'
        return f'Atenção! Possível queda {quem}{onde}. Verifiquem agora, por favor.'
    itens = list(itens)
    if not itens:
        return ''
    # frase curta: fala ate tres EPIs pelo nome e resume o resto
    ditos, resto = (itens, 0) if len(itens) <= MAX_EPIS_FALADOS else (itens[:MAX_EPIS_FALADOS - 1], len(itens) - MAX_EPIS_FALADOS + 1)
    com = _lista([nome_epi(i) for i in ditos] + ([f'os outros {resto} equipamentos'] if resto else []))
    if nome:
        if repeticao:
            return f'{nome}, você continua sem {com}{onde}. Coloque agora, por favor.'
        return f'{nome}, por favor, coloque {com}{onde}.'
    sem = _lista(['sem ' + nome_epi(i, artigo=False) for i in ditos] + ([f'sem outros {resto} equipamentos'] if resto else []))
    if repeticao:
        return f'Atenção! Ainda há uma pessoa {sem}{onde}. Coloque o equipamento agora, por favor.'
    return f'Atenção! Pessoa {sem}{onde}. Por favor, coloque o equipamento.'


def id_do_texto(texto: str) -> str:
    return hashlib.sha1((midia.VOZ + '|' + texto).encode('utf-8')).hexdigest()[:24]


def arquivo(aid: str):
    """Caminho do audio ja pronto, ou None."""
    if not aid or not aid.isalnum():
        return None
    alvo = os.path.join(CACHE, aid + '.wav')
    return alvo if os.path.isfile(alvo) and os.path.getsize(alvo) > 44 else None


class _Piper:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.erro = ''

    def _abrir(self):
        if self.proc is not None and self.proc.poll() is None:
            return True
        exe, modelo = midia.caminho('piper'), midia.caminho('voz')
        if not exe or not modelo:
            self.erro = 'A voz ainda não está instalada neste servidor.'
            return False
        os.makedirs(CACHE, exist_ok=True)
        ambiente = dict(os.environ)
        if not midia.WIN:      # o piper do Linux traz as bibliotecas (.so) ao lado dele
            ambiente['LD_LIBRARY_PATH'] = os.path.dirname(exe) + os.pathsep + ambiente.get('LD_LIBRARY_PATH', '')
        try:
            self.proc = midia.abrir(
                [exe, '--model', modelo, '--json-input', '--output_dir', CACHE, '--sentence_silence', '0.15', '--quiet'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=os.path.dirname(exe),
                env=ambiente)
            self.erro = ''
            return True
        except OSError as e:
            self.proc = None
            self.erro = f'Não foi possível abrir a voz: {e}'
            return False

    def falar(self, texto, alvo) -> bool:
        with self.lock:
            for _tentativa in range(2):
                if not self._abrir():
                    return False
                tmp = alvo[:-4] + '.tmp.wav'
                try:
                    linha = json.dumps({'text': texto, 'output_file': tmp}, ensure_ascii=False) + '\n'
                    self.proc.stdin.write(linha.encode('utf-8'))
                    self.proc.stdin.flush()
                    saida = self.proc.stdout.readline()     # o piper responde com o caminho do arquivo
                    if saida and os.path.isfile(tmp) and os.path.getsize(tmp) > 44:
                        os.replace(tmp, alvo)
                        return True
                except (OSError, ValueError):
                    pass
                self.fechar_sem_trava()     # processo morreu: abre de novo e tenta mais uma vez
            self.erro = 'A voz não respondeu.'
            return False

    def fechar_sem_trava(self):
        p, self.proc = self.proc, None
        if p is not None:
            try:
                p.kill()
            except OSError:
                pass

    def fechar(self):
        with self.lock:
            self.fechar_sem_trava()


_piper = _Piper()
_fazendo = {}             # id -> Event (frase sendo sintetizada)
_fazendo_lock = threading.Lock()
_ultima_faxina = [0.0]


def disponivel() -> bool:
    return bool(midia.caminho('piper') and midia.caminho('voz'))


def preparar() -> bool:
    """Garante o Piper e a voz na pasta (baixa em segundo plano se faltar)."""
    return midia.garantir('piper', 'voz')


def situacao() -> dict:
    est = midia.estado()
    if disponivel():
        return {'status': 'pronta', 'voz': midia.VOZ, 'erro': _piper.erro}
    partes = [est['piper'], est['voz']]
    if any(p['status'] == 'baixando' for p in partes):
        return {'status': 'baixando', 'voz': midia.VOZ, 'erro': ''}
    erro = next((p.get('erro') for p in partes if p.get('erro')), '')
    return {'status': 'ausente', 'voz': midia.VOZ, 'erro': erro}


def _faxina():
    agora = time.time()
    if agora - _ultima_faxina[0] < 3600:
        return
    _ultima_faxina[0] = agora
    try:
        arquivos = [os.path.join(CACHE, a) for a in os.listdir(CACHE) if a.endswith('.wav')]
        if len(arquivos) > MAX_ARQUIVOS:
            arquivos.sort(key=os.path.getatime)
            for a in arquivos[:len(arquivos) - MAX_ARQUIVOS]:
                os.remove(a)
    except OSError:
        pass


def sintetizar(texto: str, esperar_s=8.0):
    """(id do audio, caminho ou None). Bloqueia ate o audio ficar pronto ou o tempo acabar."""
    texto = ' '.join(str(texto or '').split())[:MAX_TEXTO]
    if not texto:
        return '', None
    aid = id_do_texto(texto)
    pronto = arquivo(aid)
    if pronto:
        return aid, pronto
    if not preparar():
        return aid, None
    with _fazendo_lock:
        ev = _fazendo.get(aid)
        dono = ev is None
        if dono:
            ev = _fazendo[aid] = threading.Event()
    if dono:
        try:
            _faxina()
            _piper.falar(texto, os.path.join(CACHE, aid + '.wav'))
        finally:
            ev.set()
            with _fazendo_lock:
                _fazendo.pop(aid, None)
    else:
        ev.wait(esperar_s)
    return aid, arquivo(aid)


def sintetizar_depois(texto: str) -> str:
    """Comeca a sintetizar sem esperar. Devolve o id do audio (a TV busca por ele)."""
    texto = ' '.join(str(texto or '').split())[:MAX_TEXTO]
    if not texto:
        return ''
    aid = id_do_texto(texto)
    if not arquivo(aid):
        threading.Thread(target=sintetizar, args=(texto,), daemon=True, name='voz').start()
    return aid


def aquecer(nomes, itens=None):
    """Deixa prontos os avisos mais prováveis: cada funcionario com cada EPI comum sozinho."""
    if not preparar():
        return
    itens = list(itens or ['capacete', 'oculos', 'luvas', 'colete'])

    def _rodar():
        for n in list(nomes)[:60]:
            for i in itens[:4]:
                sintetizar(frase(n, [i]))
        for i in itens:
            sintetizar(frase('', [i]))
    threading.Thread(target=_rodar, daemon=True, name='voz-aquecer').start()


def fechar():
    _piper.fechar()
