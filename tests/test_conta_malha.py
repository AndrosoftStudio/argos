"""Tokens do site (Ed25519) e caminhos de arquivo da malha. Nao precisam de banco nem de internet."""
import base64
import json
import os
import sys
import time

import pytest

pytest.importorskip('cryptography')
pytest.importorskip('psycopg')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

import conta  # noqa: E402
import malha  # noqa: E402


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


@pytest.fixture()
def chave(monkeypatch):
    privada = Ed25519PrivateKey.generate()
    monkeypatch.setattr(conta, '_chave', privada.public_key())
    return privada


def _assinar(privada, dados, alg='EdDSA'):
    cab = _b64(json.dumps({'alg': alg, 'typ': 'JWT'}).encode())
    corpo = _b64(json.dumps(dados).encode())
    return cab + '.' + corpo + '.' + _b64(privada.sign((cab + '.' + corpo).encode()))


def test_token_valido(chave):
    t = _assinar(chave, {'tipo': 'conta', 'sub': 'abc', 'exp': time.time() + 60})
    assert conta.ler_token(t)['sub'] == 'abc'


def test_token_vencido(chave):
    assert conta.ler_token(_assinar(chave, {'tipo': 'conta', 'sub': 'abc', 'exp': time.time() - 1})) is None


def test_token_adulterado(chave):
    t = _assinar(chave, {'tipo': 'conta', 'sub': 'abc'})
    cab, _corpo, sig = t.split('.')
    outro = _b64(json.dumps({'tipo': 'conta', 'sub': 'admin'}).encode())
    assert conta.ler_token(cab + '.' + outro + '.' + sig) is None


def test_token_de_outra_chave(chave):
    assert conta.ler_token(_assinar(Ed25519PrivateKey.generate(), {'tipo': 'conta', 'sub': 'abc'})) is None


def test_token_alg_none_recusado(chave):
    cab = _b64(json.dumps({'alg': 'none'}).encode())
    corpo = _b64(json.dumps({'tipo': 'conta', 'sub': 'abc'}).encode())
    assert conta.ler_token(cab + '.' + corpo + '.') is None


def test_chave_publica_embutida_carrega(monkeypatch):
    monkeypatch.setattr(conta, '_chave', None)
    assert conta._chave_publica() is not None


def test_token_antigo_nao_parece_do_site():
    assert not conta.parece_token_do_site('cam_0123456789abcdef')
    assert not conta.parece_token_do_site('5b0c1d9e-1111-2222-3333-444455556666')
    assert conta.parece_token_do_site('a.b.c')


def test_relativo_de_caminho_docker_e_windows():
    assert malha.relativo('/app/dados/users/u1/epis/x/a.jpg') == 'users/u1/epis/x/a.jpg'
    assert malha.relativo('C:\\Argos\\dados\\users\\u1\\funcs\\b.png') == 'users/u1/funcs/b.png'
    assert malha.relativo('/outro/lugar/a.jpg') == ''


def test_arquivo_so_da_propria_conta(tmp_path, monkeypatch):
    monkeypatch.setattr(malha, 'BASE_DADOS', str(tmp_path))
    pasta = tmp_path / 'users' / 'u1' / 'epis'
    pasta.mkdir(parents=True)
    (pasta / 'a.jpg').write_bytes(b'x')
    (tmp_path / 'users' / 'u2').mkdir(parents=True)
    (tmp_path / 'users' / 'u2' / 'b.jpg').write_bytes(b'y')
    assert malha.rel_seguro('users/u1/epis/a.jpg', 'u1')
    assert not malha.rel_seguro('users/u2/b.jpg', 'u1')                 # outra conta
    assert not malha.rel_seguro('users/u1/../u2/b.jpg', 'u1')           # escapando com ..
    assert not malha.rel_seguro('users/u1/epis/nao_existe.jpg', 'u1')
    assert not malha.rel_seguro('../../etc/passwd', 'u1')
