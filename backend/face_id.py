"""
face_id.py - Reconhecimento de rosto por embedding (SCRFD + ArcFace)

Antes o Argos treinava um YOLO classificador por funcionario: exigia 3 fotos de
cada um, demorava minutos e obrigava a retreinar tudo a cada admissao. Aqui o
caminho e o mesmo do projeto DEEPFAKE: o detector acha o rosto, o modelo de
reconhecimento devolve um vetor de 512 dimensoes, e reconhecer e comparar
vetores. Cadastrar alguem passa a ser inserir uma linha -- sem treino.

Modelos (pacote buffalo_l do InsightFace, em models/insightface/):
  det_10g.onnx    deteccao SCRFD
  w600k_r50.onnx  reconhecimento ArcFace R50 -> 512 dimensoes

Por que nao usar o pacote insightface: a versao 0.7.3 nao tem wheel, so codigo
fonte. Exigiria compilador C++ e mais dez dependencias (albumentations,
scikit-image, matplotlib...) dentro da imagem. Rodando os dois ONNX direto no
onnxruntime o resultado e o mesmo -- ha um teste que confere os embeddings
contra o insightface -- com uma fracao do peso.

O vetor sai normalizado (norma 1), entao a distancia L2 entre dois vetores vira
medida de semelhanca: mesma pessoa fica abaixo de ~1.0, o limiar usado no
DEEPFAKE. Como sao unitarios, vale dist^2 = 2 - 2*cos, isto e, dist 1.0
equivale a cosseno 0.5.
"""
import os
import threading

import numpy as np

# Mesmo limiar do projeto DEEPFAKE. Abaixo disso, mesma pessoa.
LIMIAR_DISTANCIA = float(os.environ.get('ARGOS_FACE_DIST', '1.0'))
# Margem minima entre o melhor e o segundo melhor candidato. Sem isso, dois
# funcionarios parecidos ficam trocando de nome de um quadro para o outro.
MARGEM_MINIMA = float(os.environ.get('ARGOS_FACE_MARGEM', '0.06'))
DIM = 512
DET_THRESH = float(os.environ.get('ARGOS_FACE_DET', '0.5'))
NMS_THRESH = 0.4

# Gabarito de 5 pontos do ArcFace, em 112x112. O rosto e alinhado nele antes
# de virar vetor; sem esse alinhamento o embedding perde muita precisao.
_GABARITO = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                      [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)

_lock = threading.RLock()
_sessoes = None          # (detector, reconhecedor, tamanho_entrada)
_erro_carga = None


def _raiz_modelos() -> str:
    return os.environ.get('INSIGHTFACE_ROOT') or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'models', 'insightface')


def _caminho(nome) -> str:
    return os.path.join(_raiz_modelos(), 'models', 'buffalo_l', nome)


def _provedores(device: str):
    """Espelha a escolha de hardware do resto do sistema."""
    d = str(device or 'cpu').lower()
    if d in ('0', 'cuda', 'gpu') or d.isdigit():
        return ['CUDAExecutionProvider', 'CPUExecutionProvider']
    if d == 'dml':
        return ['DmlExecutionProvider', 'CPUExecutionProvider']
    return ['CPUExecutionProvider']


def disponivel() -> bool:
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return os.path.exists(_caminho('det_10g.onnx')) and os.path.exists(_caminho('w600k_r50.onnx'))


def erro() -> str:
    if _erro_carga:
        return _erro_carga
    if not disponivel():
        faltando = [n for n in ('det_10g.onnx', 'w600k_r50.onnx') if not os.path.exists(_caminho(n))]
        if faltando:
            return 'modelos ausentes em models/insightface/models/buffalo_l: ' + ', '.join(faltando)
        return 'onnxruntime nao instalado'
    return ''


def carregar(device='cpu'):
    """Carrega os dois ONNX uma vez por processo. None se nao der."""
    global _sessoes, _erro_carga
    if _sessoes is not None:
        return _sessoes
    with _lock:
        if _sessoes is not None:
            return _sessoes
        try:
            import onnxruntime as ort
            prov = _provedores(device)
            op = ort.SessionOptions()
            op.log_severity_level = 3
            det = ort.InferenceSession(_caminho('det_10g.onnx'), sess_options=op, providers=prov)
            rec = ort.InferenceSession(_caminho('w600k_r50.onnx'), sess_options=op, providers=prov)
            forma = rec.get_inputs()[0].shape          # [1,3,112,112]
            lado = int(forma[2]) if isinstance(forma[2], int) else 112
            _sessoes = (det, rec, lado)
            _erro_carga = None
            print(f'[face_id] SCRFD + ArcFace prontos ({det.get_providers()[0]})')
        except Exception as e:
            _erro_carga = str(e)
            print(f'[face_id] indisponivel: {e}')
            return None
    return _sessoes


def normalizar(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32).ravel()
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


# ── SCRFD ───────────────────────────────────────────────────────────

def _centros(altura, largura, stride, n_ancoras, _cache={}):
    chave = (altura, largura, stride)
    c = _cache.get(chave)
    if c is None:
        ys, xs = np.mgrid[:altura, :largura]
        c = np.stack([xs, ys], axis=-1).astype(np.float32).reshape(-1, 2) * stride
        if n_ancoras > 1:                 # o SCRFD repete o centro por ancora
            c = np.repeat(c, n_ancoras, axis=0)
        if len(_cache) < 60:
            _cache[chave] = c
    return c


def _dist_para_caixa(centros, dist):
    x1 = centros[:, 0] - dist[:, 0]
    y1 = centros[:, 1] - dist[:, 1]
    x2 = centros[:, 0] + dist[:, 2]
    y2 = centros[:, 1] + dist[:, 3]
    return np.stack([x1, y1, x2, y2], axis=-1)


def _dist_para_pontos(centros, dist):
    pts = []
    for i in range(0, dist.shape[1], 2):
        pts.append(centros[:, 0] + dist[:, i])
        pts.append(centros[:, 1] + dist[:, i + 1])
    return np.stack(pts, axis=-1).reshape(dist.shape[0], -1, 2)


def _nms(caixas, notas, limiar):
    x1, y1, x2, y2 = caixas[:, 0], caixas[:, 1], caixas[:, 2], caixas[:, 3]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    ordem = notas.argsort()[::-1]
    fica = []
    while ordem.size > 0:
        i = ordem[0]
        fica.append(i)
        xx1 = np.maximum(x1[i], x1[ordem[1:]])
        yy1 = np.maximum(y1[i], y1[ordem[1:]])
        xx2 = np.minimum(x2[i], x2[ordem[1:]])
        yy2 = np.minimum(y2[i], y2[ordem[1:]])
        inter = np.maximum(0.0, xx2 - xx1 + 1) * np.maximum(0.0, yy2 - yy1 + 1)
        iou = inter / (areas[i] + areas[ordem[1:]] - inter)
        ordem = ordem[1:][iou <= limiar]
    return fica


def _detectar_rostos(img, det, tamanho=640, limiar=DET_THRESH):
    """[(caixa, nota, 5 pontos)] em coordenadas da imagem original."""
    import cv2
    h0, w0 = img.shape[:2]
    escala = min(tamanho / max(h0, 1), tamanho / max(w0, 1))
    nw, nh = int(round(w0 * escala)), int(round(h0 * escala))
    tela = np.zeros((tamanho, tamanho, 3), dtype=np.uint8)   # letterbox no canto
    tela[:nh, :nw] = cv2.resize(img, (nw, nh))
    entrada = cv2.dnn.blobFromImage(tela, 1.0 / 128.0, (tamanho, tamanho),
                                    (127.5, 127.5, 127.5), swapRB=True)
    saidas = det.run(None, {det.get_inputs()[0].name: entrada})

    fmc, ancoras = 3, 2                    # det_10g: 3 escalas, 2 ancoras
    caixas, notas, pontos = [], [], []
    for i, stride in enumerate((8, 16, 32)):
        nota = saidas[i].reshape(-1)
        bbox = saidas[i + fmc].reshape(-1, 4) * stride
        kps = saidas[i + fmc * 2].reshape(-1, 10) * stride
        lado = tamanho // stride
        centros = _centros(lado, lado, stride, ancoras)
        manter = np.where(nota >= limiar)[0]
        if manter.size == 0:
            continue
        caixas.append(_dist_para_caixa(centros[manter], bbox[manter]))
        notas.append(nota[manter])
        pontos.append(_dist_para_pontos(centros[manter], kps[manter]))

    if not caixas:
        return []
    caixas = np.vstack(caixas) / escala
    notas = np.concatenate(notas)
    pontos = np.vstack(pontos) / escala
    fica = _nms(caixas, notas, NMS_THRESH)
    return [(caixas[i], float(notas[i]), pontos[i]) for i in fica]


def _alinhar(img, pontos, lado=112):
    """Recorta o rosto no gabarito do ArcFace a partir dos 5 pontos."""
    import cv2
    alvo = _GABARITO * (lado / 112.0)
    M, _ = cv2.estimateAffinePartial2D(pontos.astype(np.float32), alvo, method=cv2.LMEDS)
    if M is None:
        return None
    return cv2.warpAffine(img, M, (lado, lado), borderValue=0)


def _embedding(rosto_alinhado, rec) -> np.ndarray:
    import cv2
    blob = cv2.dnn.blobFromImage(rosto_alinhado, 1.0 / 127.5, rosto_alinhado.shape[:2][::-1],
                                 (127.5, 127.5, 127.5), swapRB=True)
    saida = rec.run(None, {rec.get_inputs()[0].name: blob})[0]
    return normalizar(saida)


# ── API usada pelo resto do sistema ─────────────────────────────────

def detectar(img, device='cpu') -> list:
    """[{'bbox':[x1,y1,x2,y2], 'embedding': vetor normalizado, 'conf': float}]"""
    s = carregar(device)
    if s is None or img is None:
        return []
    det, rec, lado = s
    try:
        achados = _detectar_rostos(img, det)
    except Exception as e:
        print(f'[face_id] falha ao detectar: {e}')
        return []
    saida = []
    for caixa, nota, pontos in achados:
        try:
            alinhado = _alinhar(img, pontos, lado)
            if alinhado is None:
                continue
            saida.append({'bbox': [float(v) for v in caixa], 'conf': round(nota, 4),
                          'embedding': _embedding(alinhado, rec)})
        except Exception:
            continue
    return saida


def embedding_de_foto(caminho: str, device='cpu'):
    """Vetor do maior rosto da foto de cadastro. (None, motivo) quando nao da."""
    try:
        import cv2
    except ImportError:
        return None, 'opencv ausente'
    if not disponivel():
        return None, erro() or 'reconhecimento facial indisponivel'
    img = cv2.imread(caminho)
    if img is None:
        return None, 'nao foi possivel abrir a imagem'
    rostos = detectar(img, device)
    if not rostos:
        return None, 'nenhum rosto encontrado na foto'
    # o maior rosto e o do funcionario; os menores costumam ser de fundo
    maior = max(rostos, key=lambda r: (r['bbox'][2] - r['bbox'][0]) * (r['bbox'][3] - r['bbox'][1]))
    return maior['embedding'], ''


class Galeria:
    """Embeddings cadastrados, prontos para comparacao.

    Guarda uma matriz (N x 512) e, para cada linha, de qual funcionario ela e.
    Um funcionario pode ter varias fotos; vale a melhor delas, como no DEEPFAKE,
    que compara contra todos os embeddings da identidade e fica com o menor.
    """

    def __init__(self):
        self.matriz = np.zeros((0, DIM), dtype=np.float32)
        self.donos = []
        self.nomes = {}

    @property
    def vazia(self) -> bool:
        return self.matriz.shape[0] == 0

    def adicionar(self, func_id, nome, embedding):
        v = normalizar(embedding).reshape(1, -1)
        if v.shape[1] != DIM:
            return
        self.matriz = np.vstack([self.matriz, v]) if self.matriz.size else v
        self.donos.append(func_id)
        self.nomes[func_id] = nome

    def identificar(self, embedding):
        """(func_id, nome, distancia) ou (None, None, dist) quando nao reconhece."""
        if self.vazia:
            return None, None, float('inf')
        v = normalizar(embedding)
        # vetores unitarios: a distancia L2 sai direto do produto escalar
        cos = np.clip(self.matriz @ v, -1.0, 1.0)
        dists = np.sqrt(np.maximum(0.0, 2.0 - 2.0 * cos))

        por_func = {}                       # menor distancia por funcionario
        for i, fid in enumerate(self.donos):
            d = float(dists[i])
            if d < por_func.get(fid, float('inf')):
                por_func[fid] = d
        ordenado = sorted(por_func.items(), key=lambda kv: kv[1])
        melhor_id, melhor = ordenado[0]
        if melhor > LIMIAR_DISTANCIA:
            return None, None, melhor
        # com dois parecidos na galeria, so aceita se um se destacar
        if len(ordenado) > 1 and (ordenado[1][1] - melhor) < MARGEM_MINIMA:
            return None, None, melhor
        return melhor_id, self.nomes.get(melhor_id), melhor


def galeria_de_linhas(linhas) -> Galeria:
    """Monta a galeria a partir das linhas de func_fotos vindas do banco."""
    g = Galeria()
    for l in linhas or []:
        bruto = l.get('embedding')
        if not bruto:
            continue
        try:
            v = np.frombuffer(bytes(bruto), dtype=np.float32)
        except Exception:
            continue
        if v.size != DIM:
            continue
        g.adicionar(l.get('func_id'), l.get('nome') or '', v)
    return g
