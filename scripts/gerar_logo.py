"""Gera a logo e os icones do Argos a partir da arte original (fundo branco).

    python scripts/gerar_logo.py caminho/da/arte.jpeg

Escreve:
    frontend/logo.png, coisas/logo.png            logo completa, fundo transparente
    frontend/icone-192.png, frontend/icone-512.png so a cabeca do lince, transparente
    frontend/apple-touch-icon.png                 cabeca sobre o azul-marinho da marca
    frontend/favicon.ico, coisas/favicon.ico      cabeca, varios tamanhos
    frontend/og-argos.png                         previa do link (WhatsApp etc.), 1200x630
    treinamento/site_treinamento/public/          favicon e logo do site do treino

O fundo branco sai (inclusive dentro das voltas do letreiro); o branco-azulado
do rosto do lince tem cor e continua opaco.
"""
import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AZUL_MARINHO = (5, 7, 90)          # fundo do apple-touch-icon
LADO_LOGO = 600                    # maior lado da logo final, em px


def _fundo_transparente(rgb):
    """RGBA com o fundo branco (e a sombra cinza ligada a ele) transparente."""
    c = rgb.astype(np.float32)
    menor, maior = c.min(axis=2), c.max(axis=2)
    # parece fundo: quase branco, ou cinza claro sem cor (sombra do letreiro)
    parece_fundo = (menor >= 238) | ((menor >= 200) & (maior - menor < 12))
    rotulos, n = ndimage.label(parece_fundo)
    borda = np.unique(np.concatenate([rotulos[0], rotulos[-1], rotulos[:, 0], rotulos[:, -1]]))
    # brancos fechados (dentro das voltas do "J" do letreiro) tambem sao fundo;
    # o rosto do lince e branco-azulado e nem entra em parece_fundo
    tam = ndimage.sum(np.ones_like(rotulos), rotulos, range(1, n + 1))
    fechados = np.nonzero(tam >= 30)[0] + 1
    fundo = np.isin(rotulos, np.union1d(borda[borda > 0], fechados))
    # faixa de 2 px em volta do fundo: borda suavizada (antialias) do desenho
    faixa = ndimage.binary_dilation(fundo, iterations=2)

    # "cor para alfa" contra o branco: sobre fundo branco a imagem fica identica
    alfa_branco = (255.0 - menor) / 255.0
    alfa = np.where(faixa, alfa_branco, 1.0)
    alfa = np.where(fundo & (menor >= 238), 0.0, alfa)
    seguro = np.maximum(alfa, 1e-3)[..., None]
    cor = np.where(faixa[..., None], (c - 255.0 * (1.0 - alfa[..., None])) / seguro, c)
    rgba = np.dstack([np.clip(cor, 0, 255), alfa * 255.0]).astype(np.uint8)
    return rgba


def _recortar(rgba, margem=8):
    ys, xs = np.nonzero(rgba[..., 3] > 25)
    y0, y1 = max(ys.min() - margem, 0), min(ys.max() + margem + 1, rgba.shape[0])
    x0, x1 = max(xs.min() - margem, 0), min(xs.max() + margem + 1, rgba.shape[1])
    return rgba[y0:y1, x0:x1]


def _cabeca(rgba, w, h):
    """So o lince: corta a regiao da cabeca e apaga o dourado (louros e letreiro)."""
    # caixa da cabeca em fracao da arte (orelhas ate o peito, focinho e bigodes inclusos)
    x0, x1 = int(w * 0.26), int(w * 0.75)
    y0, y1 = int(h * 0.17), int(h * 0.665)
    reg = rgba[y0:y1, x0:x1].copy()
    c = reg[..., :3].astype(int)
    dourado = (c[..., 0] - c[..., 2] > 35)
    # o olho e creme (puxa para o dourado): protege a regiao dele
    oy0, oy1 = int(h * 0.41) - y0, int(h * 0.46) - y0
    ox0, ox1 = int(w * 0.54) - x0, int(w * 0.61) - x0
    dourado[oy0:oy1, ox0:ox1] = False
    reg[..., 3][dourado] = 0
    # fica so o maior pedaco (o lince); sobras de folha somem
    rotulos, n = ndimage.label(reg[..., 3] > 128)
    if n > 1:
        tam = ndimage.sum(np.ones_like(rotulos), rotulos, range(1, n + 1))
        maior = np.argmax(tam) + 1
        manter = ndimage.binary_dilation(rotulos == maior, iterations=2)
        reg[..., 3][~manter] = 0
    return _recortar(reg, margem=0)


def _quadrado(img, lado, ocupacao, fundo=(0, 0, 0, 0)):
    """Centraliza img num quadrado lado x lado, ocupando a fracao dada."""
    escala = lado * ocupacao / max(img.size)
    nova = img.resize((max(1, round(img.width * escala)), max(1, round(img.height * escala))),
                      Image.LANCZOS)
    tela = Image.new('RGBA', (lado, lado), fundo)
    tela.alpha_composite(nova, ((lado - nova.width) // 2, (lado - nova.height) // 2))
    return tela


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    arte = Image.open(sys.argv[1]).convert('RGB')
    w, h = arte.size
    rgba = _fundo_transparente(np.array(arte))

    logo = Image.fromarray(_recortar(rgba))
    logo.thumbnail((LADO_LOGO, LADO_LOGO), Image.LANCZOS)
    cabeca = Image.fromarray(_cabeca(rgba, w, h))

    def caminho(*p):
        return os.path.join(RAIZ, *p)

    for destino in [caminho('frontend', 'logo.png'), caminho('coisas', 'logo.png'),
                    caminho('treinamento', 'site_treinamento', 'public', 'logo.png')]:
        logo.save(destino, optimize=True)

    for lado in (192, 512):
        _quadrado(cabeca, lado, 0.80).save(caminho('frontend', f'icone-{lado}.png'), optimize=True)
    _quadrado(cabeca, 180, 0.78, AZUL_MARINHO + (255,)).convert('RGB').save(
        caminho('frontend', 'apple-touch-icon.png'), optimize=True)

    ico = _quadrado(cabeca, 256, 0.96)
    ico.save(caminho('frontend', 'favicon.ico'), sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    for destino in [caminho('coisas', 'favicon.ico'),
                    caminho('treinamento', 'site_treinamento', 'public', 'favicon.ico')]:
        ico.save(destino, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    og = Image.new('RGBA', (1200, 630), AZUL_MARINHO + (255,))
    grande = Image.fromarray(_recortar(rgba))
    grande.thumbnail((470, 470), Image.LANCZOS)
    og.alpha_composite(grande, ((1200 - grande.width) // 2, (630 - grande.height) // 2))
    og.convert('RGB').save(caminho('frontend', 'og-argos.png'), optimize=True)

    print(f'logo {logo.size}, cabeca {cabeca.size}: arquivos gravados em frontend/, coisas/ '
          'e treinamento/site_treinamento/public/')


if __name__ == '__main__':
    main()
