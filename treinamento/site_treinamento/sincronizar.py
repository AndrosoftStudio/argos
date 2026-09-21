"""
sincronizar.py - Copia a pagina do painel (e a marca) para public/.

A pagina e a mesma que roda no PC, em treinamento/acompanhar_treinamento/index.html.
Rode isto depois de mexer no painel, antes de mandar o site para o GitHub:

    python treinamento/site_treinamento/sincronizar.py
"""
import os
import shutil

AQUI = os.path.dirname(os.path.abspath(__file__))
TREINAMENTO = os.path.dirname(AQUI)
V19 = os.path.dirname(TREINAMENTO)
PROJETO = os.path.dirname(V19)
PUBLICO = os.path.join(AQUI, 'public')

ARQUIVOS = [
    (os.path.join(TREINAMENTO, 'acompanhar_treinamento', 'index.html'), 'index.html', True),
    (os.path.join(PROJETO, 'coisas', 'logo.png'), 'logo.png', False),
    (os.path.join(PROJETO, 'coisas', 'favicon.ico'), 'favicon.ico', False),
]


def main():
    os.makedirs(PUBLICO, exist_ok=True)
    for origem, destino, obrigatorio in ARQUIVOS:
        if not os.path.exists(origem):
            if obrigatorio:
                raise SystemExit(f'nao encontrei {origem}')
            print(f'- {destino}: sem origem ({origem}), pulando')
            continue
        shutil.copy2(origem, os.path.join(PUBLICO, destino))
        print(f'+ {destino}')


if __name__ == '__main__':
    main()
