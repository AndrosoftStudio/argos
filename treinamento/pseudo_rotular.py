"""
pseudo_rotular.py - Completa rotulos que faltam usando um modelo "professor".

Cada dataset publico anota so alguns EPIs: um marca capacete e colete, mas ignora as
luvas que aparecem na foto. Treinar assim ensina o modelo que aquelas luvas sao fundo.
Aqui o professor (treinado no dataset montado) acrescenta caixas apenas das classes que
a fonte daquela imagem NAO anota, e so com confianca alta. O teste nao e alterado.

Os rotulos originais ficam em labels_originais/, entao rodar de novo nao acumula caixas.

Uso:
  python treinamento/pseudo_rotular.py --dataset D:/ArgosEPI/datasets/argos_epi_v1 --pesos <best.pt do professor>
"""
import argparse
import csv
import glob
import os
import shutil
from collections import Counter

import comum  # antes do ppe_taxonomy: coloca backend/ no sys.path
import ppe_taxonomy as tax

CLASSES = tax.DATASET_CLASSES


def ler_rotulo(caminho):
    caixas = []
    if os.path.exists(caminho):
        with open(caminho) as f:
            for linha in f:
                v = linha.split()
                if len(v) == 5:
                    caixas.append((int(v[0]), *map(float, v[1:])))
    return caixas


def item(c):
    nome = CLASSES[c]
    return nome[4:] if nome.startswith('sem_') else nome


def main():
    ap = argparse.ArgumentParser(description='Completa rotulos faltantes com um modelo professor')
    ap.add_argument('--dataset', required=True)
    ap.add_argument('--pesos', required=True)
    ap.add_argument('--conf', type=float, default=0.5)
    ap.add_argument('--conf-negativo', type=float, default=0.6, help='limiar das classes sem_*')
    ap.add_argument('--iou-conflito', type=float, default=0.3,
                    help='nao cria caixa que sobreponha outra do mesmo EPI (ex.: luvas x sem_luvas)')
    ap.add_argument('--imgsz', type=int, default=640)
    ap.add_argument('--lote', type=int, default=32)
    ap.add_argument('--device', default='0')
    ap.add_argument('--splits', nargs='*', default=['train', 'val'])
    args = ap.parse_args()

    from ultralytics import YOLO
    modelo = YOLO(args.pesos)
    nomes_modelo = [modelo.names[i] for i in sorted(modelo.names)]
    if nomes_modelo != CLASSES:
        raise SystemExit(f'classes do professor diferem da taxonomia: {nomes_modelo}')

    fontes_classes = comum.carregar_json(os.path.join(args.dataset, 'fontes_classes.json'), {})
    anotadas_por_fonte = {f: {CLASSES.index(c) for c in cls} for f, cls in fontes_classes.items()}
    with open(os.path.join(args.dataset, 'origem.csv'), newline='', encoding='utf-8') as f:
        fonte_de = {linha['arquivo']: linha['fonte'] for linha in csv.DictReader(f)}

    adicionadas = Counter()
    for split in args.splits:
        pasta_rotulos = os.path.join(args.dataset, 'labels', split)
        pasta_originais = os.path.join(args.dataset, 'labels_originais', split)
        if not os.path.isdir(pasta_originais):
            shutil.copytree(pasta_rotulos, pasta_originais)
        imagens = sorted(glob.glob(os.path.join(args.dataset, 'images', split, '*.jpg')))
        print(f"[{split}] {len(imagens)} imagens")
        for ini in range(0, len(imagens), args.lote):
            lote = imagens[ini:ini + args.lote]
            resultados = modelo.predict(lote, imgsz=args.imgsz, conf=min(args.conf, args.conf_negativo),
                                        device=args.device, half=True, verbose=False, batch=len(lote))
            for caminho, res in zip(lote, resultados):
                arquivo = os.path.splitext(os.path.basename(caminho))[0]
                # fonte desconhecida: trata como se anotasse tudo (nao inventa caixas)
                ja_anotadas = anotadas_por_fonte.get(fonte_de.get(arquivo), set(range(len(CLASSES))))
                originais = ler_rotulo(os.path.join(pasta_originais, arquivo + '.txt'))
                novas = []
                caixas = res.boxes
                for k in (caixas.conf.argsort(descending=True).tolist() if len(caixas) else []):
                    c, p = int(caixas.cls[k]), float(caixas.conf[k])
                    limiar = args.conf_negativo if CLASSES[c].startswith('sem_') else args.conf
                    if c in ja_anotadas or p < limiar:
                        continue
                    candidata = (c, *map(float, caixas.xywhn[k].tolist()))
                    if any(item(o[0]) == item(c) and comum.iou_xywh(o, candidata) > args.iou_conflito
                           for o in originais + novas):
                        continue
                    novas.append(candidata)
                    adicionadas[(split, CLASSES[c])] += 1
                with open(os.path.join(pasta_rotulos, arquivo + '.txt'), 'w') as f:
                    f.writelines(f"{c} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n" for c, cx, cy, w, h in originais + novas)
            if (ini // args.lote) % 50 == 0:
                print(f"  {min(ini + args.lote, len(imagens))}/{len(imagens)}")
        cache = pasta_rotulos + '.cache'  # o YOLO guarda os rotulos lidos; forcar releitura
        if os.path.exists(cache):
            os.remove(cache)

    resumo = {f"{s}/{c}": n for (s, c), n in sorted(adicionadas.items())}
    comum.salvar_json(os.path.join(args.dataset, 'pseudo_rotulos.json'),
                      {'pesos': args.pesos, 'conf': args.conf, 'conf_negativo': args.conf_negativo, 'adicionadas': resumo})
    print('Caixas adicionadas:')
    for chave, n in resumo.items():
        print(f"  {chave}: {n}")


if __name__ == '__main__':
    main()
