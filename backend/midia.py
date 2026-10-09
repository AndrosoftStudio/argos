"""
midia.py - Programas auxiliares que ficam na pasta midia/: o MediaMTX (video direto por WebRTC)
e o Piper com a voz em portugues (avisos falados na TV).

No Argos EPI Servidor (programa compilado) o instalador ja traz a pasta pronta. Rodando do
codigo (iniciar.bat, iniciar.sh, Docker), o que faltar e baixado aqui, uma vez, em segundo
plano; enquanto nao chega, o video segue pelo caminho antigo e a TV toca so a sirene.

  midia/mediamtx/mediamtx(.exe)
  midia/piper/piper(.exe)  + espeak-ng-data
  midia/voz/pt_BR-faber-medium.onnx (+ .json)

ARGOS_MIDIA_BAIXAR=0 desliga o download automatico.
"""
import os
import shutil
import subprocess
import tarfile
import threading
import time
import zipfile

import requests

import pastas

PASTA = os.path.join(pastas.RAIZ, 'midia')
WIN = os.name == 'nt'
EXE = '.exe' if WIN else ''
VERSAO_MEDIAMTX = 'v1.21.2'
VOZ = 'pt_BR-faber-medium'
_GH = 'https://github.com/'
_HF = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/'

ITENS = {
    'mediamtx': {
        'arquivo': os.path.join('mediamtx', 'mediamtx' + EXE),
        'baixar': [(_GH + f'bluenviron/mediamtx/releases/download/{VERSAO_MEDIAMTX}/mediamtx_{VERSAO_MEDIAMTX}_'
                    + ('windows_amd64.zip' if WIN else 'linux_amd64.tar.gz'), 'mediamtx', True)],
        'nome': 'vídeo direto (MediaMTX)',
    },
    'piper': {
        'arquivo': os.path.join('piper', 'piper' + EXE),
        # o pacote ja traz uma pasta "piper" dentro
        'baixar': [(_GH + 'rhasspy/piper/releases/download/2023.11.14-2/'
                    + ('piper_windows_amd64.zip' if WIN else 'piper_linux_x86_64.tar.gz'), '', True)],
        'nome': 'voz (Piper)',
    },
    'voz': {
        'arquivo': os.path.join('voz', VOZ + '.onnx'),
        'baixar': [(_HF + VOZ + '.onnx.json', os.path.join('voz', VOZ + '.onnx.json'), False),
                   (_HF + VOZ + '.onnx', os.path.join('voz', VOZ + '.onnx'), False)],
        'nome': 'voz em português',
    },
}

_lock = threading.Lock()
_estado = {}          # nome -> {'status': baixando|falhou, 'erro': str, 'em': float}


def caminho(nome):
    """Caminho do programa/arquivo, ou None se ainda nao esta na pasta.
    ARGOS_MEDIAMTX / ARGOS_PIPER / ARGOS_VOZ apontam para um arquivo em outro lugar."""
    fora = os.environ.get('ARGOS_' + nome.upper(), '').strip()
    if fora:
        return fora if os.path.isfile(fora) else None
    alvo = os.path.join(PASTA, ITENS[nome]['arquivo'])
    return alvo if os.path.isfile(alvo) else None


def estado() -> dict:
    with _lock:
        baixando = {k: dict(v) for k, v in _estado.items()}
    saida = {}
    for nome in ITENS:
        if caminho(nome):
            saida[nome] = {'status': 'pronto'}
        else:
            saida[nome] = baixando.get(nome) or {'status': 'ausente'}
    return saida


def _pode_baixar() -> bool:
    return os.environ.get('ARGOS_MIDIA_BAIXAR', '1').strip().lower() not in ('0', 'false', 'nao', 'não', 'off')


def _baixar_arquivo(url, destino):
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    tmp = destino + '.baixando'
    with requests.get(url, stream=True, timeout=60, headers={'User-Agent': 'ArgosEPI-Servidor'}) as r:
        r.raise_for_status()
        with open(tmp, 'wb') as f:
            for bloco in r.iter_content(1 << 20):
                f.write(bloco)
    os.replace(tmp, destino)


def _extrair(pacote, destino):
    os.makedirs(destino, exist_ok=True)
    base = os.path.realpath(destino)
    if pacote.endswith('.zip'):
        with zipfile.ZipFile(pacote) as z:
            for n in z.namelist():
                if not os.path.realpath(os.path.join(destino, n)).startswith(base):
                    raise RuntimeError('pacote com caminho invalido')
            z.extractall(destino)
    else:
        with tarfile.open(pacote) as t:
            for m in t.getmembers():
                if not os.path.realpath(os.path.join(destino, m.name)).startswith(base):
                    raise RuntimeError('pacote com caminho invalido')
            t.extractall(destino)


def _baixar(nome):
    item = ITENS[nome]
    try:
        for url, onde, pacote in item['baixar']:
            if pacote:
                tmp = os.path.join(PASTA, '_' + nome + ('.zip' if url.endswith('.zip') else '.tar.gz'))
                _baixar_arquivo(url, tmp)
                _extrair(tmp, os.path.join(PASTA, onde))
                os.remove(tmp)
            else:
                _baixar_arquivo(url, os.path.join(PASTA, onde))
        alvo = os.path.join(PASTA, item['arquivo'])
        if not os.path.isfile(alvo):
            raise RuntimeError('o pacote baixado nao trouxe ' + item['arquivo'])
        if not WIN:
            os.chmod(alvo, 0o755)
        with _lock:
            _estado.pop(nome, None)
        print(f"[midia] {item['nome']}: pronto")
    except Exception as e:
        with _lock:
            _estado[nome] = {'status': 'falhou', 'erro': str(e)[:200], 'em': time.time()}
        print(f"[midia] nao foi possivel baixar {item['nome']}: {e}")


def garantir(*nomes) -> bool:
    """True se tudo ja esta na pasta. O que falta comeca a baixar em segundo plano (uma vez;
    depois de uma falha, tenta de novo so apos 10 minutos)."""
    falta = [n for n in nomes if not caminho(n)]
    if not falta:
        return True
    if not _pode_baixar():
        return False
    for nome in falta:
        with _lock:
            atual = _estado.get(nome)
            if atual and (atual['status'] == 'baixando' or time.time() - atual.get('em', 0) < 600):
                continue
            _estado[nome] = {'status': 'baixando', 'erro': '', 'em': time.time()}
        print(f"[midia] baixando {ITENS[nome]['nome']}...")
        threading.Thread(target=_baixar, args=(nome,), daemon=True, name=f'midia-{nome}').start()
    return False


_trabalho = None


def abrir(comando, **kw):
    """Abre um programa auxiliar que morre junto com o servidor (sem janela no Windows).

    No Windows o processo entra num "job" que fecha tudo quando o servidor termina, mesmo se
    ele for encerrado a forca; no Linux o kernel avisa o filho quando o pai morre."""
    global _trabalho
    if WIN:
        import ctypes
        from ctypes import wintypes
        kw['creationflags'] = kw.get('creationflags', 0) | 0x08000000      # CREATE_NO_WINDOW
        proc = subprocess.Popen(comando, **kw)
        try:
            k32 = ctypes.WinDLL('kernel32', use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            if _trabalho is None:
                class _Basico(ctypes.Structure):
                    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                                ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                                ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                                ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                                ('SchedulingClass', wintypes.DWORD)]

                class _Estendido(ctypes.Structure):
                    _fields_ = [('Basico', _Basico), ('Io', ctypes.c_uint64 * 6), ('ProcessMemoryLimit', ctypes.c_size_t),
                                ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t),
                                ('PeakJobMemoryUsed', ctypes.c_size_t)]
                job = k32.CreateJobObjectW(None, None)
                info = _Estendido()
                info.Basico.LimitFlags = 0x2000                             # KILL_ON_JOB_CLOSE
                if job and k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                    _trabalho = job
            if _trabalho:
                h = k32.OpenProcess(0x0101, False, proc.pid)                # SET_QUOTA | TERMINATE
                if h:
                    k32.AssignProcessToJobObject(_trabalho, h)
                    k32.CloseHandle(wintypes.HANDLE(h))
        except Exception:
            pass
        return proc

    def _morrer_com_o_pai():
        try:
            import ctypes
            ctypes.CDLL('libc.so.6').prctl(1, 9)                            # PR_SET_PDEATHSIG, SIGKILL
        except Exception:
            pass
    return subprocess.Popen(comando, preexec_fn=_morrer_com_o_pai, **kw)


def limpar_restos():
    """Sobras de um download interrompido."""
    if not os.path.isdir(PASTA):
        return
    for raiz, _d, arquivos in os.walk(PASTA):
        for a in arquivos:
            if a.endswith('.baixando') or (raiz == PASTA and a.startswith('_')):
                try:
                    os.remove(os.path.join(raiz, a))
                except OSError:
                    pass
    shutil.rmtree(os.path.join(PASTA, 'pkgconfig'), ignore_errors=True)
