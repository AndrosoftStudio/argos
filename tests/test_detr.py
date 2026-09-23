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


def test_info_do_modelo_le_arquitetura_e_guarda(tmp_path, detr):
    caminho = str(tmp_path / 'argos_epi_detr_teste.pt')
    detr.save(caminho)
    info = epi_detector.info_do_modelo(caminho)
    assert info['arquitetura'] == 'detr'
    assert len(info['classes']) == 80
    info['classes'].clear()  # quem chama pode mexer na copia sem estragar o que ficou guardado
    assert len(epi_detector.info_do_modelo(caminho)['classes']) == 80
    assert epi_detector.arquitetura_do_arquivo(str(tmp_path / 'nao_existe.pt')) == 'yolo'
