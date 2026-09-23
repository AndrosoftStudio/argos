"""
treinar_detr.py - Treina o DETR de EPIs (RT-DETR) com o MESMO dataset do argos_epi_v1.

Nao baixa nem monta nada: usa o dataset que o pipeline.py ja deixou pronto (com os
pseudo-rotulos), entao a nota de teste do DETR e comparavel com a do YOLO. No fim copia
o modelo para models/ com o .json da nota; o servidor usa como padrao o modelo com a maior
nota e, sendo de outra arquitetura, como segunda opiniao em trabalhador encoberto.

Retoma sozinho depois de queda de energia: e so rodar de novo.

Uso: dois cliques em treinamento/treinar_detr.bat
     python treinamento/treinar_detr.py [--epocas 72] [--lote 8] [--device 0]
"""
import argparse
import os
import subprocess
import sys

import comum

NOME = 'argos_epi_detr_v1'


def achar_dataset(base, nome):
    """data.yaml do dataset montado pelo pipeline.py para 'nome' (pode estar num SSD, anotado no estado)."""
    candidatos = []
    for raiz in dict.fromkeys([base, r'D:\ArgosEPI', r'C:\ArgosEPI']):
        estado = comum.carregar_json(os.path.join(raiz, f'estado_{nome}.json'), {})
        if estado.get('pasta_dataset'):
            candidatos.append(estado['pasta_dataset'])
        candidatos.append(os.path.join(comum.pastas(raiz)['datasets'], nome))
    for pasta in candidatos:
        if os.path.exists(os.path.join(pasta, 'data.yaml')):
            return pasta
    return None


def nota(caminho_json):
    info = comum.carregar_json(caminho_json, {})
    m = info.get('map50_teste')
    return float(m) if isinstance(m, (int, float)) else None


def main():
    ap = argparse.ArgumentParser(description='Treina o DETR de EPIs com o dataset do argos_epi_v1')
    ap.add_argument('--base', default=comum.base_padrao())
    ap.add_argument('--dataset-de', default='argos_epi_v1', help='nome do treino cujo dataset sera usado')
    ap.add_argument('--epocas', type=int, default=72, help='72 e o ciclo padrao do RT-DETR (6x)')
    ap.add_argument('--paciencia', type=int, default=20)
    ap.add_argument('--lote', default='8', help='cai sozinho pela metade se faltar memoria na GPU')
    ap.add_argument('--imgsz', type=int, default=640)
    ap.add_argument('--device', default='0')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--modelo', default='rtdetr-l.pt')
    ap.add_argument('--exportar-para', default=os.path.join(comum.RAIZ_PROJETO, 'models'))
    ap.add_argument('--sem-graficos', action='store_true')
    args = ap.parse_args()

    dataset = achar_dataset(args.base, args.dataset_de)
    if not dataset:
        sys.exit(f"Nao achei o dataset do {args.dataset_de} (data.yaml). Rode antes treinar_tudo.bat "
                 f"ou defina ARGOS_TREINO_DIR com a pasta dos dados de treino.")
    print(f"Dataset: {dataset}\nModelo inicial: {args.modelo}\nEpocas: {args.epocas}  lote: {args.lote}\n", flush=True)

    aqui = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(aqui, 'treinar.py'), '--dados', os.path.join(dataset, 'data.yaml'),
           '--nome', NOME, '--base', args.base, '--arquitetura', 'detr', '--modelo', args.modelo,
           '--epocas', str(args.epocas), '--paciencia', str(args.paciencia), '--lote', str(args.lote),
           '--imgsz', str(args.imgsz), '--device', args.device, '--workers', str(args.workers),
           '--retomar', '--exportar-para', args.exportar_para]
    if args.sem_graficos:
        cmd.append('--sem-graficos')
    subprocess.run(cmd, check=True)

    melhor = os.path.join(comum.pastas(args.base)['runs'], NOME, 'weights', 'best.pt')
    subprocess.run([sys.executable, os.path.join(aqui, 'avaliar.py'), '--pesos', melhor, '--dataset', dataset,
                    '--imgsz', str(args.imgsz), '--device', args.device], check=False)

    detr = nota(os.path.join(args.exportar_para, NOME + '.json'))
    yolo = nota(os.path.join(args.exportar_para, args.dataset_de + '.json'))
    print('\n=== Resultado (mesmo conjunto de teste) ===')
    print(f"  YOLO ({args.dataset_de}): mAP50 = {yolo if yolo is not None else '?'}")
    print(f"  DETR ({NOME}): mAP50 = {detr if detr is not None else '?'}")
    if detr is not None and yolo is not None:
        if detr > yolo:
            print('  O DETR detectou melhor: o sistema passa a usa-lo como padrao sozinho.')
        elif detr >= 0.8 * yolo:
            print('  O YOLO segue como padrao; o DETR entra como segunda opiniao em trabalhador encoberto.')
        else:
            print('  O DETR ficou bem abaixo: o sistema segue so com o YOLO (o DETR nao e usado).')
    print('  Reinicie o Argos para as cameras carregarem o modelo novo.')


if __name__ == '__main__':
    main()
