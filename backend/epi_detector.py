"""
epi_detector.py - Detector de EPIs e conversao das deteccoes para a taxonomia Argos.

Compartilhado pelo tempo real (processor.py) e pelas analises de video (analysis_jobs.py).
"""
import json
import os
import threading

import torch

import ppe_taxonomy as tax
from ppe_analyzer import supported_items
from yolo_runtime import precision_kwargs

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'models')
# modelo de pose por nivel de detalhe (o Ultralytics baixa na primeira vez)
POSE_MODELS = {'tempo_real': 'yolo26n-pose.pt', 'detalhado': 'yolo26s-pose.pt', 'super': 'yolo26m-pose.pt'}


def resolve_device(req):
    req = str(req or 'auto').lower()
    if req == 'cpu':
        return 'cpu'
    if req in ('auto', '0', 'cuda', 'gpu') and torch.cuda.is_available():
        return '0'
    if req in ('auto', 'dml'):
        try:
            import torch_directml
            return torch_directml.device()
        except Exception:
            pass
    return 'cpu'


def is_cuda(device):
    d = str(device)
    return d in ('0', 'cuda', 'gpu') or d.startswith('cuda')


def pose_model_path(level):
    return os.path.join(MODELS_DIR, POSE_MODELS.get(level, POSE_MODELS['tempo_real']))


def _runtime_path(model_path, device, use_tensorrt):
    if not use_tensorrt or not is_cuda(device):
        return model_path, 'pytorch'
    if os.environ.get('EPI_USE_TENSORRT', '1').strip().lower() in ('0', 'false', 'no', 'off'):
        return model_path, 'pytorch'
    root, ext = os.path.splitext(model_path)
    if ext.lower() == '.engine':
        return model_path, 'tensorrt'
    if os.path.exists(root + '.engine'):
        return root + '.engine', 'tensorrt'
    return model_path, 'pytorch'


# Duas familias de detector rodam pelo mesmo caminho do Ultralytics: a YOLO (convolucional, com NMS)
# e a RT-DETR (Detection Transformer em tempo real: atencao global e saida sem NMS). O YOLO() da
# biblioteca reconhece o checkpoint RT-DETR sozinho; aqui so se descobre qual e, para a tela e o treino.
ARQUITETURAS = {'yolo': 'YOLO', 'detr': 'DETR (transformer)'}


def arquitetura_de(modelo):
    """'detr' quando a cabeca da rede e o decodificador do RT-DETR; 'yolo' nos demais casos."""
    try:
        cabeca = modelo.model.model[-1]
    except Exception:  # TensorRT e outros formatos exportados nao expoem as camadas
        return 'yolo'
    return 'detr' if 'DETR' in type(cabeca).__name__.upper() else 'yolo'


_INFO_MODELOS = {}
_INFO_TRAVA = threading.Lock()


def info_do_modelo(caminho):
    """Classes e arquitetura de um .pt. Fica guardado pela data do arquivo: a lista de modelos
    abria cada peso a cada visita a tela."""
    from ultralytics import YOLO
    chave = (os.path.abspath(caminho), os.path.getmtime(caminho))
    with _INFO_TRAVA:
        info = _INFO_MODELOS.get(chave)
    if info is None:
        modelo = YOLO(caminho)
        names = modelo.names if isinstance(modelo.names, dict) else dict(enumerate(modelo.names))
        info = {'classes': [str(names[i]) for i in sorted(names)], 'arquitetura': arquitetura_de(modelo)}
        with _INFO_TRAVA:
            _INFO_MODELOS[chave] = info
    return {'classes': list(info['classes']), 'arquitetura': info['arquitetura']}


def melhor_modelo_argos(pasta):
    """Nome do modelo Argos (argos_epi*.pt) com maior mAP50 no teste, lido do .json do treino.
    Quem usa o painel nao escolhe modelo: um DETR treinado so vira padrao se detectar melhor
    que o YOLO. Sem nota, vale o mais recente. None quando nao ha modelo Argos."""
    try:
        nomes = [f for f in os.listdir(pasta) if f.startswith('argos_epi') and f.endswith('.pt')]
    except OSError:
        return None
    return melhor_modelo_argos_entre(pasta, nomes)


def nota_do_modelo(caminho):
    """mAP50 no teste gravado pelo treino no <modelo>.json ao lado do .pt; None sem nota."""
    try:
        with open(caminho[:-3] + '.json', encoding='utf-8') as f:
            m = json.load(f).get('map50_teste')
    except (OSError, ValueError, AttributeError):
        return None
    return float(m) if isinstance(m, (int, float)) else None


def melhor_modelo_argos_entre(pasta, nomes):
    """O de maior mAP50 no teste entre 'nomes' (arquivos de 'pasta'); sem nota, o mais recente."""
    def chave(nome):
        caminho = os.path.join(pasta, nome)
        n = nota_do_modelo(caminho)
        return (n if n is not None else -1.0, os.path.getmtime(caminho))

    return max(nomes, key=chave) if nomes else None


def modelo_de_reforco(pasta, principal, arquitetura_principal):
    """Caminho do melhor modelo Argos da OUTRA arquitetura (DETR se o principal e YOLO, e vice-versa),
    usado como segunda opiniao em pessoas encobertas. None quando a equipe ainda nao treinou um.

    So entra quem detecta quase tao bem quanto o principal (80% da nota dele; 0,5 se o principal
    nao tem nota): um detector fraco veria capacete onde nao ha e esconderia uma falta."""
    try:
        nomes = [f for f in os.listdir(pasta) if f.startswith('argos_epi') and f.endswith('.pt')]
    except OSError:
        return None
    base = nota_do_modelo(principal)
    minimo = 0.8 * base if base is not None else 0.5
    outros = [f for f in nomes
              if (nota_do_modelo(os.path.join(pasta, f)) or 0.0) >= minimo
              and arquitetura_do_arquivo(os.path.join(pasta, f)) != arquitetura_principal]
    if not outros:
        return None
    escolhido = melhor_modelo_argos_entre(pasta, outros)
    return os.path.join(pasta, escolhido) if escolhido else None


def arquitetura_do_arquivo(caminho):
    try:
        return info_do_modelo(caminho)['arquitetura']
    except Exception:
        return 'yolo'


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def merge_detections(*groups, iou=0.5):
    """Uniao de listas de deteccoes: caixas da mesma classe sobrepostas ficam com a de maior confianca."""
    kept = []
    for d in sorted((d for g in groups for d in g), key=lambda d: -d['conf']):
        if all(k['cls'] != d['cls'] or _iou(k['box'], d['box']) < iou for k in kept):
            kept.append(d)
    return kept


class EpiDetector:
    """Modelo de EPIs (YOLO ou RT-DETR; Argos, enviado pelo usuario ou COCO) com classes ja interpretadas."""

    def __init__(self, model_path, device='cpu', use_tensorrt=True, log=print):
        from ultralytics import YOLO
        runtime, backend = _runtime_path(model_path, device, use_tensorrt)
        try:
            self.model = YOLO(runtime)
        except Exception:
            if runtime == model_path:
                raise
            log('Falha ao carregar TensorRT; usando modelo PyTorch original')
            runtime, backend = model_path, 'pytorch'
            self.model = YOLO(runtime)
        self.model_path, self.runtime_path, self.backend = model_path, runtime, backend
        self.arquitetura = arquitetura_de(self.model) if backend == 'pytorch' else arquitetura_do_arquivo(model_path)
        self.device = device
        self.half = bool(is_cuda(device) and backend != 'tensorrt')
        names = self.model.names
        self.names = dict(names) if isinstance(names, dict) else dict(enumerate(names))
        self.class_names = [str(self.names[i]) for i in sorted(self.names)]
        self.resolved = tax.resolve_model_classes(self.names)
        self.supported = supported_items(self.resolved)
        self.items = {item for item, _, _ in self.resolved.values()}
        self.knows_ppe = any(known and item != 'person' for item, _, known in self.resolved.values())
        # modelo generico (COCO, 80 classes): classes fora da taxonomia nao sao EPI
        self.is_generic = len(self.names) >= 80 and not self.knows_ppe

    def clean_required(self, required):
        """Tira itens que nao sao EPI (ex.: 'car' salvo por configuracoes antigas de modelos COCO)."""
        return [i for i in required if i in tax.ITEMS or not self.is_generic]

    def useful_for(self, required):
        """Vale rodar? Um modelo COCO puro nao acrescenta nada aos EPIs exigidos."""
        return self.knows_ppe or any(item in self.items for item in required)

    def _convert(self, result, dx=0.0, dy=0.0):
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return []
        out = []
        for xyxy, conf, cls in zip(boxes.xyxy.cpu().tolist(), boxes.conf.cpu().tolist(), boxes.cls.int().cpu().tolist()):
            item, present, known = self.resolved.get(cls, (str(cls), True, False))
            out.append({'item': item, 'present': present, 'known': known, 'conf': round(float(conf), 4),
                        'box': (xyxy[0] + dx, xyxy[1] + dy, xyxy[2] + dx, xyxy[3] + dy),
                        'label': str(self.names.get(cls, cls)), 'cls': cls})
        return out

    def detect(self, img, imgsz=640, conf=0.35, augment=False):
        # o RT-DETR nao tem aumento no teste (TTA); o iou e ignorado por ele, que ja sai sem NMS
        r = self.model.predict(img, imgsz=imgsz, conf=conf, iou=0.5, device=self.device, verbose=False,
                               augment=augment and self.arquitetura != 'detr', **precision_kwargs(self.half))[0]
        return self._convert(r)

    def detect_crops(self, img, boxes, imgsz=640, conf=0.3, margin=0.15, min_px=24):
        """Detector em recortes de cada pessoa ampliados para imgsz: EPIs pequenos ficam visiveis."""
        h, w = img.shape[:2]
        crops, offsets = [], []
        for x1, y1, x2, y2 in boxes:
            mx, my = (x2 - x1) * margin, (y2 - y1) * margin
            cx1, cy1 = int(max(0, x1 - mx)), int(max(0, y1 - my))
            cx2, cy2 = int(min(w, x2 + mx)), int(min(h, y2 + my))
            if cx2 - cx1 < min_px or cy2 - cy1 < min_px:
                continue
            crops.append(img[cy1:cy2, cx1:cx2])
            offsets.append((cx1, cy1))
        if not crops:
            return []
        results = self.model.predict(crops, imgsz=imgsz, conf=conf, iou=0.5, device=self.device, verbose=False,
                                     **precision_kwargs(self.half))
        out = []
        for r, (dx, dy) in zip(results, offsets):
            out.extend(self._convert(r, dx, dy))
        return out


def run_employee_model(model, img, device, half):
    """Rostos reconhecidos pelo modelo funcionarios.pt: [{'nome', 'confidence', 'bbox'}]."""
    r = model.predict(img, device=device, verbose=False, conf=0.4, iou=0.45, **precision_kwargs(half))[0]
    if r.boxes is None or len(r.boxes) == 0:
        return []
    return [{'nome': str(model.names[c]).replace('_', ' '), 'confidence': round(float(p), 4),
             'bbox': [round(float(v), 2) for v in xyxy]}
            for xyxy, p, c in zip(r.boxes.xyxy.cpu().tolist(), r.boxes.conf.cpu().tolist(), r.boxes.cls.int().cpu().tolist())]
