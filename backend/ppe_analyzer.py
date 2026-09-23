"""
ppe_analyzer.py - Analise por pessoa: pose, EPIs por regiao do corpo, postura e movimento.

Por frame:
  1. o modelo de pose (YOLO26-pose) acha cada pessoa, 17 pontos do corpo e um ID de rastreio;
  2. cada deteccao de EPI e ligada a pessoa cuja regiao do corpo (cabeca, tronco, maos, pes)
     contem a caixa;
  3. cada EPI exigido recebe uma evidencia: tem / ausencia detectada / nao aparece / nao da para ver;
  4. uma votacao temporal por ID estabiliza o estado e mede ha quanto tempo dura a violacao;
  5. os pontos do corpo dao postura (em pe, agachado, curvado, caido), direcao da cabeca e velocidade.
"""
import math
import time
import unicodedata
from bisect import bisect_left, bisect_right
from collections import deque

import cv2
import numpy as np

import ppe_taxonomy as tax
from yolo_runtime import precision_kwargs

NOSE, L_EYE, R_EYE, L_EAR, R_EAR = range(5)
L_SHO, R_SHO, L_ELB, R_ELB, L_WRI, R_WRI = range(5, 11)
L_HIP, R_HIP, L_KNE, R_KNE, L_ANK, R_ANK = range(11, 17)

# (ponto a, ponto b, regiao que colore o segmento)
SKELETON = [
    (NOSE, L_EYE, 'head'), (NOSE, R_EYE, 'head'), (L_EYE, L_EAR, 'head'), (R_EYE, R_EAR, 'head'),
    (L_SHO, R_SHO, 'torso'), (L_SHO, L_HIP, 'torso'), (R_SHO, R_HIP, 'torso'), (L_HIP, R_HIP, 'torso'),
    (L_SHO, L_ELB, 'hands'), (L_ELB, L_WRI, 'hands'), (R_SHO, R_ELB, 'hands'), (R_ELB, R_WRI, 'hands'),
    (L_HIP, L_KNE, 'feet'), (L_KNE, L_ANK, 'feet'), (R_HIP, R_KNE, 'feet'), (R_KNE, R_ANK, 'feet'),
]

DEFAULT_CONFIG = {
    'kp_conf': 0.35,       # confianca minima de um ponto do corpo
    'window_sec': 1.5,     # janela da votacao temporal
    'min_obs': 3,          # observacoes necessarias para decidir
    'ok_score': 0.3,
    'missing_score': -0.35,
    'alert_sec': 2.0,      # violacao vira alerta depois deste tempo
    'fall_sec': 1.5,       # tempo caido para alertar possivel queda
    'forget_sec': 5.0,     # descarta IDs que sumiram
    'memoria_encoberto_sec': 12.0,  # parte do corpo escondida mantem o ultimo estado conhecido
}
# altura minima da pessoa (px) para concluir que um EPI nao esta la sem uma deteccao negativa.
# maos e pes pedem mais pixels que cabeca e tronco porque luva e calcado sao objetos pequenos;
# 160px exigia a pessoa quase inteira na tela e deixava luva sem avaliar na maioria das cenas.
MIN_PERSON_PX = {'head': 90, 'torso': 90, 'body': 90, 'hands': 130, 'feet': 130}
# peso de cada evidencia na votacao
WEIGHTS = {'detectado': 1.0, 'ausencia_detectada': -1.0, 'inferido': -0.5, 'nao_visivel': 0.0}

POSTURE_LABELS = {'em_pe': 'em pé', 'sentado': 'sentado', 'agachado': 'agachado', 'curvado': 'curvado',
                  'caido': 'caído', 'desconhecida': 'postura ?'}
HEAD_LABELS = {'frente': 'de frente', 'virada_direita': 'virada à direita', 'virada_esquerda': 'virada à esquerda',
               'perfil_direita': 'perfil direito', 'perfil_esquerda': 'perfil esquerdo', 'de_costas': 'de costas',
               'desconhecida': '?'}
STATE_COLORS = {'ok': (72, 187, 88), 'faltando': (48, 48, 220), 'verificando': (0, 176, 245),
                'nao_visivel': (150, 150, 150)}  # BGR
_STATE_RANK = {'faltando': 3, 'verificando': 2, 'ok': 1, 'nao_visivel': 0}


# ── Geometria ───────────────────────────────────────────────────────
def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _ioa(inner, outer):
    """Fracao da caixa 'inner' que fica dentro de 'outer'."""
    ix = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    iy = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    a = _area(inner)
    return ix * iy / a if a > 0 else 0.0


def _box(cx, cy, w, h):
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def _dist(k, a, b):
    return float(math.hypot(k[a, 0] - k[b, 0], k[a, 1] - k[b, 1]))


def _joint_angle(k, a, b, c):
    """Angulo em b (graus) entre b->a e b->c."""
    v1, v2 = k[a, :2] - k[b, :2], k[c, :2] - k[b, :2]
    n = float(np.linalg.norm(v1) * np.linalg.norm(v2))
    if n <= 0:
        return 180.0
    return math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(v1, v2)) / n))))


def body_regions(box, k, frame_w, frame_h, thr=DEFAULT_CONFIG['kp_conf']):
    """{regiao: ([caixas], visivel)} onde cada tipo de EPI deve aparecer.

    A cabeca e ancorada nos ombros: de perfil o modelo de pose costuma errar nariz e olhos,
    e os ombros sao bem mais estaveis."""
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    vis = k[:, 2] >= thr
    sho = [i for i in (L_SHO, R_SHO) if vis[i]]
    hips = [i for i in (L_HIP, R_HIP) if vis[i]]
    face = [i for i in (NOSE, L_EYE, R_EYE, L_EAR, R_EAR) if vis[i]]
    sw = max(_dist(k, L_SHO, R_SHO) if len(sho) == 2 else 0.0, bw * 0.35)
    regions = {'body': ([tuple(box)], True)}

    # cabeca, com folga acima (capacete) e abaixo (mascara, protetor facial)
    if sho:
        sx, sy = float(k[sho, 0].mean()), float(k[sho, 1].mean())
        hx = float(k[face, 0].mean()) if face else sx
        top, bottom = sy - 1.15 * sw, sy - 0.05 * sw
        if face:
            fy = float(k[face, 1].mean())
            top, bottom = min(top, fy - 0.75 * sw), max(bottom, fy + 0.45 * sw)
        head = (hx - 0.55 * sw, top, hx + 0.55 * sw, bottom)
    elif face:
        xs, ys = k[face, 0], k[face, 1]
        s = max(float(xs.max() - xs.min()) * 2.2, bw * 0.4, 12.0)
        fx, fy = float(xs.mean()), float(ys.mean())
        head = (fx - 0.65 * s, fy - 0.95 * s, fx + 0.65 * s, fy + 0.7 * s)
    else:
        head = (x1, y1, x2, y1 + bh * 0.25)
    head_visible = bool(sho or face) and _ioa(head, (0, 0, frame_w, frame_h)) >= 0.6 and (bool(face) or y1 > 2)
    regions['head'] = ([head], head_visible)

    if sho and (hips or bh > 2.2 * sw):
        pts = k[sho + hips, :2]
        bottom = float(pts[:, 1].max()) + 0.15 * sw if hips else float(k[sho, 1].mean()) + 1.4 * sw
        regions['torso'] = ([(float(pts[:, 0].min()) - 0.25 * sw, float(pts[:, 1].min()) - 0.1 * sw,
                              float(pts[:, 0].max()) + 0.25 * sw, bottom)], True)
    else:
        regions['torso'] = ([(x1, y1 + bh * 0.2, x2, y1 + bh * 0.65)], False)

    # maos: o punho e o ponto menos confiavel do modelo de pose (mao pequena, em
    # movimento, muitas vezes fora de foco). Sem um plano B, luva nenhuma era avaliada.
    hands = []
    for wri, elb, ombro in ((L_WRI, L_ELB, L_SHO), (R_WRI, R_ELB, R_SHO)):
        if vis[wri]:
            if vis[elb]:
                fore = max(_dist(k, wri, elb), 0.3 * sw)
                ux, uy = (k[wri, 0] - k[elb, 0]) / fore, (k[wri, 1] - k[elb, 1]) / fore
            else:
                fore, ux, uy = 0.6 * sw, 0.0, 1.0
            side = max(0.9 * fore, 0.45 * sw)
            hands.append(_box(float(k[wri, 0] + ux * 0.35 * fore), float(k[wri, 1] + uy * 0.35 * fore),
                              side, side))
        elif vis[elb] and vis[ombro]:
            # punho escondido: projeta a mao adiante do cotovelo, no rumo do braco.
            # O antebraco tem mais ou menos o comprimento do braco.
            braco = max(_dist(k, ombro, elb), 0.35 * sw)
            ux, uy = (k[elb, 0] - k[ombro, 0]) / braco, (k[elb, 1] - k[ombro, 1]) / braco
            side = max(0.95 * braco, 0.45 * sw)
            hands.append(_box(float(k[elb, 0] + ux * 1.0 * braco), float(k[elb, 1] + uy * 1.0 * braco),
                              side, side))
    regions['hands'] = (hands or [(x1, y1 + bh * 0.35, x2, y1 + bh * 0.7)], bool(hands))

    feet = []
    for ank, kne in ((L_ANK, L_KNE), (R_ANK, R_KNE)):
        if not vis[ank] or k[ank, 1] > frame_h * 0.985:  # pe cortado na borda de baixo
            continue
        shin = _dist(k, ank, kne) if vis[kne] else 0.9 * sw
        side = max(0.7 * shin, 0.5 * sw)
        feet.append(_box(float(k[ank, 0]), float(k[ank, 1] + 0.2 * side), side, side))
    regions['feet'] = (feet or [(x1, y1 + bh * 0.8, x2, y2)], bool(feet))
    return regions


# ── Oclusao ─────────────────────────────────────────────────────────
# Pontos que provam que a regiao esta a vista. Com um objeto na frente, o modelo de pose
# "completa" o corpo com pontos inventados (quadril e pernas atras de uma caixa com confianca
# alta) e ainda cria uma segunda copia da pessoa so com a parte de cima. A copia partida
# denuncia quais pontos foram inventados.
PONTOS_DA_REGIAO = {'head': (NOSE, L_EYE, R_EYE, L_EAR, R_EAR), 'torso': (L_HIP, R_HIP),
                    'hands': (L_WRI, R_WRI), 'feet': (L_KNE, R_KNE, L_ANK, R_ANK)}


def _mesma_pessoa(a, b, thr):
    """b (a menor) e uma copia de a: quase toda dentro dela e com os mesmos ombros (ou cabeca)."""
    if _ioa(b['box'], a['box']) < 0.7:
        return False
    ka, kb = a['kpts'], b['kpts']
    sw = max(_dist(ka, L_SHO, R_SHO) if ka[L_SHO, 2] >= thr and ka[R_SHO, 2] >= thr else 0.0,
             (a['box'][2] - a['box'][0]) * 0.35, 1.0)
    for pontos in ((L_SHO, R_SHO), (NOSE, L_EAR, R_EAR)):
        comuns = [i for i in pontos if ka[i, 2] >= thr and kb[i, 2] >= thr]
        if comuns:
            return all(_dist(np.vstack([ka[i], kb[i]]), 0, 1) <= 0.35 * sw for i in comuns)
    return False


def consolidar_pessoas(people, thr=DEFAULT_CONFIG['kp_conf'], conhecidos=()):
    """Une as copias da mesma pessoa que o modelo de pose cria quando algo fica na frente.

    Sem isso o EPI fica com uma copia e a outra aparece sem EPI (alarme falso). Fica a caixa
    maior (o ponto de apoio segue valendo para as areas), os pontos inventados perdem a
    confianca e as regioes escondidas vao para p['encoberto']. O ID que ja era rastreado ha
    mais tempo (menor) e mantido, para o historico nao se partir."""
    ordem = sorted(range(len(people)), key=lambda i: -_area(people[i]['box']))
    fora = set()
    for ai in ordem:
        if ai in fora:
            continue
        a = people[ai]
        for bi in ordem:
            if bi == ai or bi in fora or _area(people[bi]['box']) > _area(a['box']):
                continue
            b = people[bi]
            if not _mesma_pessoa(a, b, thr):
                continue
            fora.add(bi)
            k = np.array(a['kpts'], dtype=float, copy=True)
            escondidas = set()
            for regiao, pontos in PONTOS_DA_REGIAO.items():
                if any(k[i, 2] >= thr for i in pontos) and not any(b['kpts'][i, 2] >= thr for i in pontos):
                    escondidas.add(regiao)
                    for i in pontos:
                        k[i, 2] = min(k[i, 2], float(b['kpts'][i, 2]))
            a['kpts'] = k
            a['encoberto'] = set(a.get('encoberto') or ()) | escondidas
            ids = [x for x in (a.get('track_id'), b.get('track_id')) if x is not None and x in conhecidos]
            if ids:
                a['track_id'] = min(ids)
    return [p for i, p in enumerate(people) if i not in fora]


def encobertas_por_outra(p, people, frame_h):
    """Regioes de p cobertas por outra pessoa que esta na frente (pes mais perto da camera)."""
    ocultas = set()
    for q in people:
        if q is p or q['box'][3] <= p['box'][3] + 0.02 * frame_h:
            continue
        for regiao, (caixas, _) in p['regions'].items():
            if regiao != 'body' and max((_ioa(c, q['box']) for c in caixas), default=0.0) >= 0.5:
                ocultas.add(regiao)
    return ocultas


def encobertas_fora_da_silhueta(p, frame_w, frame_h):
    """Regioes que a pose estimou fora da pessoa que o modelo de fato viu, dentro da imagem.

    Com a cabeca atras de um objeto, a caixa da pessoa comeca abaixo dele e a cabeca estimada
    fica para cima dela: o corpo continua ali, so nao aparece. Na borda da imagem nao vale
    (isso e corte do quadro, tratado em body_regions)."""
    ocultas = set()
    quadro = (0, 0, frame_w, frame_h)
    for regiao in ('head', 'torso', 'feet'):
        caixas, visivel = p['regions'][regiao]
        if visivel and all(_ioa(c, p['box']) < 0.4 and _ioa(c, quadro) >= 0.6 for c in caixas):
            ocultas.add(regiao)
    return ocultas


def regioes_encobertas(p, people, frame_w, frame_h):
    """Tudo o que esta escondido em p: copia partida pela pose, outra pessoa na frente ou objeto."""
    return (set(p.get('encoberto') or ()) | encobertas_por_outra(p, people, frame_h)
            | encobertas_fora_da_silhueta(p, frame_w, frame_h))


def _postura_das_pernas(k, vis, sy, hy, torso):
    """Com o tronco ereto, as pernas dizem se a pessoa esta em pe, sentada ou agachada.

    Tres medidas, todas relativas ao proprio corpo (funcionam perto ou longe da camera):
      queda_da_coxa = o quanto o joelho fica abaixo do quadril, sobre o comprimento da coxa.
                      ~1 em pe (coxa vertical), ~0 sentado (coxa horizontal).
      compressao    = altura ombro->tornozelo sobre a soma dos segmentos. ~1 em pe,
                      ~0,7 sentado (a coxa deixa de somar altura), ~0,5 agachado.
    Sem joelho visivel nao da para separar sentado de em pe: devolve 'desconhecida'
    em vez de afirmar 'em pe', que era a origem do erro de quem trabalha sentado.
    """
    pernas = ((L_HIP, L_KNE, L_ANK), (R_HIP, R_KNE, R_ANK))
    quedas, compressoes, joelhos = [], [], []
    for quadril, joelho, torn in pernas:
        if not (vis[quadril] and vis[joelho]):
            continue
        coxa = math.hypot(k[joelho, 0] - k[quadril, 0], k[joelho, 1] - k[quadril, 1])
        if coxa < 1e-6:
            continue
        quedas.append((k[joelho, 1] - k[quadril, 1]) / coxa)
        if vis[torn]:
            canela = math.hypot(k[torn, 0] - k[joelho, 0], k[torn, 1] - k[joelho, 1])
            total = torso + coxa + canela
            if total > 1e-6:
                compressoes.append((float(k[torn, 1]) - sy) / total)
            joelhos.append(_joint_angle(k, quadril, joelho, torn))

    if not quedas:
        # joelhos escondidos (mesa, bancada, balcao): nao da para afirmar a postura
        return 'desconhecida'

    queda = max(quedas)          # a perna mais "esticada" manda
    if queda >= 0.62:
        return 'em_pe'
    if compressoes and max(compressoes) < 0.62:
        return 'agachado'        # corpo bem compactado: quadril perto do chao
    if joelhos and min(joelhos) < 55:
        return 'agachado'        # joelho muito fechado sem estar sentado
    return 'sentado'


def posture_of(box, k, thr=DEFAULT_CONFIG['kp_conf']):
    """(postura, bracos_levantados, direcao_da_cabeca, inclinacao_do_tronco em graus)."""
    vis = k[:, 2] >= thr
    sho = [i for i in (L_SHO, R_SHO) if vis[i]]
    hips = [i for i in (L_HIP, R_HIP) if vis[i]]
    sw = max(_dist(k, L_SHO, R_SHO) if len(sho) == 2 else 0.0, (box[2] - box[0]) * 0.35)

    posture, angle = 'desconhecida', None
    if sho and hips:
        sx, sy = float(k[sho, 0].mean()), float(k[sho, 1].mean())
        hx, hy = float(k[hips, 0].mean()), float(k[hips, 1].mean())
        torso = math.hypot(sx - hx, sy - hy)
        angle = math.degrees(math.atan2(abs(sx - hx), hy - sy))  # 0 = ereto, 90 = deitado
        if angle >= 60:
            posture = 'caido'
        elif angle >= 35:
            posture = 'curvado'
        else:
            posture = _postura_das_pernas(k, vis, sy, hy, torso)

    wrists = [float(k[i, 1]) for i in (L_WRI, R_WRI) if vis[i]]
    ref = float(k[NOSE, 1]) if vis[NOSE] else (float(k[sho, 1].mean()) - 0.6 * sw if sho else None)
    arms_up = bool(wrists and ref is not None and min(wrists) < ref)

    head = 'desconhecida'
    if vis[NOSE] and vis[L_EYE] and vis[R_EYE]:
        head = 'frente'
        if len(sho) == 2:
            off = (k[NOSE, 0] - (k[L_SHO, 0] + k[R_SHO, 0]) / 2) / max(abs(k[L_SHO, 0] - k[R_SHO, 0]), 1.0)
            head = 'virada_direita' if off > 0.25 else 'virada_esquerda' if off < -0.25 else 'frente'
    elif vis[NOSE] and any(vis[i] for i in (L_EYE, R_EYE, L_EAR, R_EAR)):
        ref_x = np.mean([k[i, 0] for i in (L_EAR, R_EAR, L_EYE, R_EYE) if vis[i]])
        head = 'perfil_direita' if k[NOSE, 0] > ref_x else 'perfil_esquerda'
    elif sho and not any(vis[i] for i in (NOSE, L_EYE, R_EYE)):
        head = 'de_costas'
    return posture, arms_up, head, angle


def movement_label(speed):
    """speed em alturas de corpo por segundo."""
    return 'parado' if speed < 0.15 else 'andando' if speed < 1.2 else 'correndo'


# ── Ligacao EPI -> pessoa ───────────────────────────────────────────
def assign_detections(people, detections):
    """Marca d['person'] com o indice da pessoa dona de cada deteccao (ou None)."""
    for d in detections:
        d['person'] = None
        if d['item'] == 'person':
            continue
        region = tax.item_region(d['item'])
        bx = d['box']
        cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
        best, best_score = None, 0.0
        for pi, p in enumerate(people):
            x1, y1, x2, y2 = p['box']
            mx, my = (x2 - x1) * 0.15, (y2 - y1) * 0.15
            if not (x1 - mx <= cx <= x2 + mx and y1 - my <= cy <= y2 + my):
                continue
            boxes, _ = p['regions'].get(region, p['regions']['body'])
            score = max((_ioa(bx, rb) for rb in boxes), default=0.0)
            if score > best_score:
                best, best_score = pi, score
        if best_score >= (0.25 if region in ('hands', 'feet') else 0.35):
            d['person'] = best


def assign_employees(people, employees):
    """Liga rostos reconhecidos (modelo funcionarios.pt) a pessoa dona daquele rosto.

    Antes exigia que o centro do rosto caisse dentro da caixa da cabeca e ficava com a
    primeira pessoa que servisse. A caixa da cabeca e estimada a partir dos ombros, entao
    de perfil, com a cabeca inclinada ou com o capacete alto ela erra por alguns pixels e
    o nome sumia. Agora usa sobreposicao (nao o centro), aceita o corpo como segunda
    opcao e fica com a melhor pessoa, nao com a primeira."""
    usados = set()
    for e in sorted(employees or [], key=lambda x: -float(x.get('confidence') or 0)):
        rosto = e['bbox']
        melhor, melhor_score = None, 0.0
        for pi, p in enumerate(people):
            if pi in usados:          # um rosto por pessoa
                continue
            cabeca = p['regions']['head'][0][0]
            score = _ioa(rosto, cabeca)
            if score < 0.35:          # rosto fora da cabeca: aceita dentro do corpo, com peso menor
                score = _ioa(rosto, p['box']) * 0.5
            if score > melhor_score:
                melhor, melhor_score = pi, score
        if melhor is not None and melhor_score >= 0.3:
            people[melhor]['nome'] = e['nome']
            if e.get('func_id'):        # veio do reconhecimento por embedding
                people[melhor]['func_id'] = e['func_id']
            usados.add(melhor)


def supported_items(resolved_classes):
    """{item: {'pos': bool, 'neg': bool}} a partir de ppe_taxonomy.resolve_model_classes()."""
    out = {}
    for item, present, _known in resolved_classes.values():
        if item != 'person':
            out.setdefault(item, {'pos': False, 'neg': False})['pos' if present else 'neg'] = True
    return out


def frame_evidence(person, detections, required, supported, encoberto=()):
    """{item: (fonte, confianca)} para os EPIs exigidos, usando as deteccoes desta pessoa.

    encoberto: regioes escondidas atras de um objeto ou de outra pessoa. Nelas, nao achar o EPI
    nao prova que ele falta: vira 'nao_visivel' em vez de 'inferido'."""
    height = person['box'][3] - person['box'][1]
    evidence = {}
    for item in required:
        pos = [d['conf'] for d in detections if d['item'] == item and d['present']]
        neg = [d['conf'] for d in detections if d['item'] == item and not d['present']]
        region = tax.item_region(item)
        _, visible = person['regions'].get(region, person['regions']['body'])
        if pos and (not neg or max(pos) >= max(neg)):
            evidence[item] = ('detectado', max(pos))
        elif neg:
            evidence[item] = ('ausencia_detectada', max(neg))
        elif region in encoberto:
            evidence[item] = ('nao_visivel', 0.0)
        elif visible and supported.get(item, {}).get('pos') and height >= MIN_PERSON_PX.get(region, 90):
            evidence[item] = ('inferido', 0.0)
        else:
            evidence[item] = ('nao_visivel', 0.0)
    return evidence


# ── Estado temporal ─────────────────────────────────────────────────
def decide_state(previous, weights, cfg):
    """Novo estado a partir dos pesos (nao nulos) da janela, com histerese."""
    if len(weights) >= cfg['min_obs']:
        score = sum(weights) / len(weights)
        if score >= cfg['ok_score']:
            return 'ok'
        if score <= cfg['missing_score']:
            return 'faltando'
        return 'verificando' if previous == 'nao_visivel' else previous
    if weights and previous == 'nao_visivel':
        return 'verificando'
    return previous


class _ItemState:
    __slots__ = ('obs', 'state', 'missing_since', 'last_obs', 'last_neg', 'conf', 'source')

    def __init__(self):
        self.obs = deque()
        self.state = 'nao_visivel'
        self.missing_since = None
        self.last_obs = None
        self.last_neg = None   # ultima vez que a ausencia foi vista de fato (classe sem_*)
        self.conf = 0.0
        self.source = 'nao_visivel'

    def update(self, t, source, conf, cfg, encoberto=False):
        weight = WEIGHTS[source]
        if weight:
            self.obs.append((t, weight))
            self.last_obs, self.conf, self.source = t, conf, source
            if source == 'ausencia_detectada':
                self.last_neg = t
        while self.obs and t - self.obs[0][0] > cfg['window_sec']:
            self.obs.popleft()
        previous = self.state
        # atras de um objeto o EPI nao some: vale o ultimo estado visto por mais tempo
        memoria = cfg['memoria_encoberto_sec'] if encoberto else 3 * cfg['window_sec']
        if encoberto and any(w == WEIGHTS['inferido'] for _, w in self.obs):
            # "nao achei" deduzido antes de perceber o que estava na frente nao prova a falta;
            # ausencia vista de fato (sem_capacete, sem_colete...) continua valendo
            self.obs = deque(o for o in self.obs if o[1] != WEIGHTS['inferido'])
            sem_negativo = not any(w < 0 for _, w in self.obs)
            if previous == 'faltando' and sem_negativo and (self.last_neg is None or t - self.last_neg > memoria):
                previous = self.state = 'verificando'
        if self.obs:
            self.state = decide_state(previous, [w for _, w in self.obs], cfg)
        elif self.last_obs is None or t - self.last_obs > memoria:
            self.state = 'nao_visivel'
        if self.state != 'faltando':
            self.missing_since = None
        elif previous != 'faltando':
            self.missing_since = next((ts for ts, w in self.obs if w < 0), t)

    def snapshot(self, t, cfg):
        dur = t - self.missing_since if self.missing_since is not None else 0.0
        return {'estado': self.state, 'fonte': self.source, 'confianca': round(self.conf, 3),
                'faltando_ha': round(dur, 1), 'alerta': self.state == 'faltando' and dur >= cfg['alert_sec']}


class _Track:
    def __init__(self, tid):
        self.id = tid
        self.items = {}
        self.last_seen = 0.0
        self.hist = deque(maxlen=90)  # (t, x do pe, y do pe, altura)
        self.speed = 0.0
        self.posture = 'desconhecida'
        self.posture_since = None
        self.name = None
        self.func_id = None   # identidade gruda no track, igual ao nome

    def item(self, key):
        st = self.items.get(key)
        if st is None:
            st = self.items[key] = _ItemState()
        return st

    def update_motion(self, t, box, posture):
        x1, _, x2, y2 = box
        height = max(box[3] - box[1], 1.0)
        self.hist.append((t, (x1 + x2) / 2, y2, height))
        ref = next((e for e in self.hist if t - e[0] <= 1.0), None)
        if ref is not None and t - ref[0] >= 0.2:
            v = math.hypot(self.hist[-1][1] - ref[1], self.hist[-1][2] - ref[2]) / ((ref[3] + height) / 2) / (t - ref[0])
            self.speed = 0.6 * self.speed + 0.4 * v
        if posture != self.posture:
            self.posture, self.posture_since = posture, t


def centered_states(times, weights, cfg):
    """Estados de um EPI ao longo de um video inteiro, com janela centrada (passado + futuro).

    Usado no modo super detalhado: sem a restricao de tempo real, o estado de cada instante
    tambem considera o que vem depois, eliminando atraso e piscadas."""
    nz_t = [t for t, w in zip(times, weights) if w]
    nz_w = [w for w in weights if w]
    prefix = [0.0]
    for w in nz_w:
        prefix.append(prefix[-1] + w)
    half = cfg['window_sec'] / 2
    states, state, last_nz = [], 'nao_visivel', None
    for t in times:
        lo, hi = bisect_left(nz_t, t - half), bisect_right(nz_t, t + half)
        window = nz_w[lo:hi]
        if window:
            state = decide_state(state, window, cfg)
            last_nz = t
        elif last_nz is None or t - last_nz > 3 * cfg['window_sec']:
            state = 'nao_visivel'
        states.append(state)
    return states


# ── Analisador ──────────────────────────────────────────────────────
class PPEAnalyzer:
    """Pose + rastreio + estado de EPI por pessoa. Uma instancia por stream ou video:
    o rastreador guarda estado entre frames."""

    def __init__(self, pose_model_path, device='cpu', half=False, imgsz=640, config=None,
                 tracker='bytetrack.yaml'):
        from ultralytics import YOLO
        self.pose = YOLO(pose_model_path)
        self.device, self.half, self.imgsz, self.tracker = device, half, imgsz, tracker
        self.cfg = {**DEFAULT_CONFIG, **(config or {})}
        self.tracks = {}
        self._anon = 0

    def reset(self):
        self.tracks.clear()
        self.pose.predictor = None  # recria o rastreador na proxima chamada

    def detect_people(self, frame):
        res = self.pose.track(frame, persist=True, tracker=self.tracker, imgsz=self.imgsz, conf=0.3,
                              device=self.device, verbose=False, **precision_kwargs(self.half))[0]
        if res.boxes is None or res.keypoints is None or len(res.boxes) == 0:
            return []
        boxes = res.boxes.xyxy.cpu().numpy()
        confs = res.boxes.conf.cpu().numpy()
        ids = res.boxes.id.int().cpu().tolist() if res.boxes.id is not None else [None] * len(boxes)
        kpts = res.keypoints.data.cpu().numpy()
        people = [{'box': tuple(float(v) for v in b), 'conf': float(c), 'track_id': tid, 'kpts': k}
                  for b, c, tid, k in zip(boxes, confs, ids, kpts)]
        return consolidar_pessoas(people, self.cfg['kp_conf'], self.tracks)

    def analyze(self, frame, detections, required, supported, t=None, employees=None, people=None,
                resolvedor=None):
        """Analisa um frame.

        detections: [{'item', 'present', 'known', 'conf', 'box', 'label'}] em pixels do frame.
        resolvedor: areas.ResolvedorDeArea. Quando presente, cada pessoa e cobrada so pelos
        EPIs da zona em que esta pisando, em vez da lista unica do stream.
        Retorna o resultado serializavel e, em cada pessoa, 'evidencias' cruas (para revisao offline)."""
        t = time.time() if t is None else t
        h, w = frame.shape[:2]
        if people is None:
            people = self.detect_people(frame)
        for p in people:
            p['regions'] = body_regions(p['box'], p['kpts'], w, h, self.cfg['kp_conf'])
        encobertos = [regioes_encobertas(p, people, w, h) for p in people]
        for p, enc in zip(people, encobertos):
            p['encoberto'] = enc
        assign_detections(people, detections)
        assign_employees(people, employees)

        persons = []
        for pi, p in enumerate(people):
            tid = p['track_id']
            if tid is None:  # sem ID o estado nao persiste entre frames
                self._anon -= 1
                tid = self._anon
            track = self.tracks.get(tid) or _Track(tid)
            self.tracks[tid] = track
            track.last_seen = t
            # o rosto so aparece em alguns quadros; a identidade fica no track para
            # os quadros seguintes, senao a violacao e gravada sem dono
            if p.get('nome'):
                track.name = p['nome']
            if p.get('func_id'):
                track.func_id = p['func_id']

            # EPIs cobrados desta pessoa: os da zona onde ela pisa, ou o padrao do stream
            req_p, area_p = (resolvedor.para_box(p['box']) if resolvedor is not None
                             else (required, None))
            encoberto = p['encoberto']
            evidence = frame_evidence(p, [d for d in detections if d.get('person') == pi], req_p, supported,
                                      encoberto)
            epis = {}
            for item, (source, conf) in evidence.items():
                st = track.item(item)
                st.update(t, source, conf, self.cfg, encoberto=tax.item_region(item) in encoberto)
                epis[item] = st.snapshot(t, self.cfg)

            posture, arms_up, head, angle = posture_of(p['box'], p['kpts'], self.cfg['kp_conf'])
            track.update_motion(t, p['box'], posture)
            missing = [i for i, s in epis.items() if s['estado'] == 'faltando']
            _onde = f" em {area_p['nome']}" if area_p else ''
            alerts = [f"Sem {tax.item_label(i).lower()}{_onde} há {epis[i]['faltando_ha']:.0f}s"
                      for i in missing if epis[i]['alerta']]
            if track.posture == 'caido' and t - (track.posture_since or t) >= self.cfg['fall_sec']:
                alerts.append('Possível queda')
            persons.append({
                'track_id': tid if tid >= 0 else None,
                'nome': track.name,
                'bbox': [round(v, 1) for v in p['box']],
                'conf': round(p['conf'], 3),
                'keypoints': np.round(p['kpts'], 1).tolist(),
                'epis': epis,
                'faltando': missing,
                'postura': track.posture,
                'bracos_levantados': arms_up,
                'cabeca': head,
                'inclinacao_tronco': round(angle, 1) if angle is not None else None,
                'movimento': movement_label(track.speed),
                'velocidade': round(track.speed, 2),
                # regioes escondidas neste quadro que importam para os EPIs cobrados desta pessoa
                'encoberto': sorted(encoberto & {tax.item_region(i) for i in req_p}),
                'reforco': list(p.get('reforco') or ()),  # segunda olhada usada (recorte, detr)
                'alertas': alerts,
                'func_id': p.get('func_id') or track.func_id,
                'exigidos': list(req_p),
                'area_id': area_p['id'] if area_p else None,
                'area': area_p['nome'] if area_p else None,
                'area_cor': area_p['cor'] if area_p else None,
                'evidencias': {i: src for i, (src, _) in evidence.items()},
            })

        for tid in [k for k, tr in self.tracks.items() if t - tr.last_seen > self.cfg['forget_sec']]:
            del self.tracks[tid]
        return summarize(persons)


def summarize(persons):
    missing_items = sorted({i for p in persons for i in p['faltando']})
    if not persons:
        status = 'sem_pessoa'
    elif missing_items or any('Possível queda' in p['alertas'] for p in persons):
        status = 'perigo'
    elif any(s['estado'] == 'verificando' for p in persons for s in p['epis'].values()):
        status = 'verificando'
    else:
        status = 'seguro'
    alerts = [f"{'ID ' + str(p['track_id']) if p['track_id'] is not None else 'Pessoa'}: {a}"
              for p in persons for a in p['alertas']]
    return {'persons': persons, 'status': status, 'missing_items': missing_items,
            'missing': [tax.item_label(i) for i in missing_items], 'alerts': alerts}


def activity_text(analysis):
    persons = analysis.get('persons') or []
    if not persons:
        return 'sem pessoa detectada'
    named = [p for p in persons if p.get('nome')]
    quem = f"{len(persons)} pessoa(s)" + (f", {len(named)} identificada(s)" if named else '')
    if analysis.get('status') == 'perigo':
        return quem + ' em situação de risco'
    return quem + ' trabalhando'


# ── Desenho ─────────────────────────────────────────────────────────
def _ascii(text):
    return unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode()


def _label(img, text, x, y_bottom, color, scale, thick):
    """Etiqueta com fundo colorido terminando em y_bottom. Retorna o topo."""
    text = _ascii(text)
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    top = max(0, int(y_bottom) - th - base - 4)
    x = int(max(0, min(x, img.shape[1] - tw - 6)))
    cv2.rectangle(img, (x, top), (x + tw + 6, top + th + base + 4), color, -1)
    cv2.putText(img, text, (x + 3, top + th + 2), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), thick, cv2.LINE_AA)
    return top


def _region_states(epis):
    out = {}
    for item, s in epis.items():
        region = tax.item_region(item)
        if _STATE_RANK[s['estado']] >= _STATE_RANK.get(out.get(region), -1):
            out[region] = s['estado']
    return out


def _hex_bgr(cor, padrao=(60, 60, 200)):
    s = str(cor or '').lstrip('#')
    if len(s) != 6:
        return padrao
    try:
        return (int(s[4:6], 16), int(s[2:4], 16), int(s[0:2], 16))  # BGR
    except ValueError:
        return padrao


def draw_zones(img, zonas, areas_por_id):
    """Desenha os poligonos das areas mapeadas sobre a imagem (no proprio img)."""
    if not zonas:
        return
    h, w = img.shape[:2]
    scale = max(0.4, min(h, w) / 900)
    capa = img.copy()
    for z in zonas:
        area = (areas_por_id or {}).get(z.get('area_id'))
        if not area:
            continue
        cor = _hex_bgr(area.get('cor'))
        pts = np.array([[int(px * w), int(py * h)] for px, py in z['pontos']], dtype=np.int32)
        cv2.fillPoly(capa, [pts], cor)
        cv2.polylines(img, [pts], True, cor, max(1, int(round(scale * 2))), cv2.LINE_AA)
        topo = pts[np.argmin(pts[:, 1])]
        _label(img, _ascii(area.get('nome') or 'area'), int(topo[0]), int(topo[1]), cor, scale * 0.5,
               max(1, int(round(scale * 1.5))))
    cv2.addWeighted(capa, 0.18, img, 0.82, 0, img)  # preenchimento translucido


def draw_analysis(img, analysis, detections=(), show_ppe=True, zonas=None, areas_por_id=None):
    """Desenha caixas de EPI, esqueleto colorido por regiao e etiquetas por pessoa (no proprio img)."""
    h, w = img.shape[:2]
    scale = max(0.4, min(h, w) / 900)
    thick = max(1, int(round(scale * 2)))
    draw_zones(img, zonas, areas_por_id)  # areas ficam no fundo, sob as pessoas
    if show_ppe:
        for d in detections:
            if d['item'] == 'person':
                continue
            x1, y1, x2, y2 = map(int, d['box'])
            color = (72, 187, 88) if d['present'] else (48, 48, 220)
            if not d.get('known', True):
                color = (200, 140, 60)
            cv2.rectangle(img, (x1, y1), (x2, y2), color, max(1, thick - 1))
            _label(img, f"{d['label']} {d['conf']:.2f}", x1, y1, color, scale * 0.45, max(1, thick - 1))
    for p in analysis.get('persons', []):
        regions = _region_states(p['epis'])
        if p['faltando'] or p['alertas']:
            color = STATE_COLORS['faltando']
        elif 'verificando' in regions.values():
            color = STATE_COLORS['verificando']
        else:
            color = STATE_COLORS['ok']
        x1, y1, x2, y2 = map(int, p['bbox'])
        cv2.rectangle(img, (x1, y1), (x2, y2), color, thick)
        k = np.asarray(p['keypoints'], dtype=float)
        if k.shape == (17, 3):
            for a, b, region in SKELETON:
                if k[a, 2] >= 0.35 and k[b, 2] >= 0.35:
                    c = STATE_COLORS.get(regions.get(region), (235, 235, 235))
                    cv2.line(img, (int(k[a, 0]), int(k[a, 1])), (int(k[b, 0]), int(k[b, 1])), c, thick + 1, cv2.LINE_AA)
            for x, y, c in k:
                if c >= 0.35:
                    cv2.circle(img, (int(x), int(y)), thick + 1, (255, 255, 255), -1, cv2.LINE_AA)
        title = f"ID {p['track_id']}" if p['track_id'] is not None else 'Pessoa'
        if p.get('nome'):
            title += f" {p['nome']}"
        estado = f"{POSTURE_LABELS.get(p['postura'], p['postura'])}, {p['movimento']}"
        if p.get('encoberto'):
            estado += ', encoberto'
        lines = [(f"{title} | {estado}", color)]
        if p['faltando']:
            lines.append(('Falta: ' + ', '.join(tax.item_label(i) for i in p['faltando']), STATE_COLORS['faltando']))
        if 'Possível queda' in p['alertas']:
            lines.append(('POSSIVEL QUEDA', (0, 0, 200)))
        y = y1
        for text, c in reversed(lines):
            y = _label(img, text, x1, y, c, scale * 0.5, max(1, thick - 1))
    return img
