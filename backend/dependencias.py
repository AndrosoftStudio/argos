"""
dependencias.py - Instala, pelo painel, o que falta para converter modelos em TensorRT.

O TensorRT e o onnx sao grandes (~2 GB) e so servem com GPU NVIDIA, por isso
nao vem no requirements.txt. O botao "Instalar dependencias" chama instalar():

  - No Docker: instala em /app/pylibs, um volume proprio (argosepi-pylibs). Assim
    continua instalado quando o container e recriado por uma imagem nova. Primeiro
    o pip calcula (sem instalar) o que falta neste ambiente; depois instala so isso,
    com --no-deps, na pasta do volume.
  - Fora do Docker (iniciar.bat / iniciar.sh): instala direto no Python do Argos,
    com o pip ou, no Python portatil do .bat, com o uv (bin/uv.exe).
  - No programa compilado (Argos EPI Servidor): nao ha pip nem Python solto. O onnx ja
    vem no motor; as DLLs do TensorRT (3 GB) sao um pacote opcional, ja compilado, que
    e baixado da pasta de downloads e extraido dentro de motor/.

Com pip, o numpy fica travado na versao instalada: o onnx puxaria o numpy 2,
e o resto do projeto precisa do numpy 1.
"""
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import pastas

BASE_DIR = pastas.RAIZ
EM_DOCKER = os.path.exists('/.dockerenv')
PASTA_DOCKER = os.environ.get('ARGOS_PYLIBS', '/app/pylibs')
# os mesmos limites que o Ultralytics confere ao exportar (TensorRT 10, CUDA 12)
PACOTES = ['onnx>=1.12.0,<2.0.0', 'tensorrt-cu12>=10.3,!=10.1.0,!=10.2.0,<11']
TAMANHO_APROX = '~2 GB'
# programa compilado: <instalacao>/motor (onde ficam o executavel e as .dll)
MOTOR = os.path.dirname(os.path.abspath(sys.executable)) if pastas.CONGELADO else ''
MARCA_TRT = os.path.join(MOTOR, 'argos-trt-id.txt')      # vem dentro do pacote do TensorRT
DOWNLOADS_PADRAO = 'https://pub-6b5befc214654d93bdc0345875151ea0.r2.dev/argosepi-discovery/'

_lock = threading.Lock()
# pct: quanto do download ja veio (None nas etapas sem medida, como instalar e conferir)
_estado = {'status': 'parado', 'etapa': '', 'pct': None, 'log': [], 'inicio': 0.0, 'fim': 0.0, 'erro': ''}


def preparar_caminho():
    """No Docker, poe a pasta do volume no fim do sys.path (o que ja vem na imagem ganha)."""
    if EM_DOCKER and os.path.isdir(PASTA_DOCKER) and PASTA_DOCKER not in sys.path:
        sys.path.append(PASTA_DOCKER)
    importlib.invalidate_caches()


def faltando() -> list:
    if pastas.CONGELADO:
        return [] if os.path.exists(MARCA_TRT) else ['tensorrt']
    preparar_caminho()
    return [m for m in ('onnx', 'tensorrt') if importlib.util.find_spec(m) is None]


def estado() -> dict:
    with _lock:
        e = dict(_estado)
        e['log'] = list(_estado['log'][-12:])
    e['faltando'] = faltando()
    e['tamanho'] = TAMANHO_APROX
    e['modo'] = 'docker' if EM_DOCKER else 'programa' if pastas.CONGELADO else 'local'
    return e


def _registrar(linha: str):
    linha = linha.rstrip()
    if not linha:
        return
    with _lock:
        _estado['log'].append(linha[-240:])
        del _estado['log'][:-200]
    print('[TensorRT/instalar] ' + linha, flush=True)


def _etapa(txt: str):
    with _lock:
        _estado['etapa'] = txt
        _estado['pct'] = None
    _registrar('== ' + txt)


def _rodar(cmd: list):
    _registrar('$ ' + ' '.join(cmd))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding='utf-8', errors='replace', bufsize=1,
                         env=dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK='1', PYTHONIOENCODING='utf-8'))
    for linha in p.stdout:
        _registrar(linha)
    if p.wait() != 0:
        raise RuntimeError(f'o instalador terminou com erro (codigo {p.returncode}). Veja o log abaixo.')


def _instalador_local() -> list:
    """Comando base para instalar no Python que roda o Argos."""
    if importlib.util.find_spec('pip') is not None:
        return [sys.executable, '-m', 'pip', 'install']
    uv = os.path.join(BASE_DIR, 'bin', 'uv.exe' if os.name == 'nt' else 'uv')
    uv = uv if os.path.exists(uv) else shutil.which('uv')
    if uv:
        return [uv, 'pip', 'install', '--python', sys.executable]
    raise RuntimeError('Nem pip nem uv foram encontrados neste Python.')


def _trava_numpy(pasta: str) -> str:
    caminho = os.path.join(pasta, 'restricoes.txt')
    with open(caminho, 'w', encoding='utf-8') as f:
        try:
            f.write('numpy==' + importlib.metadata.version('numpy') + '\n')
        except importlib.metadata.PackageNotFoundError:
            f.write('numpy<2\n')
    return caminho


def _ler(caminho: str) -> str:
    try:
        with open(caminho, encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


def _instalar_no_programa():
    """Programa compilado: baixa o pacote do TensorRT (so .dll/.pyd) e extrai em motor/. Sem pip."""
    import tarfile
    import zipfile

    import requests

    fonte = (os.environ.get('ARGOS_DOWNLOADS_URL') or DOWNLOADS_PADRAO).rstrip('/') + '/'
    _etapa('Procurando o pacote do TensorRT')
    m = requests.get(fonte + 'latest.json', timeout=30, headers={'User-Agent': 'ArgosEPI-Servidor'}).json()
    so = 'windows' if os.name == 'nt' else 'linux'
    pac = (((m.get(so) or {}).get('extras') or {}).get('tensorrt') or {}).get('nvidia')
    if not pac:
        raise RuntimeError('O pacote do TensorRT nao foi publicado para este sistema.')
    meu = _ler(os.path.join(MOTOR, 'argos-motor-id.txt'))
    if pac.get('motor') and meu and pac['motor'] != meu:
        raise RuntimeError('O pacote do TensorRT publicado e de outra versao do programa. Atualize o '
                           'Argos EPI Servidor (Ajustes do programa > Procurar atualizacao) e tente de novo.')
    url = pac['url'] if '://' in pac['url'] else fonte + pac['url']
    total = int(pac.get('tamanho') or 0)
    destino = os.path.join(BASE_DIR, 'dados', 'tensorrt.baixando')
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    _etapa(f'Baixando o TensorRT ({total / 1073741824:.1f} GB)')
    soma, feito, ultimo = hashlib.sha256(), 0, -1
    try:
        with requests.get(url, stream=True, timeout=60, headers={'User-Agent': 'ArgosEPI-Servidor'}) as r:
            r.raise_for_status()
            with open(destino, 'wb') as f:
                for bloco in r.iter_content(1 << 20):
                    f.write(bloco)
                    soma.update(bloco)
                    feito += len(bloco)
                    pct = feito * 100 // total if total else 0
                    if pct != ultimo:
                        ultimo = pct
                        with _lock:
                            _estado['pct'] = int(pct)
                            _estado['etapa'] = (f'Baixando o TensorRT: {pct}% '
                                                f'({feito / 1073741824:.1f} de {total / 1073741824:.1f} GB)')
        if pac.get('sha256') and soma.hexdigest() != pac['sha256']:
            raise RuntimeError('O download veio corrompido (SHA-256 diferente). Tente de novo.')
        _etapa('Instalando as bibliotecas do TensorRT')
        raiz = os.path.realpath(BASE_DIR)

        def alvo(nome):
            caminho = os.path.realpath(os.path.join(raiz, nome))
            if not caminho.startswith(os.path.join(raiz, 'motor') + os.sep):
                raise RuntimeError(f'Pacote com caminho inesperado: {nome}')
            os.makedirs(os.path.dirname(caminho), exist_ok=True)
            return caminho

        if url.endswith('.zip'):
            with zipfile.ZipFile(destino) as z:
                for item in z.infolist():
                    if not item.is_dir():
                        nome = item.filename.replace(chr(92), '/')
                        with z.open(item) as origem, open(alvo(nome), 'wb') as saida:
                            shutil.copyfileobj(origem, saida, 1 << 20)
        else:
            with tarfile.open(destino) as t:
                for item in t:
                    if item.isfile():
                        with t.extractfile(item) as origem, open(alvo(item.name), 'wb') as saida:
                            shutil.copyfileobj(origem, saida, 1 << 20)
    finally:
        try:
            os.remove(destino)
        except OSError:
            pass
    if not os.path.exists(MARCA_TRT):
        raise RuntimeError('O pacote baixado nao tem o que era esperado.')


def _instalar():
    if pastas.CONGELADO:
        _instalar_no_programa()
        _etapa('Conferindo')
        import tensorrt
        _registrar(f'TensorRT {tensorrt.__version__} pronto.')
        return
    with tempfile.TemporaryDirectory(prefix='argos-trt-') as tmp:
        restricoes = _trava_numpy(tmp)
        if EM_DOCKER:
            _etapa('Calculando o que falta')
            relatorio = os.path.join(tmp, 'plano.json')
            _rodar([sys.executable, '-m', 'pip', 'install', '--dry-run', '--quiet', '--report', relatorio,
                    '-c', restricoes, *PACOTES])
            with open(relatorio, encoding='utf-8') as f:
                plano = [f"{i['metadata']['name']}=={i['metadata']['version']}" for i in json.load(f).get('install', [])]
            if plano:
                os.makedirs(PASTA_DOCKER, exist_ok=True)
                _etapa(f'Baixando e instalando {len(plano)} pacote(s) ({TAMANHO_APROX})')
                _rodar([sys.executable, '-m', 'pip', 'install', '--no-deps', '--upgrade',
                        '--target', PASTA_DOCKER, *plano])
        else:
            _etapa(f'Baixando e instalando ({TAMANHO_APROX})')
            _rodar([*_instalador_local(), '-c', restricoes, *PACOTES])
    _etapa('Conferindo')
    preparar_caminho()
    import onnx  # noqa: F401
    import tensorrt
    _registrar(f'onnx {onnx.__version__} e TensorRT {tensorrt.__version__} prontos.')


def _trabalho():
    try:
        _instalar()
        with _lock:
            _estado.update(status='pronto', etapa='Pronto', fim=time.time(), erro='')
    except Exception as e:
        _registrar(f'ERRO: {e}')
        with _lock:
            _estado.update(status='erro', fim=time.time(), erro=str(e))


def instalar() -> dict:
    with _lock:
        if _estado['status'] == 'instalando':
            return {'iniciado': False, 'status': 'instalando'}
        _estado.update(status='instalando', etapa='Começando', pct=None, log=[], inicio=time.time(), fim=0.0, erro='')
    threading.Thread(target=_trabalho, daemon=True, name='instalar-tensorrt').start()
    return {'iniciado': True, 'status': 'instalando'}
