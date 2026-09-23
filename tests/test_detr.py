"""O detector reconhece a arquitetura (YOLO ou RT-DETR) sem baixar pesos: as redes sao montadas
pelos .yaml que vem dentro do Ultralytics, com pesos aleatorios."""
import os
import sys

import pytest

pytest.importorskip('ultralytics')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))

from ultralytics import RTDETR, YOLO  # noqa: E402

import epi_detector  # noqa: E402


@pytest.fixture(scope='module')
def detr():
    return RTDETR('rtdetr-l.yaml')


@pytest.fixture(scope='module')
def yolo():
    return YOLO('yolo26n.yaml')


def test_rtdetr_e_reconhecido_como_detr(detr):
    assert epi_detector.arquitetura_de(detr) == 'detr'


def test_yolo_continua_yolo(yolo):
    assert epi_detector.arquitetura_de(yolo) == 'yolo'


def test_formato_exportado_cai_em_yolo():
    class Exportado:  # TensorRT/ONNX: o Ultralytics nao expoe as camadas
        model = object()
    assert epi_detector.arquitetura_de(Exportado()) == 'yolo'


def test_padrao_e_o_modelo_que_detecta_melhor(tmp_path):
    import json
    import time
    assert epi_detector.melhor_modelo_argos(str(tmp_path)) is None
    for nome, nota in (('argos_epi_v1', 0.683), ('argos_epi_detr_v1', 0.61), ('outro_modelo', 0.99)):
        (tmp_path / f'{nome}.pt').write_bytes(b'x')
        (tmp_path / f'{nome}.json').write_text(json.dumps({'map50_teste': nota}))
        time.sleep(0.02)
    # o DETR e o mais recente, mas detecta pior: o YOLO continua padrao
    assert epi_detector.melhor_modelo_argos(str(tmp_path)) == 'argos_epi_v1.pt'
    (tmp_path / 'argos_epi_detr_v1.json').write_text(json.dumps({'map50_teste': 0.72}))
    assert epi_detector.melhor_modelo_argos(str(tmp_path)) == 'argos_epi_detr_v1.pt'
    (tmp_path / 'argos_epi_v2.pt').write_bytes(b'x')  # sem .json: fica atras dos que tem nota
    assert epi_detector.melhor_modelo_argos(str(tmp_path)) == 'argos_epi_detr_v1.pt'


def test_info_do_modelo_le_arquitetura_e_guarda(tmp_path, detr):
    caminho = str(tmp_path / 'argos_epi_detr_teste.pt')
    detr.save(caminho)
    info = epi_detector.info_do_modelo(caminho)
    assert info['arquitetura'] == 'detr'
    assert len(info['classes']) == 80
    info['classes'].clear()  # quem chama pode mexer na copia sem estragar o que ficou guardado
    assert len(epi_detector.info_do_modelo(caminho)['classes']) == 80
    assert epi_detector.arquitetura_do_arquivo(str(tmp_path / 'nao_existe.pt')) == 'yolo'
