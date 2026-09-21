"""
pipeline.py - Treinamento completo do detector de EPIs, etapa por etapa.

  baixar    datasets publicos (fontes.py)
  montar    dataset unificado, limpo e sem duplicatas
  professor primeiro modelo, usado para completar rotulos
  pseudo    completa as classes que cada fonte nao anotou
  final     modelo final (copiado para models/)
  avaliar   relatorio com mAP comum e justo

Etapas concluidas ficam em <base>/estado_<nome>.json. Rodar de novo, sem --inicio, continua
de onde parou; se o PC desligou no meio de um treino, ele retoma do ultimo checkpoint.

Uso:
  python treinamento/pipeline.py                              # tudo, ou continua de onde parou
  python treinamento/pipeline.py --inicio pseudo              # refaz a partir do pseudo-rotulo
  python treinamento/pipeline.py --nome teste --rapido        # teste de fumaca (minutos)
"""
import argparse
import os
import subprocess
import sys
import time

import comum

AQUI = os.path.dirname(os.path.abspath(__file__))
ETAPAS = ['baixar', 'montar', 'professor', 'pseudo', 'final', 'avaliar']


def rodar(script, *params):
    cmd = [sys.executable, os.path.join(AQUI, script), *[str(p) for p in params]]
    print('\n>>> ' + ' '.join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser(description='Treinamento completo do detector de EPIs')
    ap.add_argument('--base', default=comum.base_padrao())
    ap.add_argument('--nome', default='argos_epi_v1')
    ap.add_argument('--modelo', default='yolo26s.pt')
    ap.add_argument('--epocas-professor', type=int, default=40)
    ap.add_argument('--epocas', type=int, default=120)
    ap.add_argument('--imgsz', type=int, default=640)
    ap.add_argument('--lote', default='16', help='ver treinar.py')
    ap.add_argument('--inicio', choices=ETAPAS, default=None,
                    help='refaz a partir desta etapa (padrao: continua da primeira nao concluida)')
    ap.add_argument('--fim', choices=ETAPAS, default=ETAPAS[-1])
    ap.add_argument('--sem-nao-comercial', action='store_true')
    ap.add_argument('--rapido', action='store_true', help='poucas imagens e 1 epoca, so para validar o fluxo')
    ap.add_argument('--pasta-dataset', default=None,
                    help='onde fica o dataset montado, ex.: num SSD (padrao: <base>/datasets/<nome>); fica salvo no estado')
    args = ap.parse_args()

    pastas = comum.pastas(args.base)
    professor = args.nome + '_professor'
    nc = ['--sem-nao-comercial'] if args.sem_nao_comercial else []
    estado_path = os.path.join(args.base, f'estado_{args.nome}.json')
    estado = comum.carregar_json(estado_path, {})
    if args.pasta_dataset:
        estado['pasta_dataset'] = os.path.abspath(args.pasta_dataset)
        comum.salvar_json(estado_path, estado)
    # com a base num HD, o dataset montado num SSD evita a GPU ociosa esperando imagens
    dataset = estado.get('pasta_dataset') or os.path.join(pastas['datasets'], args.nome)
    data_yaml = os.path.join(dataset, 'data.yaml')

    inicio = args.inicio or ETAPAS[0]
    etapas = ETAPAS[ETAPAS.index(inicio):ETAPAS.index(args.fim) + 1]
    for etapa in etapas:
        if estado.get(etapa) and etapa != args.inicio:
            print(f"[{etapa}] ja concluida em {estado[etapa]}, pulando")
            continue
        # etapa que ja tinha comecado e foi interrompida (queda de energia, PC desligado): retoma
        retomando = estado.get('_em_andamento') == etapa
        if retomando:
            print(f"[{etapa}] retomando etapa interrompida", flush=True)
        else:
            estado['_em_andamento'] = etapa
            comum.salvar_json(estado_path, estado)
        modo_treino = '--retomar' if retomando else '--novo'
        t0 = time.time()
        if etapa == 'baixar':
            rodar('baixar_datasets.py', '--base', args.base, *nc)
        elif etapa == 'montar':
            extra = ['--limite-por-fonte', 300] if args.rapido else []
            rodar('montar_dataset.py', '--base', args.base, '--nome', args.nome, '--saida', dataset, '--sobrescrever',
                  *nc, *extra)
        elif etapa == 'professor':
            rodar('treinar.py', '--dados', data_yaml, '--nome', professor, '--base', args.base,
                  '--modelo', args.modelo, '--epocas', 1 if args.rapido else args.epocas_professor,
                  '--imgsz', args.imgsz, '--lote', args.lote, modo_treino)
        elif etapa == 'pseudo':
            rodar('pseudo_rotular.py', '--dataset', dataset, '--imgsz', args.imgsz,
                  '--pesos', os.path.join(pastas['runs'], professor, 'weights', 'best.pt'))
        elif etapa == 'final':
            exportar = [] if args.rapido else ['--exportar-para', os.path.join(comum.RAIZ_PROJETO, 'models')]
            rodar('treinar.py', '--dados', data_yaml, '--nome', args.nome, '--base', args.base,
                  '--modelo', args.modelo, '--epocas', 1 if args.rapido else args.epocas,
                  '--imgsz', args.imgsz, '--lote', args.lote, modo_treino, *exportar)
        elif etapa == 'avaliar':
            rodar('avaliar.py', '--pesos', os.path.join(pastas['runs'], args.nome, 'weights', 'best.pt'),
                  '--dataset', dataset, '--imgsz', args.imgsz)
        estado[etapa] = time.strftime('%Y-%m-%d %H:%M:%S')
        estado.pop('_em_andamento', None)
        for seguinte in ETAPAS[ETAPAS.index(etapa) + 1:]:
            estado.pop(seguinte, None)  # etapas seguintes dependem desta: refazer
        comum.salvar_json(estado_path, estado)
        print(f"[{etapa}] concluida em {(time.time() - t0) / 60:.1f} min")


if __name__ == '__main__':
    main()
