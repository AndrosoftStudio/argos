"""
gerar_sirene.py - Gera frontend/sons/sirene.wav, o som de sirene da TV e do painel.

E uma sirene do tipo "wail" (a de ambulancia/fabrica: o tom sobe e desce), sintetizada: o tom
varre de 650 a 1350 Hz, com harmonicos de corneta, duas vozes levemente desafinadas (o som
"cheio" de sirene mecanica) e um pouco de eco de galpao. Um ciclo de 2,4 s, feito para tocar
em sequencia sem estalo. Quem preferir uma gravacao propria e so trocar o arquivo .wav.

Uso:  python scripts/gerar_sirene.py
"""
import os
import wave

import numpy as np

TAXA = 22050
DUR = 2.4
F_MIN, F_MAX = 650.0, 1350.0
SAIDA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend', 'sons', 'sirene.wav')


def voz(freq, desafino=1.0):
    """Tom de corneta: fundamental + harmonicos que caem devagar (som aspero, que corta o barulho)."""
    fase = 2 * np.pi * np.cumsum(freq * desafino) / TAXA
    onda = np.zeros_like(fase)
    for n in range(1, 9):
        # harmonicos acima de 9 kHz ficam de fora (evita chiado de amostragem)
        peso = np.where(freq * n < 9000, 1.0 / n ** 1.15, 0.0)
        onda += peso * np.sin(n * fase)
    return onda


def gerar():
    t = np.arange(int(TAXA * DUR)) / TAXA
    # sobe em 1,0 s, desce em 1,4 s (a descida mais lenta e o que da o "uiii-uuuu" de sirene)
    subida = t < 1.0
    pos = np.where(subida, t / 1.0, 1.0 - (t - 1.0) / (DUR - 1.0))
    curva = np.sin(pos * np.pi / 2) ** 1.3                      # arranque rapido, topo suave
    freq = F_MIN + (F_MAX - F_MIN) * curva
    som = voz(freq) + 0.8 * voz(freq, 1.006) + 0.35 * voz(freq, 0.5)   # 2 rotores + uma oitava abaixo
    som *= 0.72 + 0.28 * curva                                  # mais forte no agudo, como a de verdade
    som = np.tanh(1.6 * som / np.max(np.abs(som)))              # satura de leve (alto-falante de corneta)
    # eco curto de ambiente fechado, dando a volta no fim para o ciclo emendar sem estalo
    eco = np.zeros_like(som)
    for atraso_s, ganho in ((0.031, 0.28), (0.047, 0.2), (0.089, 0.13), (0.137, 0.08)):
        eco += ganho * np.roll(som, int(atraso_s * TAXA))
    som = som + eco
    som /= np.max(np.abs(som))
    # 12 ms de entrada e saida suaves: tocando um ciclo atras do outro, a emenda nao estala
    n = int(0.012 * TAXA)
    rampa = np.sin(np.linspace(0, np.pi / 2, n)) ** 2
    som[:n] *= rampa
    som[-n:] *= rampa[::-1]
    return (som * 0.89 * 32767).astype('<i2')


def main():
    os.makedirs(os.path.dirname(SAIDA), exist_ok=True)
    amostras = gerar()
    with wave.open(SAIDA, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TAXA)
        w.writeframes(amostras.tobytes())
    print(f'{SAIDA}: {len(amostras) / TAXA:.1f} s, {os.path.getsize(SAIDA) // 1024} KB')


if __name__ == '__main__':
    main()
