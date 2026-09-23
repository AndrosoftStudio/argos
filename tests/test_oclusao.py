"""Oclusao: copia partida pela pose, parte do corpo fora da silhueta e memoria do estado.
Pontos do corpo simulados; nao precisa de modelo nem de video."""
import os
import sys

import numpy as np
import pytest

pytest.importorskip('cv2')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))

import ppe_analyzer as pa  # noqa: E402

CFG = pa.DEFAULT_CONFIG


def pessoa(box, visiveis, tid=None, conf=0.95):
    """Pessoa em pe dentro de box, com os pontos em 'visiveis' confiaveis e os demais em 0."""
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    pos = {pa.NOSE: (.5, .08), pa.L_EYE: (.46, .06), pa.R_EYE: (.54, .06), pa.L_EAR: (.42, .07),
           pa.R_EAR: (.58, .07), pa.L_SHO: (.3, .2), pa.R_SHO: (.7, .2), pa.L_ELB: (.25, .35),
           pa.R_ELB: (.75, .35), pa.L_WRI: (.22, .48), pa.R_WRI: (.78, .48), pa.L_HIP: (.38, .52),
           pa.R_HIP: (.62, .52), pa.L_KNE: (.38, .75), pa.R_KNE: (.62, .75), pa.L_ANK: (.38, .97),
           pa.R_ANK: (.62, .97)}
    k = np.zeros((17, 3))
    for i, (fx, fy) in pos.items():
        k[i] = (x1 + fx * w, y1 + fy * h, conf if i in visiveis else 0.0)
    return {'box': tuple(box), 'conf': 0.9, 'track_id': tid, 'kpts': k}


TUDO = set(range(17))
CIMA = set(range(11))  # cabeca, ombros, bracos: quadril e pernas atras do objeto


def test_copia_partida_vira_uma_pessoa_com_tronco_e_pernas_escondidos():
    inteira = pessoa((100, 100, 200, 400), TUDO, tid=7)        # pernas "inventadas" atras da caixa
    metade = pessoa((100, 100, 200, 400), CIMA, tid=3)
    metade['box'] = (102, 100, 198, 250)                        # so a parte de cima
    metade['kpts'][:, :2] = inteira['kpts'][:, :2]
    juntas = pa.consolidar_pessoas([inteira, metade], conhecidos={3: object(), 7: object()})
    assert len(juntas) == 1
    p = juntas[0]
    assert p['box'] == (100, 100, 200, 400)                     # fica a caixa maior (ponto de apoio)
    assert p['track_id'] == 3                                   # o ID mais antigo segue
    assert {'torso', 'feet'} <= p['encoberto']
    assert p['kpts'][pa.L_HIP, 2] == 0 and p['kpts'][pa.L_ANK, 2] == 0


def test_duas_pessoas_lado_a_lado_nao_se_juntam():
    a = pessoa((100, 100, 200, 400), TUDO, tid=1)
    b = pessoa((150, 110, 250, 410), TUDO, tid=2)
    assert len(pa.consolidar_pessoas([a, b])) == 2


def test_cabeca_fora_da_silhueta_conta_como_encoberta():
    p = pessoa((300, 300, 400, 600), TUDO)
    p['regions'] = pa.body_regions(p['box'], p['kpts'], 1280, 720)
    assert pa.encobertas_fora_da_silhueta(p, 1280, 720) == set()
    p['box'] = (300, 470, 400, 600)                             # objeto na frente da cabeca e ombros
    p['regions'] = pa.body_regions(p['box'], p['kpts'], 1280, 720)
    assert 'head' in pa.encobertas_fora_da_silhueta(p, 1280, 720)


def test_regiao_encoberta_nao_deduz_falta():
    p = pessoa((300, 100, 420, 600), TUDO)
    p['regions'] = pa.body_regions(p['box'], p['kpts'], 1280, 720)
    sup = {'capacete': {'pos': True, 'neg': True}}
    assert pa.frame_evidence(p, [], ['capacete'], sup)['capacete'][0] == 'inferido'
    assert pa.frame_evidence(p, [], ['capacete'], sup, {'head'})['capacete'][0] == 'nao_visivel'
    neg = [{'item': 'capacete', 'present': False, 'conf': 0.8}]
    assert pa.frame_evidence(p, neg, ['capacete'], sup, {'head'})['capacete'][0] == 'ausencia_detectada'


def test_palpite_antes_de_perceber_o_objeto_e_descartado():
    st = pa._ItemState()
    for t in (0.0, 0.1, 0.2, 0.3):
        st.update(t, 'inferido', 0.0, CFG)
    assert st.state == 'faltando'
    st.update(0.4, 'nao_visivel', 0.0, CFG, encoberto=True)
    assert st.state == 'verificando'                            # nao ha prova da falta


def test_falta_vista_de_fato_continua_atras_do_objeto():
    st = pa._ItemState()
    for t in (0.0, 0.1, 0.2, 0.3):
        st.update(t, 'ausencia_detectada', 0.8, CFG)
    assert st.state == 'faltando'
    for t in (2.0, 5.0, 9.0):
        st.update(t, 'nao_visivel', 0.0, CFG, encoberto=True)
    assert st.state == 'faltando'                               # quem estava sem capacete continua sem


def test_epi_visto_e_lembrado_enquanto_encoberto():
    st = pa._ItemState()
    for t in (0.0, 0.1, 0.2, 0.3):
        st.update(t, 'detectado', 0.8, CFG)
    st.update(8.0, 'nao_visivel', 0.0, CFG, encoberto=True)
    assert st.state == 'ok'                                     # 8 s atras da caixa: segue ok
    st.update(8.0, 'nao_visivel', 0.0, CFG, encoberto=False)
    assert st.state == 'nao_visivel'                            # sem objeto na frente, a memoria e curta
