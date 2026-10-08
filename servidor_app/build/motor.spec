# -*- mode: python ; coding: utf-8 -*-
# Receita do PyInstaller para o motor do Argos EPI Servidor (motor/ArgosMotor.exe no Windows,
# motor/argos-motor no Linux): supervisor + backend compilados num executavel, com o Python e
# as bibliotecas em .dll/.pyd (.so) na mesma pasta. Nenhum .py vai para a instalacao.
# Chamado pelo empacotar_windows.ps1 / empacotar_linux.sh com:
#   ARGOS_RAIZ   pasta do projeto (v20)
#   ARGOS_PLACA  cpu | dml | nvidia
# Windows (ARGOS_CODIGO_FORA=1): o executavel leva so as bibliotecas; o codigo do Argos sai para o
# codigo.pak (montar_codigo.py), e a atualizacao de uma versao para outra baixa so esse arquivo pequeno.
#   ARGOS_CODIGO_LISTA  onde gravar a lista dos modulos do Argos (entrada do montar_codigo.py)
#   ARGOS_NUCLEO_ID     onde gravar o id do executavel (muda so quando muda uma biblioteca ou a receita)
import glob
import hashlib
import json
import os
import sys

from PyInstaller.utils.hooks import (collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata,
                                     get_package_paths)

RAIZ = os.environ['ARGOS_RAIZ']
PLACA = os.environ.get('ARGOS_PLACA', 'cpu')
WIN = sys.platform == 'win32'
CODIGO_FORA = os.environ.get('ARGOS_CODIGO_FORA') == '1'


def metadados(*pacotes):
    """O Ultralytics confere a versao dos pacotes pelo .dist-info antes de exportar."""
    saida = []
    for p in pacotes:
        try:
            saida += copy_metadata(p)
        except Exception:
            pass
    return saida


ocultos = ['psycopg_binary', 'lap']
# o banner so usa uma fonte do pyfiglet (o pacote traz mais de 500)
dados = collect_data_files('pyfiglet', includes=['fonts/ansi_shadow.flf', 'fonts/standard.flf'])
binarios = []

# OpenCV: o carregador do pacote (cv2/__init__.py) so funciona lendo arquivos .py de configuracao
# em texto. Aqui vai so a extensao compilada, ao lado do executavel: "import cv2" abre ela direto.
_cv2 = get_package_paths('cv2')[1]
binarios += [(f, '.') for padrao in ('cv2*.pyd', 'cv2*.so', 'opencv_videoio_ffmpeg*.dll')
             for f in glob.glob(os.path.join(_cv2, padrao))]
# torchvision: as operacoes em C++ (o NMS das deteccoes) ficam em _C_stable/image_stable, que o gancho do
# PyInstaller ainda procura com o nome antigo (_C/image). Sem elas qualquer deteccao falha.
_tv = get_package_paths('torchvision')[1]
binarios += collect_dynamic_libs('torchvision')
binarios += [(f, 'torchvision') for padrao in ('*.pyd', '*.so') for f in glob.glob(os.path.join(_tv, padrao))]
dados += metadados('ultralytics', 'torch', 'torchvision', 'numpy', 'opencv-python', 'lap',
                   'onnxruntime', 'onnxruntime-gpu', 'onnxruntime-directml')

if PLACA == 'dml':
    ocultos += ['torch_directml']
    binarios += collect_dynamic_libs('torch_directml')
    dados += collect_data_files('torch_directml')
if PLACA == 'nvidia':
    # Conversao para TensorRT: o onnx e a parte Python do tensorrt entram no motor; as DLLs do TensorRT
    # (3 GB) saem depois para um pacote opcional (pastas tensorrt_*, ver empacotar_*), que o botao
    # "Instalar dependencias do TensorRT" do painel baixa ja compilado.
    # So entram se estiverem no Python de montagem (no Linux o TensorRT e opcional: COM_TENSORRT=1).
    import importlib.util
    if importlib.util.find_spec('onnx'):
        ocultos += ['onnx']
        ocultos += collect_submodules('onnx', filter=lambda n: '.test' not in n and not n.startswith('onnx.bin'))
    if importlib.util.find_spec('tensorrt'):
        ocultos += ['tensorrt', 'tensorrt_libs', 'tensorrt_bindings']
    dados += metadados('onnx', 'tensorrt-cu12', 'tensorrt-cu12-libs', 'tensorrt-cu12-bindings', 'protobuf')

a = Analysis(
    # com o codigo fora, a entrada e o nucleo.py: ele importa o motor.py, que vem do codigo.pak
    [os.path.join(RAIZ, 'servidor_app', 'build', 'nucleo.py' if CODIGO_FORA else 'motor.py')],
    pathex=[RAIZ, os.path.join(RAIZ, 'backend'), os.path.join(RAIZ, 'scripts'), os.path.join(RAIZ, 'servidor_app')],
    binaries=binarios,
    datas=dados,
    hiddenimports=ocultos,
    runtime_hooks=[os.path.join(RAIZ, 'servidor_app', 'build', 'rthook_motor.py')],
    # cv2: ver acima. polars/pandas/gevent: so entrariam por funcoes do Ultralytics que o Argos nao usa.
    # pkg_resources: so um utilitario do onnxruntime pede, e sem o setuptools ele nem abre.
    excludes=['cv2', 'tkinter', '_tkinter', 'IPython', 'jupyter', 'notebook', 'pytest', 'PyQt5', 'PyQt6', 'PySide2',
              'PySide6', 'tensorboard', 'tensorflow', 'pip', 'setuptools', 'pkg_resources', 'wheel', 'polars',
              '_polars_runtime_32', 'pandas', 'gevent', 'greenlet'],
    # os ganchos do torch/ultralytics mandam copiar tambem os .py: aqui vai so o compilado
    module_collection_mode={'torch': 'pyz', 'torchvision': 'pyz', 'ultralytics': 'pyz', 'onnx': 'pyz'},
    noarchive=False,
)
if CODIGO_FORA:
    import PyInstaller

    def _do_argos(fonte):
        # pastas sem __init__.py (pacotes de espaco de nomes) aparecem com '-' no lugar do arquivo
        return os.path.isfile(fonte) and \
            os.path.normcase(os.path.abspath(fonte)).startswith(os.path.normcase(os.path.abspath(RAIZ)) + os.sep)

    def _sha(arquivo):
        try:
            with open(arquivo, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return str(arquivo)

    nossos = sorted((m[0], m[1]) for m in a.pure if _do_argos(m[1]))
    # as pastas do Argos (backend) saem junto: se ficassem no executavel, ele procuraria backend.app dentro dele
    pastas_nossas = {nome.rsplit('.', i)[0] for nome, _ in nossos for i in range(1, nome.count('.') + 1)}
    a.pure = [m for m in a.pure if not _do_argos(m[1]) and not (m[1] == '-' and m[0] in pastas_nossas)]
    if not any(nome == 'motor' for nome, _ in nossos):
        raise SystemExit('motor.spec: o PyInstaller nao achou o codigo do Argos a partir do nucleo.py')
    with open(os.environ['ARGOS_CODIGO_LISTA'], 'w', encoding='utf-8') as f:
        json.dump([{'nome': nome, 'fonte': fonte, 'pacote': os.path.basename(fonte).startswith('__init__.')}
                   for nome, fonte in nossos], f, indent=1)
    # id do executavel: o que entra nele (bibliotecas em Python, scripts de partida, PyInstaller, Python).
    # O arquivo em si muda a cada compilacao; o id so muda quando muda o que foi compilado.
    partes = [PyInstaller.__version__, sys.version, PLACA, 'console utf8 sem-upx']
    partes += sorted(m[0] + ':' + _sha(m[1]) for m in a.pure)
    partes += [m[0] + ':' + _sha(m[1]) for m in a.scripts]
    with open(os.environ['ARGOS_NUCLEO_ID'], 'w') as f:
        f.write(hashlib.sha256('|'.join(partes).encode('utf-8')).hexdigest())
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [('u', None, 'OPTION'), ('X utf8', None, 'OPTION')],     # saida sem buffer e em UTF-8
    exclude_binaries=True,
    name='ArgosMotor' if WIN else 'argos-motor',
    console=True,                 # aberto escondido pela janela; console=False abriria uma janela a cada filho
    icon=os.path.join(RAIZ, 'servidor_app', 'windows', 'argos.ico') if WIN else None,
    contents_directory='.',       # .dll/.pyd ao lado do executavel, tudo dentro de motor/
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name='motor', upx=False)
