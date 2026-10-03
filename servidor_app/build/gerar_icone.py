"""Gera o argos.ico (icone do programa e do instalador) a partir do icone-512.png do site.

O lince fica num circulo branco: assim aparece bem na barra de tarefas clara ou escura.
Uso: python gerar_icone.py  (precisa do Pillow)
"""
import os
from PIL import Image, ImageDraw

AQUI = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(AQUI)
ORIGEM = os.path.join(APP, 'ui', 'icone-512.png')
DESTINO = os.path.join(APP, 'windows', 'argos.ico')
PNG_LINUX = os.path.join(APP, 'linux', 'argos-epi-servidor.png')


def montar(tam=1024):
    lince = Image.open(ORIGEM).convert('RGBA')
    base = Image.new('RGBA', (tam, tam), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    d.ellipse((0, 0, tam - 1, tam - 1), fill=(255, 255, 255, 255))
    margem = int(tam * 0.06)
    lince = lince.resize((tam - 2 * margem, tam - 2 * margem), Image.LANCZOS)
    base.alpha_composite(lince, (margem, margem))
    return base


if __name__ == '__main__':
    img = montar()
    tamanhos = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    img.save(DESTINO, sizes=[(t, t) for t in tamanhos])
    os.makedirs(os.path.dirname(PNG_LINUX), exist_ok=True)
    img.resize((256, 256), Image.LANCZOS).save(PNG_LINUX)
    print('ok', DESTINO, PNG_LINUX)
