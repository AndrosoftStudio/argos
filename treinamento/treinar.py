"""
treinar.py - Treina o detector de EPIs do Argos EPI (Ultralytics YOLO26 ou RT-DETR).

Arquiteturas (--arquitetura):
  yolo   rede convolucional com NMS (padrao, yolo26s.pt): mais leve, roda bem sem GPU
  detr   RT-DETR, o Detection Transformer em tempo real (rtdetr-l.pt): atencao global sobre a
         imagem inteira e saida sem NMS. Pede mais memoria de GPU (lote 8 a 640 px em 8 GB)
         e mais epocas para convergir. O servidor reconhece o modelo sozinho.

Checkpoints (em <base>/runs/<nome>/weights/):
  last.pt      gravado a cada epoca; e dele que o treino retoma
  epochN.pt    copia a cada --salvar-a-cada epocas, para o caso de o last.pt corromper
               (queda de energia no meio da gravacao)
  best.pt      melhor epoca na validacao

Uso:
  python treinamento/treinar.py --dados D:/ArgosEPI/datasets/argos_epi_v1/data.yaml --nome argos_epi_v1
  python treinamento/treinar.py ... --retomar     # continua um treino interrompido, se houver
  python treinamento/treinar.py ... --novo        # guarda o treino anterior com outro nome e comeca do zero
  python treinamento/treinar.py --dados ... --nome argos_epi_detr_v1 --arquitetura detr
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys
import time

import comum


# pesos iniciais, lote e ajustes de cada arquitetura. O RT-DETR segue o artigo original:
# AdamW com taxa 1e-4; com o SGD do YOLO o transformer demora muito mais a convergir.
PADROES = {
    'yolo': {'modelo': 'yolo26s.pt', 'lote': 16, 'extra': {}},
    'detr': {'modelo': 'rtdetr-l.pt', 'lote': 8, 'extra': {'optimizer': 'AdamW', 'lr0': 0.0001, 'weight_decay': 0.0001}},
}


def _lote(valor):
    v = float(valor)
    return int(v) if v >= 1 or v == -1 else v


def _estado_checkpoint(caminho):
    """'inacabado', 'concluido' ou None (ausente ou corrompido).

    O Ultralytics zera 'epoch' e remove o otimizador ao terminar o treino."""
    if not os.path.exists(caminho):
        return None
    try:
        import torch
        ck = torch.load(caminho, map_location='cpu', weights_only=False)
    except Exception:
        return None
    return 'inacabado' if ck.get('epoch', -1) != -1 and ck.get('optimizer') is not None else 'concluido'


def _checkpoint_para_retomar(pasta_pesos):
    """Estado do last.pt. Se ele sumiu ou corrompeu, recupera o epochN.pt mais recente que esteja integro."""
    ultimo = os.path.join(pasta_pesos, 'last.pt')
    estado = _estado_checkpoint(ultimo)
    if estado:
        return estado
    periodicos = sorted(glob.glob(os.path.join(pasta_pesos, 'epoch*.pt')),
                        key=lambda p: int(re.sub(r'\D', '', os.path.basename(p)) or -1), reverse=True)
    for p in periodicos:
        if _estado_checkpoint(p) == 'inacabado':
            print(f"last.pt ausente ou corrompido; recuperando {os.path.basename(p)}")
            shutil.copy2(p, ultimo)
            return 'inacabado'
    return None


def _impedir_suspensao():
    """Evita que o Windows suspenda por inatividade durante o treino (vale so enquanto o processo roda)."""
    if os.name == 'nt':
        import ctypes
        es_continuous, es_system_required = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)


def main():
    ap = argparse.ArgumentParser(description='Treina o detector de EPIs')
    ap.add_argument('--dados', required=True, help='data.yaml do dataset montado')
    ap.add_argument('--nome', required=True)
    ap.add_argument('--base', default=comum.base_padrao())
    ap.add_argument('--arquitetura', choices=sorted(PADROES), default='yolo',
                    help='yolo (convolucional) ou detr (RT-DETR, Detection Transformer)')
    ap.add_argument('--modelo', default=None,
                    help='pesos iniciais (nome oficial e baixado automaticamente); padrao conforme a arquitetura')
    ap.add_argument('--epocas', type=int, default=120)
    ap.add_argument('--imgsz', type=int, default=640)
    # O AutoBatch (-1 ou fracao da VRAM) superestima a memoria do YOLO26: numa GPU de 8 GB escolheu lote 7,
    # que usava so 2,2 GB e deixava a GPU pela metade. 16 cabe com folga em 8 GB a 640 px (o RT-DETR, 8).
    ap.add_argument('--lote', type=_lote, default=None, help='inteiro, -1 (AutoBatch) ou fracao da VRAM (ex.: 0.75)')
    ap.add_argument('--paciencia', type=int, default=40)
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--cache', choices=['nao', 'ram', 'disk'], default='nao')
    ap.add_argument('--device', default='0')
    ap.add_argument('--salvar-a-cada', type=int, default=5, help='copia epochN.pt a cada N epocas (0 desliga)')
    modo = ap.add_mutually_exclusive_group()
    modo.add_argument('--retomar', action='store_true', help='continua o treino interrompido; se ja terminou, so avalia')
    modo.add_argument('--novo', action='store_true', help='renomeia o treino anterior com este nome e comeca do zero')
    ap.add_argument('--rapido', action='store_true', help='teste de fumaca com 2%% dos dados')
    ap.add_argument('--sem-graficos', action='store_true',
                    help='nao desenha os graficos do treino (o Ultralytics baixa uma fonte para desenha-los)')
    ap.add_argument('--exportar-para', default='', help='pasta onde copiar o modelo final (ex.: models/)')
    args = ap.parse_args()
    padrao = PADROES[args.arquitetura]
    args.modelo = args.modelo or padrao['modelo']
    args.lote = padrao['lote'] if args.lote is None else args.lote

    from ultralytics import YOLO  # carrega YOLO e RT-DETR; o treinador certo vem do proprio checkpoint

    _impedir_suspensao()
    projeto = comum.pastas(args.base)['runs']
    pasta_run = os.path.join(projeto, args.nome)
    pasta_pesos = os.path.join(pasta_run, 'weights')
    if args.novo and os.path.isdir(pasta_run):
        anterior = pasta_run + time.strftime('_anterior_%Y%m%d_%H%M%S')
        os.replace(pasta_run, anterior)
        print(f"Treino anterior guardado em {anterior}")

    estado = _checkpoint_para_retomar(pasta_pesos) if args.retomar else None
    if estado == 'inacabado':
        print(f"Retomando {os.path.join(pasta_pesos, 'last.pt')}")
        modelo = YOLO(os.path.join(pasta_pesos, 'last.pt'))
        modelo.train(resume=True)
        pasta_run = str(modelo.trainer.save_dir)
    elif estado == 'concluido' and os.path.exists(os.path.join(pasta_pesos, 'best.pt')):
        print('Treino ja concluido antes da interrupcao; indo direto para a avaliacao')
    else:
        pesos = args.modelo
        if not os.path.exists(pesos):
            os.makedirs(comum.pastas(args.base)['pesos'], exist_ok=True)
            pesos = os.path.join(comum.pastas(args.base)['pesos'], os.path.basename(pesos))
        modelo = YOLO(pesos)
        modelo.train(
            data=args.dados, epochs=args.epocas, imgsz=args.imgsz, batch=args.lote,
            project=projeto, name=args.nome, exist_ok=True, patience=args.paciencia,
            device=args.device, workers=args.workers,
            cache=False if args.cache == 'nao' else args.cache,
            cos_lr=True, close_mosaic=15, mixup=0.1, degrees=5.0,
            fraction=0.02 if args.rapido else 1.0, seed=42, plots=not args.sem_graficos,
            save_period=args.salvar_a_cada if args.salvar_a_cada > 0 else -1,
            **padrao['extra'],
        )
        pasta_run = str(modelo.trainer.save_dir)

    melhor = os.path.join(pasta_run, 'weights', 'best.pt')
    if not os.path.exists(melhor):
        sys.exit('best.pt nao foi gerado')

    final = YOLO(melhor)
    metricas = final.val(data=args.dados, split='test', imgsz=args.imgsz, batch=padrao['lote'], device=args.device,
                         plots=False, verbose=False)
    resumo = {
        'nome': args.nome,
        'arquitetura': args.arquitetura,
        'classes': [final.names[i] for i in sorted(final.names)],
        'map50_teste': round(float(metricas.box.map50), 4),
        'map50_95_teste': round(float(metricas.box.map), 4),
        'precisao_teste': round(float(metricas.box.mp), 4),
        'recall_teste': round(float(metricas.box.mr), 4),
        'dados': args.dados, 'modelo_base': args.modelo, 'epocas': args.epocas, 'imgsz': args.imgsz,
        'run': pasta_run, 'treinado_em': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    comum.salvar_json(os.path.join(pasta_run, 'resumo.json'), resumo)
    if args.exportar_para:
        os.makedirs(args.exportar_para, exist_ok=True)
        destino = os.path.join(args.exportar_para, f"{args.nome}.pt")
        shutil.copy2(melhor, destino)
        comum.salvar_json(destino[:-3] + '.json', resumo)
        print(f"Modelo copiado para {destino}")
    print(json.dumps(resumo, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
