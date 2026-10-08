"""Junta o codigo do Argos, ja compilado (.pyc), em codigo.pak (Windows).

A lista de modulos vem do motor.spec (o que o PyInstaller achou dentro do projeto). O motor
abre o codigo.pak como um zip no caminho de importacao: nenhum .py vai para a instalacao.

Uso: python montar_codigo.py <lista.json> <saida codigo.pak>
"""
import json
import os
import py_compile
import sys
import tempfile
import zipfile


def compilar(fonte, mostra, tmp):
    saida = os.path.join(tmp, 'm.pyc')
    # 'mostra' e o nome que aparece nas mensagens de erro (sem o caminho da maquina de montagem);
    # UNCHECKED_HASH: o .pyc nao depende da data do arquivo e vale sem o .py ao lado
    py_compile.compile(fonte, cfile=saida, dfile=mostra, doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    with open(saida, 'rb') as f:
        return f.read()


def main(lista, destino):
    with open(lista, encoding='utf-8') as f:
        modulos = json.load(f)
    entradas = {}
    with tempfile.TemporaryDirectory() as tmp:
        for m in modulos:
            caminho = m['nome'].replace('.', '/') + ('/__init__' if m['pacote'] else '')
            entradas[caminho + '.pyc'] = compilar(m['fonte'], caminho + '.py', tmp)
        # pastas sem __init__.py (backend e uma pasta comum no projeto) viram pacotes de verdade
        vazio = os.path.join(tmp, 'vazio.py')
        open(vazio, 'w').close()
        for nome in list(entradas):
            partes = nome.split('/')[:-1]
            for i in range(1, len(partes) + 1):
                ini = '/'.join(partes[:i]) + '/__init__'
                if ini + '.pyc' not in entradas:
                    entradas[ini + '.pyc'] = compilar(vazio, ini + '.py', tmp)
    with zipfile.ZipFile(destino, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for nome in sorted(entradas):
            z.writestr(zipfile.ZipInfo(nome, date_time=(2020, 1, 1, 0, 0, 0)), entradas[nome],
                       compress_type=zipfile.ZIP_DEFLATED)
    print(f'codigo.pak: {len(entradas)} modulos, {os.path.getsize(destino) // 1024} KB')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
