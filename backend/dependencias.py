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

Nos dois casos o numpy fica travado na versao instalada: o onnx puxaria o numpy 2,
e o resto do projeto precisa do numpy 1.
"""
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

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EM_DOCKER = os.path.exists('/.dockerenv')
PASTA_DOCKER = os.environ.get('ARGOS_PYLIBS', '/app/pylibs')
# os mesmos limites que o Ultralytics confere ao exportar (TensorRT 10, CUDA 12)
PACOTES = ['onnx>=1.12.0,<2.0.0', 'tensorrt-cu12>=10.3,!=10.1.0,!=10.2.0,<11']
TAMANHO_APROX = '~2 GB'

_lock = threading.Lock()
_estado = {'status': 'parado', 'etapa': '', 'log': [], 'inicio': 0.0, 'fim': 0.0, 'erro': ''}


def preparar_caminho():
    """No Docker, poe a pasta do volume no fim do sys.path (o que ja vem na imagem ganha)."""
    if EM_DOCKER and os.path.isdir(PASTA_DOCKER) and PASTA_DOCKER not in sys.path:
        sys.path.append(PASTA_DOCKER)
    importlib.invalidate_caches()


def faltando() -> list:
    preparar_caminho()
    return [m for m in ('onnx', 'tensorrt') if importlib.util.find_spec(m) is None]


def estado() -> dict:
    with _lock:
        e = dict(_estado)
        e['log'] = list(_estado['log'][-12:])
    e['faltando'] = faltando()
    e['tamanho'] = TAMANHO_APROX
    e['modo'] = 'docker' if EM_DOCKER else 'local'
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


def _instalar():
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
        _estado.update(status='instalando', etapa='Começando', log=[], inicio=time.time(), fim=0.0, erro='')
    threading.Thread(target=_trabalho, daemon=True, name='instalar-tensorrt').start()
    return {'iniciado': True, 'status': 'instalando'}
