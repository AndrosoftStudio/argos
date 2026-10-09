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


# ── sem pedir permissao de administrador ─────────────────────────────
def test_video_direto_nao_abre_porta_fixa():
    """Porta UDP fixa em todas as placas de rede faz o Windows mostrar o aviso do Firewall (que so
    um administrador consegue aceitar): por padrao o MediaMTX nao escuta em porta nenhuma."""
    import rtc
    base = {'api': 9001, 'rtsp': 9002, 'http': 9003}
    assert "webrtcLocalUDPAddress: ''" in rtc._config(dict(base, udp=0))
    assert "webrtcLocalTCPAddress: ''" in rtc._config(dict(base, udp=0))
    assert 'webrtcLocalUDPAddress: ' + rtc.HOST + ':8189' in rtc._config(dict(base, udp=8189))
    # tudo o que escuta fica so nesta maquina
    for linha in rtc._config(dict(base, udp=0)).splitlines():
        if linha.startswith(('apiAddress', 'rtspAddress', 'webrtcAddress')):
            assert '127.0.0.1:' in linha


def test_camera_rtsp_so_por_tcp():
    """O leitor de RTSP nao tenta UDP (que abre portas e chama o aviso do Firewall)."""
    import processor  # noqa: F401
    assert 'rtsp_transport;tcp' in os.environ.get('OPENCV_FFMPEG_CAPTURE_OPTIONS', '')


# ── rede perdendo pacotes: volta ao envio normal ─────────────────────
def test_perda_de_pacotes_derruba_o_video_direto(monkeypatch):
    import processor
    import rtc

    class Falso:
        PERDA_LIMITE = processor.VideoProcessor.PERDA_LIMITE
        PERDA_JANELAS = processor.VideoProcessor.PERDA_JANELAS
        client_id = 'remote_x'
        _conferir_perda = processor.VideoProcessor._conferir_perda

        def _log(self, *a, **k):
            pass

    medidas = iter([(0, 0), (1000, 0), (2000, 5), (3000, 10),              # 0,5% de perda: segue direto
                    (3900, 110), (4800, 210), (5700, 310)])                # 10% em 3 medidas: cai
    monkeypatch.setattr(rtc, 'chegada', lambda sid: next(medidas))
    p, estado = Falso(), {}
    assert [p._conferir_perda(estado) for _ in range(6)] == [False] * 6
    assert p._conferir_perda(estado) is True
    assert p._direto_aviso == ('perda', 120)        # 1a queda: 2 min ate tentar de novo


def test_sem_medida_nao_derruba(monkeypatch):
    import processor
    import rtc

    class Falso:
        PERDA_LIMITE, PERDA_JANELAS, client_id = 0.02, 3, 'remote_x'
        _conferir_perda = processor.VideoProcessor._conferir_perda

        def _log(self, *a, **k):
            pass

    monkeypatch.setattr(rtc, 'chegada', lambda sid: None)
    assert Falso()._conferir_perda({}) is False


# ── 20.4.2: Full HD de verdade, caixa no quadro certo, analise sem erro no TensorRT ─────
def test_resolucao_conta_o_lado_menor():
    """1080p e Full HD com o celular em pe ou deitado: a imagem em pe (1080x1920) nao pode ser
    encolhida para 608x1080, e acima do pedido ela desce pelo lado menor."""
    import numpy as np
    import processor

    class Falso:
        config = {'resolucao': 1080}
        _limitar = processor.VideoProcessor._limitar

    p = Falso()
    assert p._limitar(np.zeros((1920, 1080, 3), np.uint8)).shape[:2] == (1920, 1080)
    assert p._limitar(np.zeros((1080, 1920, 3), np.uint8)).shape[:2] == (1080, 1920)
    p.config = {'resolucao': 720}
    assert p._limitar(np.zeros((1920, 1080, 3), np.uint8)).shape[:2] == (1280, 720)


def test_tempo_real_nao_encolhe_a_imagem_antes_da_analise():
    """Quem esta longe some quando a imagem e encolhida: no tempo real ela vai inteira."""
    import numpy as np
    from live_pipeline import LivePipeline
    pl = LivePipeline(lambda *a: None, nome='t')
    pl.configurar('tempo_real')
    img = np.zeros((1080, 1920, 3), np.uint8)
    pl.receber_imagem(img, t=1.0, largura_max=None, sessao='direto', extra={'tv': 20.0, 'ts': 1.0, 'ep': 1, 'rtp': 7})
    q = pl.quadros[-1]
    assert (q.w, q.h) == (1920, 1080) and q.img is img
    assert q.extra['rtp'] == 7                      # a hora do quadro vai junto ate o resultado
    pl.receber_imagem(img, t=2.0, largura_max=960)  # os outros caminhos continuam com o limite
    assert pl.quadros[-1].w == 960


def test_quadro_chave_h264_e_vp8():
    """O relogio do video so precisa achar os quadros-chave nos cabecalhos, sem decodificar."""
    import rtc
    rtp = bytes(12)
    assert rtc._quadro_chave('H264', rtp + bytes([0x65, 0]), 12)            # IDR
    assert rtc._quadro_chave('H264', rtp + bytes([0x67, 0]), 12)            # SPS
    assert not rtc._quadro_chave('H264', rtp + bytes([0x41, 0]), 12)        # quadro comum
    assert rtc._quadro_chave('H264', rtp + bytes([0x7C, 0x85]), 12)         # FU-A comecando um IDR
    assert not rtc._quadro_chave('H264', rtp + bytes([0x7C, 0x81]), 12)
    stap = bytes([0x78, 0, 2, 0x67, 0, 0, 2, 0x68, 0])                      # STAP-A com SPS dentro
    assert rtc._quadro_chave('H264', rtp + stap, 12)
    assert rtc._quadro_chave('VP8', rtp + bytes([0x10, 0x00, 0, 0]), 12)   # comeco de quadro-chave
    assert not rtc._quadro_chave('VP8', rtp + bytes([0x10, 0x01, 0, 0]), 12)


def test_ponte_do_relogio_do_video():
    """Um quadro-chave visto pelos dois lados (leitura crua e OpenCV) da a base; nenhum ou dois perto: sem base."""
    import rtc
    r = rtc.RelogioRtp('rtsp://127.0.0.1:1/x')
    r.chaves.append((1000000, 10.0))
    assert r.base_para(90000, 10.05) == 1000000 - 90000
    assert r.base_para(90000, 20.0) is None
    r.chaves.append((1001000, 10.2))
    assert r.base_para(90000, 10.25) is None


def test_tensorrt_recortes_um_de_cada_vez():
    """O modelo convertido aceita uma imagem por vez: os recortes da segunda olhada iam juntos e o
    quadro inteiro se perdia ('input size [2, 3, 640, 640] not equal to max model size')."""
    import numpy as np
    import epi_detector

    class Modelo:
        def __init__(self):
            self.chamadas = []

        def predict(self, fonte, **k):
            self.chamadas.append(fonte)
            if isinstance(fonte, list) and len(fonte) > 1:
                raise RuntimeError('input size not equal to max model size')
            return [type('R', (), {'boxes': None})()]

    d = epi_detector.EpiDetector.__new__(epi_detector.EpiDetector)
    d.model, d.backend, d.device, d.half, d.arquitetura = Modelo(), 'tensorrt', '0', False, 'yolo'
    d._falhas_trt, d.tam_trt, d.log = 0, 640, lambda *a: None
    img = np.zeros((720, 1280, 3), np.uint8)
    assert d.detect_crops(img, [(0, 0, 200, 400), (300, 0, 500, 400), (600, 0, 800, 400)]) == []
    assert len(d.model.chamadas) == 3 and not any(isinstance(c, list) for c in d.model.chamadas)


def test_tensorrt_que_falha_volta_ao_modelo_original(monkeypatch):
    import numpy as np
    import epi_detector
    import ultralytics

    class Quebrado:
        def predict(self, *a, **k):
            raise RuntimeError('CUDA error')

    class Original:
        names = {0: 'capacete'}

        def __init__(self, caminho):
            self.caminho = caminho

        def predict(self, *a, **k):
            return [type('R', (), {'boxes': None})()]

    monkeypatch.setattr(ultralytics, 'YOLO', Original)
    d = epi_detector.EpiDetector.__new__(epi_detector.EpiDetector)
    d.model, d.backend, d.device, d.half, d.arquitetura = Quebrado(), 'tensorrt', 'cpu', False, 'yolo'
    d._falhas_trt, d.tam_trt, d.log, d.model_path = 0, 640, lambda *a: None, 'argos.pt'
    img = np.zeros((64, 64, 3), np.uint8)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            d.detect(img)
    assert d.detect(img) == [] and d.backend == 'pytorch' and d.model.caminho == 'argos.pt'
