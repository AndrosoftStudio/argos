# -*- coding: utf-8 -*-
"""Gera o fundo de malha (mesh gradient) do Argos como imagem.

Gradiente radial em CSS sempre le como circulo: da para empilhar varios, mas a
transicao entre eles denuncia a borda de cada um. A referencia e uma malha
organica, com manchas que se derramam umas nas outras sem borda nenhuma.

Aqui cada mancha e uma gaussiana somada em ponto flutuante, num quadro pequeno.
A soma e depois ampliada com interpolacao suave: a mistura sai do proprio
reamostrador e nao sobra circulo em lugar nenhum.
"""
import os

import numpy as np
from PIL import Image, ImageFilter

DESTINO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

BASE = np.array([33, 20, 104], dtype=np.float32)   # indigo do fundo

# Poucas manchas, grandes e muito sobrepostas: e a sobreposicao que faz a malha
# virar um lencol continuo em vez de um punhado de bolinhas.
# (x, y, raio, achatamento, peso, cor) -- x/y/raio em fracao da largura
MANCHAS = [
    # Duas massas claras na diagonal, com escuro cavado em volta. Sao as manchas
    # escuras que criam os canais entre as claras -- so somando luz o fundo vira
    # um holofote unico, que foi o erro da primeira tentativa.
    # --- massa clara de cima, a esquerda
    (0.30, 0.24, 0.40, 2.10, 1.00, (10, 150, 240)),
    (0.36, 0.28, 0.26, 1.70, 0.55, (16, 164, 250)),
    (0.16, 0.30, 0.30, 1.60, 0.62, (14, 124, 214)),
    (0.46, 0.20, 0.26, 1.90, 0.40, (22, 128, 214)),
    # --- massa clara de baixo, deslocada para a direita
    (0.66, 0.62, 0.40, 2.00, 0.88, (34, 116, 206)),
    (0.78, 0.66, 0.28, 1.70, 0.50, (52, 118, 196)),
    (0.50, 0.60, 0.26, 1.80, 0.45, (26, 112, 200)),
    # --- escuro cavado: canto superior direito, rodape e o canal entre as massas
    (0.88, 0.12, 0.42, 1.30, 1.00, (34, 12, 104)),
    (0.97, 0.40, 0.30, 1.60, 0.75, (36, 16, 108)),
    (0.50, 0.99, 0.55, 3.00, 0.95, (32, 24, 106)),
    (0.03, 0.85, 0.30, 1.60, 0.70, (34, 28, 110)),
    (0.60, 0.40, 0.22, 2.20, 0.45, (30, 30, 118)),
]

W, H = 1920, 1080
PW, PH = 240, 135          # malha nasce pequena; a ampliacao e que mistura


def campo_suave(forma, escala, semente):
    """Ruido bem liso, para torcer as coordenadas."""
    rng = np.random.default_rng(semente)
    peq = rng.normal(0, 1, (max(3, forma[0] // escala), max(3, forma[1] // escala)))
    return np.asarray(Image.fromarray(peq.astype(np.float32), 'F')
                      .resize((forma[1], forma[0]), Image.BICUBIC))


def malha():
    ys, xs = np.mgrid[0:PH, 0:PW].astype(np.float32)
    xs /= PW
    ys /= PH
    # Torce o plano antes de somar as manchas. Sem isso cada mancha le como
    # elipse; com isso elas ganham o contorno irregular da referencia.
    # frequencia bem baixa (uns poucos pontos de controle na tela inteira):
    # o suficiente para entortar o contorno, longe de virar textura
    wx = campo_suave((PH, PW), 55, 11) * 0.075
    wy = campo_suave((PH, PW), 55, 29) * 0.075
    xs = xs + wx
    ys = ys + wy
    aspecto = PW / PH
    acum = np.tile(BASE, (PH, PW, 1))
    for fx, fy, fr, achat, peso, cor in MANCHAS:
        dx = (xs - fx) * aspecto
        dy = (ys - fy) * aspecto / achat
        d2 = (dx * dx + dy * dy) / (fr * fr)
        g = np.exp(-d2 * 1.5) * peso          # gaussiana larga: cai sem borda
        acum += g[..., None] * (np.array(cor, dtype=np.float32) - BASE)
    return np.clip(acum, 0, 255).astype(np.uint8)


def vinheta(arr):
    """Escurece as bordas: centro intacto, cantos puxados para o azul quase preto."""
    h, w = arr.shape[:2]
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    dx = (xs / w - 0.5) / 0.5
    dy = (ys / h - 0.5) / 0.5
    r = np.sqrt(dx * dx * 0.82 + dy * dy)      # elipse acompanhando a tela
    k = np.clip((r - 0.62) / 0.90, 0, 1) ** 1.5   # so comeca a valer depois do meio
    sombra = np.array([8, 7, 44], dtype=np.float32)
    saida = arr.astype(np.float32) * (1 - k[..., None] * 0.66) + sombra * (k[..., None] * 0.66)
    return np.clip(saida, 0, 255).astype(np.uint8)


def grao(arr, forca=8.5):
    ruido = np.random.default_rng(7).normal(0, forca, arr.shape[:2]).astype(np.float32)
    return np.clip(arr.astype(np.float32) + ruido[..., None], 0, 255).astype(np.uint8)


im = Image.fromarray(malha()).resize((W, H), Image.BICUBIC)
im = im.filter(ImageFilter.GaussianBlur(W / 150))
arr = grao(vinheta(np.asarray(im)))
im = Image.fromarray(arr)

caminho = os.path.join(DESTINO, 'fundo-malha.jpg')
im.save(caminho, quality=84, optimize=True, progressive=True)
print('fundo-malha.jpg  %.1f KB  %dx%d' % (os.path.getsize(caminho) / 1024, *im.size))

