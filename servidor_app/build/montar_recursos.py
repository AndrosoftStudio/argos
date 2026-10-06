"""Monta o recursos.pak do Argos EPI Servidor: a interface da janela (ui/) e o painel local (frontend/).

O programa instalado nao leva pastas de codigo: o motor le estes arquivos de dentro do .pak
(um zip so de leitura, ver backend/pastas.py).
Uso: python montar_recursos.py <pasta do projeto> <destino/recursos.pak>
"""
import os
import sys
import zipfile

# so vai o que o navegador pede; a API do site (frontend/api) roda na Vercel
FORA = {'api', 'node_modules', '.vercel', 'vercel.json', 'downloads.json', '.gitignore'}


def main():
    raiz, destino = sys.argv[1], sys.argv[2]
    origens = (('ui', os.path.join(raiz, 'servidor_app', 'ui')), ('frontend', os.path.join(raiz, 'frontend')))
    n = 0
    with zipfile.ZipFile(destino, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for prefixo, pasta in origens:
            for atual, pastas, arquivos in os.walk(pasta):
                pastas[:] = sorted(p for p in pastas if p not in FORA)
                for nome in sorted(arquivos):
                    if nome in FORA:
                        continue
                    caminho = os.path.join(atual, nome)
                    rel = os.path.relpath(caminho, pasta).replace('\\', '/')
                    z.write(caminho, f'{prefixo}/{rel}')
                    n += 1
    print(f'recursos.pak: {n} arquivos, {os.path.getsize(destino) / 1048576:.1f} MB')


if __name__ == '__main__':
    main()
