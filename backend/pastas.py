"""
pastas.py - Onde ficam as pastas do Argos (dados, models, bin) e os arquivos da interface.

Rodando do codigo (iniciar.bat, Docker), a raiz e a pasta do projeto e a
interface sai das pastas frontend/ e servidor_app/ui/.

No programa compilado (Argos EPI Servidor) nao ha codigo-fonte na instalacao:
  <instalacao>/motor/ArgosMotor.exe   este programa, com as bibliotecas (.dll) ao lado
  <instalacao>/recursos.pak           interface da janela (ui/) e painel local (frontend/)
  <instalacao>/models, bin, dados
"""
import os
import sys
import threading
import zipfile

CONGELADO = bool(getattr(sys, 'frozen', False))
if CONGELADO:
    RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
else:
    RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Pak:
    """recursos.pak: um zip so de leitura, aberto uma vez."""

    def __init__(self, caminho):
        self.zip = zipfile.ZipFile(caminho)
        self.nomes = set(self.zip.namelist())
        self.lock = threading.Lock()

    def ler(self, nome):
        nome = nome.replace('\\', '/').lstrip('/')
        if nome not in self.nomes or '..' in nome.split('/'):
            return None
        with self.lock:
            return self.zip.read(nome)


def _abrir_pak():
    caminho = os.path.join(RAIZ, 'recursos.pak')
    if CONGELADO and os.path.exists(caminho):
        return _Pak(caminho)
    return None


PAK = _abrir_pak()
