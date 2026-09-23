"""
reforco.py - Segunda olhada adaptativa: o sistema decide sozinho quando usar ROI e DETR.

O caminho normal e barato: um detector no quadro inteiro. Ele basta quando cada pessoa ja tem
os EPIs exigidos confirmados (achados ou com ausencia detectada). Quando nao basta:
  - a pessoa esta encoberta (objeto na frente, outra pessoa, copia partida pela pose), ou
  - algum EPI exigido nao foi achado nem negado (pessoa pequena, EPI cortado, contraluz),
o detector roda de novo no recorte ampliado dessa pessoa (ROI): o EPI ganha pixels e sai do
meio do que o cobria. Para as encobertas, o outro detector (DETR, se o principal for YOLO, ou
vice-versa), quando houver um treinado em models/, da uma segunda opiniao no mesmo recorte;
a atencao global do transformer lida melhor com o corpo pela metade.

Custo: so as pessoas que precisam ganham recorte (ate MAX_PESSOAS por quadro) e o segundo
detector respeita um intervalo minimo, que e maior sem GPU.
"""
import ppe_taxonomy as tax
from epi_detector import is_cuda, merge_detections
from ppe_analyzer import assign_detections, body_regions, frame_evidence, regioes_encobertas

MAX_PESSOAS = 4


class Reforco:
    def __init__(self, detector, extra=None, kp_conf=0.35):
        self.detector = detector
        self.extra = extra if extra is not None and extra.knows_ppe else None
        self.kp_conf = kp_conf
        gpu = is_cuda(detector.device)
        # quem esta a vista mas sem EPI confirmado e revisto de tempos em tempos (a votacao
        # temporal junta as evidencias); quem esta encoberto, em todo quadro-chave
        self.intervalo_recorte = 0.3 if gpu else 1.0
        # o segundo detector custa caro sem GPU (RT-DETR ~0,5 s por recorte num notebook)
        self.intervalo_extra = 0.25 if gpu else 2.0
        self._ultimo_extra = None
        self._ultimo_recorte = {}  # track_id -> t
        # mesmo EPI nos dois modelos: a classe do extra e trocada pela do principal antes de juntar
        self._cls_principal = {(item, pres): c for c, (item, pres, _) in detector.resolved.items()}

    def precisam(self, img, people, dets, required, t=None):
        """[(indice, encoberta)] das pessoas que precisam de uma segunda olhada, encobertas primeiro."""
        h, w = img.shape[:2]
        for p in people:
            p['regions'] = body_regions(p['box'], p['kpts'], w, h, self.kp_conf)
        assign_detections(people, dets)
        regioes = {tax.item_region(i) for i in required}
        saida = []
        for pi, p in enumerate(people):
            enc = regioes_encobertas(p, people, w, h) & regioes
            if not enc and t is not None:
                visto = self._ultimo_recorte.get(p.get('track_id'))
                if visto is not None and t - visto < self.intervalo_recorte:
                    continue
            ev = frame_evidence(p, [d for d in dets if d.get('person') == pi], required,
                                self.detector.supported, enc)
            if enc or any(fonte in ('inferido', 'nao_visivel') for fonte, _ in ev.values()):
                saida.append((pi, bool(enc)))
        saida.sort(key=lambda x: (not x[1], -_altura(people[x[0]])))
        return saida[:MAX_PESSOAS]

    def aplicar(self, img, people, dets, required, t, ja_recortou=False, conf=0.3):
        """Deteccoes somadas com as da segunda olhada. Cada pessoa reforcada leva p['reforco']."""
        if not people or not required:
            return dets
        alvo = self.precisam(img, people, dets, required, t)
        if not alvo:
            return dets
        caixas = [people[pi]['box'] for pi, _ in alvo]
        extras = []
        if not ja_recortou:  # no modo super detalhado todas as pessoas ja foram recortadas
            extras.append(self.detector.detect_crops(img, caixas, imgsz=640, conf=conf))
            for pi, _ in alvo:
                people[pi].setdefault('reforco', []).append('recorte')
                if people[pi].get('track_id') is not None:
                    self._ultimo_recorte[people[pi]['track_id']] = t
            if len(self._ultimo_recorte) > 200:  # IDs antigos saem
                self._ultimo_recorte = {k: v for k, v in self._ultimo_recorte.items() if t - v < 30}
        encobertas = [pi for pi, enc in alvo if enc]
        if self.extra is not None and encobertas and (
                self._ultimo_extra is None or t - self._ultimo_extra >= self.intervalo_extra):
            self._ultimo_extra = t
            achados = self.extra.detect_crops(img, [people[pi]['box'] for pi in encobertas], imgsz=640, conf=conf)
            for d in achados:
                d['cls'] = self._cls_principal.get((d['item'], d['present']), d['cls'])
            extras.append(achados)
            for pi in encobertas:
                people[pi].setdefault('reforco', []).append(self.extra.arquitetura)
        return merge_detections(dets, *extras) if extras else dets


def _altura(p):
    return p['box'][3] - p['box'][1]
