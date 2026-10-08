"""Validacao da falta de EPI: quem foi visto com o EPI ha pouco nao o perde por alguns quadros ruins,
servidor lento ainda chega a uma decisao, e o historico marca o "provavel engano".
Nao precisa de banco, de GPU nem de modelo."""
import os
import sys

import pytest

pytest.importorskip('cv2')
pytest.importorskip('ultralytics')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))

import areas  # noqa: E402
import auditoria  # noqa: E402
from live_pipeline import MODOS, _Revisor, normalizar_config  # noqa: E402
from ppe_analyzer import DEFAULT_CONFIG as CFG, PPEAnalyzer, _ItemState, janela_do_ritmo  # noqa: E402

FPS = 10


def rodar(seq):
    """seq: [(duracao_s, fonte ou funcao(i) -> fonte)]. Devolve [(t, snapshot)] e o estado final."""
    st, t, saida, i = _ItemState(), 0.0, [], 0
    for dur, fonte in seq:
        for _ in range(int(dur * FPS)):
            st.update(t, fonte(i) if callable(fonte) else fonte, 0.8, CFG)
            saida.append((t, st.snapshot(t, CFG)))
            t += 1.0 / FPS
            i += 1
    return saida, st


def primeira_falta(saida):
    return next((t for t, s in saida if s['estado'] == 'faltando'), None)


def test_rajada_de_quadros_ruins_nao_vira_falta():
    s, st = rodar([(20, 'detectado'), (3, 'inferido'), (5, 'detectado')])
    assert primeira_falta(s) is None
    assert any(x['validando'] for _, x in s)          # aparece como "verificando" enquanto confere
    assert st.enganos == 1 and s[-1][1]['estado'] == 'ok'


def test_quem_tirou_o_epi_de_verdade_e_confirmado_e_ja_com_alerta():
    s, st = rodar([(20, 'detectado'), (12, 'inferido')])
    t = primeira_falta(s)
    assert t is not None and 23.5 <= t <= 26.5
    primeiro = next(x for _, x in s if x['estado'] == 'faltando')
    assert primeiro['alerta'] and primeiro['faltando_ha'] >= 3.5 and st.enganos == 0


def test_pessoa_nova_sem_historico_decide_como_antes():
    s, _ = rodar([(6, 'inferido')])
    assert primeira_falta(s) <= 1.0


def test_quatro_de_dez_quadros_ruins_nao_viram_falta():
    s, _ = rodar([(10, 'detectado'), (20, lambda i: 'inferido' if i % 10 in (1, 4, 6, 9) else 'detectado')])
    assert primeira_falta(s) is None


def test_ausencia_vista_de_fato_confirma_na_metade_do_prazo():
    s, _ = rodar([(20, 'detectado'), (8, 'ausencia_detectada')])
    assert 21.5 <= primeira_falta(s) <= 23.6


def test_revisor_do_painel_segue_as_mesmas_regras():
    rv, estados, t = _Revisor(MODOS['tempo_real']), [], 0.0
    for dur, fonte in [(20, 'detectado'), (3, 'inferido'), (5, 'detectado'), (12, 'inferido')]:
        for _ in range(int(dur * FPS)):
            rv.adicionar(t, {'persons': [{'track_id': 1, 'evidencias': {'capacete': fonte}}]})
            p = [{'track_id': 1, 'epis': {'capacete': {}}, 'alertas': []}]
            rv.aplicar(p, t)
            estados.append((t, p[0]['epis']['capacete']['estado']))
            t += 1.0 / FPS
    assert not any(e == 'faltando' for tt, e in estados if tt < 28) and rv.enganos == 1
    assert 31.5 <= next(tt for tt, e in estados if e == 'faltando') <= 34.5


def test_servidor_lento_ainda_decide():
    """0,6 quadro analisado por segundo: a janela de 1,5 s nunca juntava 3 observacoes."""
    assert janela_do_ritmo(1.5, 1 / 8) == 1.5           # ritmo normal: nada muda
    rv, estados, t, prox = _Revisor(MODOS['tempo_real']), [], 0.0, 0.0
    while t < 30:
        if t >= prox:
            rv.adicionar(t, {'persons': [{'track_id': 7, 'evidencias': {'capacete': 'inferido'}}]})
            prox += 1 / 0.6
        p = [{'track_id': 7, 'epis': {'capacete': {}}, 'alertas': []}]
        rv.aplicar(p, t)
        estados.append((t, p[0]['epis']['capacete']['estado']))
        t += 0.1
    t_falta = next((tt for tt, e in estados if e == 'faltando'), None)
    assert t_falta is not None and t_falta < 9
    assert all(e == 'faltando' for tt, e in estados if tt > t_falta + 0.2)
    an = PPEAnalyzer.__new__(PPEAnalyzer)               # so a parte do ritmo, sem carregar modelo
    an.cfg, an._t_ant, an._dt = dict(CFG), None, None
    st, t, alerta = _ItemState(), 0.0, None
    for _ in range(18):
        c = an._cfg_do_ritmo(t)
        st.update(t, 'inferido', 0.5, c)
        if st.snapshot(t, c)['alerta'] and alerta is None:
            alerta = t
        t += 1 / 0.6
    assert alerta is not None and alerta < 12


def _episodio(dur, quadros, fortes, ok_depois, volta_em):
    return {'tipo': 'violacao', 'inicio': 100.0, 'visto': 100.0 + dur, 'quadros': quadros, 'fortes': fortes,
            'ok_depois': ok_depois, 'ok_em': None if volta_em is None else 100.0 + dur + volta_em}


def test_historico_marca_provavel_engano():
    assert auditoria.avaliar(_episodio(5, 10, 1, 4, 2))[0] == 'engano'      # curta, deduzida, EPI voltou
    assert auditoria.avaliar(_episodio(60, 100, 5, 4, 2))[0] == ''          # longa: e falta
    assert auditoria.avaliar(_episodio(5, 10, 8, 4, 2))[0] == ''            # o modelo VIU a ausencia
    assert auditoria.avaliar(_episodio(5, 10, 1, 0, None))[0] == ''         # o EPI nao voltou
    assert auditoria.avaliar(_episodio(5, 10, 1, 4, 15))[0] == ''           # voltou tarde demais
    assert auditoria.avaliar(dict(_episodio(5, 10, 1, 4, 2), tipo='queda'))[0] == ''


def test_epis_da_camera_na_config():
    assert normalizar_config({})['epis'] is None                              # sem escolha: os do modelo
    c = normalizar_config({'epis': ['capacete', 'luvas']})
    assert c['epis'] == ['capacete', 'luvas']
    assert normalizar_config({'modo': 'detalhado'}, c)['epis'] == ['capacete', 'luvas']   # outra mudanca nao apaga
    assert normalizar_config({'epis': []}, c)['epis'] == []                   # tudo desligado e uma escolha
    assert normalizar_config({'epis': None}, c)['epis'] is None               # "Padrao" volta ao modelo


def test_area_menor_vale_sobre_a_camera_inteira():
    zonas = [{'id': 'a', 'area_id': 'G', 'pontos': [[0, 0], [1, 0], [1, 1], [0, 1]]},
             {'id': 'b', 'area_id': 'S', 'pontos': [[0.2, 0.2], [0.6, 0.2], [0.6, 0.9], [0.2, 0.9]]}]
    idx = {'G': {'id': 'G', 'nome': 'Geral', 'epis_obrigatorios': ['capacete']},
           'S': {'id': 'S', 'nome': 'Solda', 'epis_obrigatorios': ['capacete', 'luvas']}}
    r = areas.ResolvedorDeArea(zonas, idx, ['colete'], 1000, 1000)
    assert r.para_box((300, 300, 500, 800))[1]['nome'] == 'Solda'
    assert r.para_box((700, 300, 900, 800))[1]['nome'] == 'Geral'
