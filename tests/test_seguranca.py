"""Testes das regras de seguranca (backend/seguranca.py).

Rodam sem GPU, banco ou OpenCV:  python -m pytest tests
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))
os.environ.setdefault('ARGOS_PBKDF2_ITERACOES', '1000')  # rapido no teste; producao usa 600 mil

import seguranca as s  # noqa: E402


# ── senhas ──────────────────────────────────────────────────────────
def test_senha_nova_confere_e_tem_sal():
    a, b = s.hash_senha('segredo1'), s.hash_senha('segredo1')
    assert a != b, 'mesma senha precisa gerar hashes diferentes (sal)'
    assert s.conferir_senha('segredo1', a) == (True, False)
    assert s.conferir_senha('errada', a) == (False, False)


def test_senha_antiga_sha256_confere_e_pede_regravacao():
    antigo = hashlib.sha256(b'segredo1').hexdigest()
    assert s.conferir_senha('segredo1', antigo) == (True, True)
    assert s.conferir_senha('outra', antigo) == (False, False)


def test_hash_corrompido_nao_confere():
    assert s.conferir_senha('x', 'pbkdf2_sha256$abc$zz$zz') == (False, False)
    assert s.conferir_senha('x', '') == (False, False)


# ── documento e e-mail ──────────────────────────────────────────────
def test_documento_aceita_com_e_sem_pontuacao():
    assert s.documento_valido('123.456.789-00')
    assert s.documento_valido('12345678900')
    assert s.documento_valido('12.345.678/0001-90')
    assert not s.documento_valido('123')


def test_email():
    assert s.email_valido('ana@empresa.com')
    assert not s.email_valido('ana@empresa')
    assert not s.email_valido('ana empresa.com')


# ── ids e arquivos ──────────────────────────────────────────────────
def test_id_bloqueia_travessia_de_pasta():
    assert s.id_valido('3f2b9c1e-7d6a-4f7e-9a51-0c9d2b1e8a77')
    for ruim in ('..', '../x', 'a/b', 'a\\b', '', 'a.b'):
        assert not s.id_valido(ruim), ruim


def test_extensao_de_foto_so_imagem():
    assert s.extensao_permitida('rosto.PNG', s.EXTENSOES_FOTO, '.jpg') == '.png'
    assert s.extensao_permitida('ataque.html', s.EXTENSOES_FOTO, '.jpg') == '.jpg'
    assert s.extensao_permitida('../../app.py', s.EXTENSOES_VIDEO, '.mp4') == '.mp4'
    assert s.extensao_permitida(None, s.EXTENSOES_VIDEO, '.mp4') == '.mp4'


# ── cameras ─────────────────────────────────────────────────────────
UID = '3f2b9c1e-7d6a-4f7e-9a51-0c9d2b1e8a77'


def test_sid_do_celular_igual_ao_cam_html():
    assert s.sid_do_token_camera('cam_0123456789abcdef') == 'remote_cam_0123456789abcdef'
    assert s.sid_do_token_camera('') == ''


def test_stream_da_conta():
    token = 'cam_0123456789abcdef'
    assert s.stream_da_conta(UID + '_0', UID)
    assert s.stream_da_conta('remote_' + token, UID, [token])
    assert not s.stream_da_conta('remote_' + token, UID, ['cam_outro'])
    assert not s.stream_da_conta('outrouid_0', UID)
    assert not s.stream_da_conta('../' + UID + '_0', UID)


def test_fonte_de_camera():
    for ok in ('webcam', '0', 'rtsp://10.0.0.5/stream1', 'http://10.0.0.5/video', 'https://x/y.m3u8'):
        assert s.fonte_camera_valida(ok), ok
    for ruim in ('/etc/passwd', 'C:\\dados\\video.mp4', '', 'file:///etc/passwd', None):
        assert not s.fonte_camera_valida(ruim), ruim


# ── tentativas de login ─────────────────────────────────────────────
def test_limite_de_tentativas():
    lim = s.LimiteTentativas(maximo=3, janela_s=60)
    for _ in range(2):
        lim.falhou('ana@x.com')
    assert lim.bloqueado('ana@x.com') == 0
    lim.falhou('ana@x.com')
    assert lim.bloqueado('ana@x.com') > 0
    assert lim.bloqueado('outra@x.com') == 0
    lim.acertou('ana@x.com')
    assert lim.bloqueado('ana@x.com') == 0
