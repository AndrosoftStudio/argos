"""Tempo real imediato, avisos falados e busca de cameras: o que da para conferir sem banco,
sem GPU, sem modelo e sem rede."""
import os
import sys

import pytest

pytest.importorskip('cv2')
os.environ.setdefault('ARGOS_MIDIA_BAIXAR', '0')        # nenhum teste baixa programa
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))

import camera_discovery  # noqa: E402
import voz  # noqa: E402
from live_pipeline import MODOS, config_publica, normalizar_config  # noqa: E402


# ── modo tempo real: nao guarda video ────────────────────────────────
def test_tempo_real_nao_reserva_video():
    m = MODOS['tempo_real']
    assert m['imediato'] is True
    assert m['atraso'] == 0 and m['atraso_max'] == 0 and m['futuro'] == 0


def test_modos_detalhados_continuam_com_atraso():
    assert MODOS['detalhado']['atraso'] == 1.5 and not MODOS['detalhado'].get('imediato')
    assert MODOS['super']['atraso'] == 4.0 and not MODOS['super'].get('imediato')


def test_config_diz_se_e_imediato():
    assert config_publica(normalizar_config({'modo': 'tempo_real'}))['imediato'] is True
    assert config_publica(normalizar_config({'modo': 'detalhado'}))['imediato'] is False


# ── analise de rosto por camera ──────────────────────────────────────
def test_rosto_vem_ligado_e_pode_ser_desligado():
    assert normalizar_config({})['rosto'] is True
    assert normalizar_config({'rosto': False})['rosto'] is False
    assert normalizar_config({'rosto': 'nao'})['rosto'] is False
    # trocar outro ajuste nao religa o rosto
    assert normalizar_config({'fps': 30}, normalizar_config({'rosto': False}))['rosto'] is False


# ── frases da TV ─────────────────────────────────────────────────────
def test_frase_com_nome_epis_e_area():
    assert voz.frase('André Jorge', ['capacete', 'oculos'], 'Solda') == \
        'André Jorge, por favor, coloque o capacete e os óculos de proteção, na área Solda.'


def test_frase_sem_nome_e_sem_area():
    f = voz.frase('', ['capacete'], '')
    assert f.startswith('Atenção! Pessoa sem capacete')
    assert 'área' not in f


def test_frase_de_repeticao_muda():
    primeira = voz.frase('Ana', ['luvas'], '')
    segunda = voz.frase('Ana', ['luvas'], '', repeticao=1)
    assert primeira != segunda and 'continua sem' in segunda


def test_frase_nao_lista_epi_demais():
    f = voz.frase('Ana', ['capacete', 'oculos', 'luvas', 'botas', 'colete'], 'Pátio')
    assert 'os outros 3 equipamentos' in f
    assert 'botas' not in f.lower() and 'colete' not in f.lower()


def test_frase_de_queda():
    assert 'queda' in voz.frase('Ana', [], 'Pátio', queda=True).lower()


def test_audio_tem_nome_estavel():
    a, b = voz.id_do_texto('Ana, coloque o capacete.'), voz.id_do_texto('Ana, coloque o capacete.')
    assert a == b and a != voz.id_do_texto('Ana, coloque as luvas.')
    assert a.isalnum()                                   # vira nome de arquivo e parte de endereco


# ── busca de cameras ─────────────────────────────────────────────────
@pytest.mark.parametrize('texto', ['NETSurveillance WEB', 'IP Camera', 'Intelbras', 'Câmera 1', 'ONVIF device', 'DVR Login'])
def test_reconhece_pagina_de_camera(texto):
    assert camera_discovery.CAMERA_HINT_RE.search(texto)


@pytest.mark.parametrize('texto', ['F6201B', 'E2320', 'Roteador', '<html xmlns="http://www.w3.org/1999/xhtml">',
                                   'application/xml', 'ipconfig'])
def test_roteador_nao_vira_camera(texto):
    assert not camera_discovery.CAMERA_HINT_RE.search(texto)


def test_so_pagina_web_nao_pede_senha():
    rec = camera_discovery._blank_device('10.0.0.1')
    rec['open_ports'] = [80, 443]
    camera_discovery._conferir_camera(rec, '', '', 0.2)
    assert rec['precisa_senha'] is False


def test_onvif_sem_perfis_pede_senha():
    rec = camera_discovery._blank_device('10.0.0.2')
    rec['open_ports'] = [80, 8899]
    camera_discovery._conferir_camera(rec, '', '', 0.2)
    assert rec['precisa_senha'] is True and rec['senha_errada'] is False
    camera_discovery._conferir_camera(rec, 'admin', 'errada', 0.2)
    assert rec['senha_errada'] is True


def test_nome_de_pagina_de_erro_nao_vira_nome_de_camera():
    rec = camera_discovery._blank_device('10.0.0.3')
    rec['http'] = {'title': '404 - Not Found'}
    assert camera_discovery._display_name(rec) == 'Câmera 10.0.0.3'
    rec['http'] = {'title': 'NETSurveillance WEB'}
    assert camera_discovery._display_name(rec).startswith('NETSurveillance WEB')
