"""Empacota a janela do Linux (main.js, preload.js, package.json) num app.asar, o formato de
pacote do Electron: a instalacao nao fica com arquivos .js soltos em janela/resources/app.

Formato (o mesmo do @electron/asar): [pickle com o tamanho do cabecalho][pickle com o cabecalho
JSON][conteudo dos arquivos em sequencia]. Cada arquivo entra no cabecalho com tamanho, posicao
(texto, contada a partir do fim do cabecalho) e a soma SHA-256.
Uso: python montar_asar.py <pasta do app> <destino/app.asar>
"""
import hashlib
import json
import os
import struct
import sys

BLOCO = 4 * 1024 * 1024


def integridade(dados):
    blocos = [hashlib.sha256(dados[i:i + BLOCO]).hexdigest() for i in range(0, len(dados), BLOCO)] or \
             [hashlib.sha256(b'').hexdigest()]
    return {'algorithm': 'SHA256', 'hash': hashlib.sha256(dados).hexdigest(), 'blockSize': BLOCO, 'blocks': blocos}


def main():
    pasta, destino = sys.argv[1], sys.argv[2]
    arvore, corpo, pos = {'files': {}}, [], 0
    for atual, pastas, arquivos in os.walk(pasta):
        pastas.sort()
        no = arvore
        rel = os.path.relpath(atual, pasta)
        if rel != '.':
            for parte in rel.replace('\\', '/').split('/'):
                no = no['files'].setdefault(parte, {'files': {}})
        for nome in sorted(arquivos):
            with open(os.path.join(atual, nome), 'rb') as f:
                dados = f.read()
            no['files'][nome] = {'size': len(dados), 'offset': str(pos), 'integrity': integridade(dados)}
            corpo.append(dados)
            pos += len(dados)
    texto = json.dumps(arvore, separators=(',', ':')).encode('utf-8')
    alinhado = texto + b'\0' * (-len(texto) % 4)
    cabecalho = struct.pack('<II', 4 + len(alinhado), len(texto)) + alinhado     # pickle: tamanho + texto
    with open(destino, 'wb') as f:
        f.write(struct.pack('<II', 4, len(cabecalho)))                             # pickle: tamanho do cabecalho
        f.write(cabecalho)
        for dados in corpo:
            f.write(dados)
    print(f'app.asar: {len(corpo)} arquivos, {os.path.getsize(destino)} bytes')


if __name__ == '__main__':
    main()
