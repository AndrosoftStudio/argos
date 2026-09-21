"""
avaliar.py - Avalia o detector de EPIs no conjunto de teste, com metrica "justa".

Cada fonte anota so parte dos EPIs, entao o mAP comum pune o modelo por acertar luvas
numa foto em que ninguem marcou luvas. O mAP justo mede cada classe apenas nas imagens
de teste vindas de fontes que anotam aquela classe.

Uso:
  python treinamento/avaliar.py --pesos models/argos_epi_v1.pt --dataset D:/ArgosEPI/datasets/argos_epi_v1
"""
import argparse
import csv
import os
import tempfile
from collections import defaultdict

import yaml

import comum  # antes do ppe_taxonomy: coloca backend/ no sys.path
import ppe_taxonomy as tax

CLASSES = tax.DATASET_CLASSES


def por_classe(metricas):
    """{classe: (instancias, mAP50, mAP50-95)} do resultado de model.val()."""
    return {linha['Class']: (int(linha['Instances']), float(linha['mAP50']), float(linha['mAP50-95']))
            for linha in metricas.summary()}


def main():
    ap = argparse.ArgumentParser(description='Avalia o detector de EPIs (mAP comum e justo)')
    ap.add_argument('--pesos', required=True)
    ap.add_argument('--dataset', required=True)
    ap.add_argument('--imgsz', type=int, default=640)
    ap.add_argument('--device', default='0')
    ap.add_argument('--saida', default='', help='arquivo .md (padrao: <dataset>/avaliacao_<modelo>.md)')
    args = ap.parse_args()

    from ultralytics import YOLO
    modelo = YOLO(args.pesos)
    opcoes = dict(imgsz=args.imgsz, batch=16, device=args.device, plots=False, verbose=False)

    geral = por_classe(modelo.val(data=os.path.join(args.dataset, 'data.yaml'), split='test', **opcoes))

    fontes_classes = comum.carregar_json(os.path.join(args.dataset, 'fontes_classes.json'), {})
    teste_por_fonte = defaultdict(list)
    with open(os.path.join(args.dataset, 'origem.csv'), newline='', encoding='utf-8') as f:
        for linha in csv.DictReader(f):
            if linha['split'] == 'test':
                teste_por_fonte[linha['fonte']].append(
                    os.path.join(args.dataset, 'images', 'test', linha['arquivo'] + '.jpg').replace('\\', '/'))

    justo = defaultdict(lambda: [0.0, 0.0, 0])  # soma mAP50*inst, soma mAP50-95*inst, inst
    with tempfile.TemporaryDirectory() as tmp:
        for fonte, imagens in teste_por_fonte.items():
            lista = os.path.join(tmp, f'teste_{fonte}.txt')
            with open(lista, 'w', encoding='utf-8') as f:
                f.write('\n'.join(imagens) + '\n')
            cfg = os.path.join(tmp, f'teste_{fonte}.yaml')
            with open(cfg, 'w', encoding='utf-8') as f:
                yaml.safe_dump({'path': args.dataset.replace('\\', '/'), 'train': 'images/train',
                                'val': lista.replace('\\', '/'), 'names': dict(enumerate(CLASSES))}, f)
            for classe, (inst, m50, m5095) in por_classe(modelo.val(data=cfg, split='val', **opcoes)).items():
                if classe in fontes_classes.get(fonte, []) and inst:
                    acc = justo[classe]
                    acc[0] += m50 * inst
                    acc[1] += m5095 * inst
                    acc[2] += inst

    nome_modelo = os.path.splitext(os.path.basename(args.pesos))[0]
    linhas = [f"# Avaliação do modelo {nome_modelo}", '',
              f"Dataset: `{args.dataset}` · teste: {sum(len(v) for v in teste_por_fonte.values())} imagens", '',
              '| Classe | Instâncias | mAP50 comum | mAP50 justo | mAP50-95 justo |',
              '|---|---:|---:|---:|---:|']
    medias = []
    for c in CLASSES:
        inst_geral, m50_geral, _ = geral.get(c, (0, 0.0, 0.0))
        s50, s5095, inst = justo.get(c, (0.0, 0.0, 0))
        if inst:
            medias.append((s50 / inst, s5095 / inst))
            linhas.append(f"| {c} | {inst_geral} | {m50_geral:.3f} | {s50 / inst:.3f} | {s5095 / inst:.3f} |")
        else:
            linhas.append(f"| {c} | {inst_geral} | {m50_geral:.3f} | - | - |")
    if medias:
        linhas += ['', f"**Média justa:** mAP50 {sum(m[0] for m in medias) / len(medias):.3f} · "
                       f"mAP50-95 {sum(m[1] for m in medias) / len(medias):.3f}"]
    saida = args.saida or os.path.join(args.dataset, f'avaliacao_{nome_modelo}.md')
    with open(saida, 'w', encoding='utf-8') as f:
        f.write('\n'.join(linhas) + '\n')
    print('\n'.join(linhas))
    print(f"\nRelatorio salvo em {saida}")


if __name__ == '__main__':
    main()
