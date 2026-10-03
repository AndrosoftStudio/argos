"""
conta.py - Vinculo deste servidor a uma conta do site e tokens de quem entra.

As contas vivem no Supabase, atras da API do site (argosepi.vercel.app/api).
O servidor nao guarda senha de ninguem:

  1. Na primeira vez que liga, pede um codigo de vinculo para a API e abre o
     site (argosepi.vercel.app/parear.html). O site acha este servidor sozinho
     (le o codigo em http://127.0.0.1:<porta>/pareamento, que so responde ao
     proprio computador), a pessoa entra na conta dela e aprova.
  2. O servidor recebe uma credencial assinada pela API e guarda no proprio
     banco (tabela servidor_local). A partir dai processa so para essa conta.
  3. Quem abre o painel manda o token da conta (assinado pela API com Ed25519);
     aqui so se confere a assinatura com a chave publica, sem ir na internet.
  4. A cada minuto o servidor avisa a API que esta vivo e recebe a lista dos
     outros servidores da mesma conta (a malha, ver malha.py).
"""
import base64
import json
import os
import socket
import threading
import time
import webbrowser

import requests

import db

API_URL = (os.environ.get('ARGOS_API_URL') or 'https://argosepi.vercel.app/api').strip().rstrip('/')
SITE_URL = API_URL[:-4] if API_URL.endswith('/api') else 'https://argosepi.vercel.app'
# Chave publica do site. A privada fica so na Vercel (ARGOS_CHAVE_PRIVADA).
CHAVE_PUBLICA_PADRAO = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEABreI+YNNatkR9G2LibIHYt+4mXn3yfWLESloxDvHwc4=
-----END PUBLIC KEY-----"""
SINAL_S = 60

_chave = None
_lock = threading.Lock()
_acordar = threading.Event()
_thread_iniciada = False
_estado = {'vinculado': False, 'conta': None, 'servidor': None, 'codigo': '', 'link': '',
           'expira_em': 0, 'erro': '', 'pares': [], 'ultimo_sinal': 0.0}
_ouvintes = []          # funcoes chamadas quando a conta/pares mudam (malha, espelho da conta)


# ── Tokens (JWT EdDSA) ──────────────────────────────────────────────

def _b64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def _chave_publica():
    global _chave
    if _chave is None:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        pem = os.environ.get('ARGOS_CHAVE_PUBLICA', '').strip().replace('\\n', '\n') or CHAVE_PUBLICA_PADRAO
        if 'BEGIN' not in pem:
            pem = '-----BEGIN PUBLIC KEY-----\n' + pem + '\n-----END PUBLIC KEY-----'
        _chave = load_pem_public_key(pem.encode())
    return _chave


def ler_token(token: str):
    """Conteudo do token se a assinatura do site confere e ele nao venceu; senao None."""
    partes = str(token or '').split('.')
    if len(partes) != 3:
        return None
    try:
        if json.loads(_b64(partes[0])).get('alg') != 'EdDSA':
            return None
        _chave_publica().verify(_b64(partes[2]), (partes[0] + '.' + partes[1]).encode())
        dados = json.loads(_b64(partes[1]))
    except Exception:
        return None
    if dados.get('exp') and float(dados['exp']) < time.time():
        return None
    return dados


def parece_token_do_site(token: str) -> bool:
    return str(token or '').count('.') == 2


# ── Estado guardado no banco local ──────────────────────────────────

def _ler(chave: str):
    l = db.consultar_um('SELECT valor FROM servidor_local WHERE chave=%s', (chave,))
    return l['valor'] if l else None


def _gravar(chave: str, valor):
    if valor is None:
        db.executar('DELETE FROM servidor_local WHERE chave=%s', (chave,))
    else:
        db.executar('INSERT INTO servidor_local (chave, valor) VALUES (%s,%s)'
                    ' ON CONFLICT (chave) DO UPDATE SET valor=EXCLUDED.valor', (chave, str(valor)))


def _carregar_credencial() -> bool:
    cred = _ler('credencial')
    dados = ler_token(cred) if cred else None
    if not dados or dados.get('tipo') != 'servidor':
        return False
    conta = json.loads(_ler('conta') or 'null') or {'id': dados.get('conta')}
    with _lock:
        _estado.update(vinculado=True, servidor={'id': dados.get('sub'), 'nome': _ler('servidor_nome') or ''},
                       conta=conta, codigo='', link='', expira_em=0)
    return True


def credencial() -> str:
    return _ler('credencial') or ''


def vinculado() -> bool:
    return bool(_estado['vinculado'])


def conta_id() -> str:
    c = _estado.get('conta') or {}
    return str(c.get('id') or '')


def servidor_id() -> str:
    s = _estado.get('servidor') or {}
    return str(s.get('id') or '')


def dados_conta() -> dict:
    with _lock:
        return dict(_estado.get('conta') or {})


def pares() -> list:
    with _lock:
        return list(_estado['pares'])


def estado_publico() -> dict:
    with _lock:
        e = dict(_estado)
    conta = e.get('conta') or {}
    return {'vinculado': e['vinculado'], 'conta': {'nome': conta.get('nome'), 'email': conta.get('email')}
            if e['vinculado'] else None, 'servidor': e.get('servidor'), 'codigo': e['codigo'],
            'link': e['link'], 'expira_em': e['expira_em'], 'erro': e['erro'],
            'ultimo_sinal': e['ultimo_sinal'], 'pares': len(e['pares'])}


def ao_mudar(funcao):
    _ouvintes.append(funcao)


def _avisar():
    for f in list(_ouvintes):
        try:
            f()
        except Exception as e:
            print(f'[conta] aviso falhou: {e}')


def desvincular():
    """Esquece a credencial (o site removeu o servidor): volta a pedir vinculo."""
    for k in ('credencial', 'conta', 'servidor_nome'):
        _gravar(k, None)
    with _lock:
        _estado.update(vinculado=False, conta=None, servidor=None, pares=[])
    _avisar()
    _acordar.set()


# ── Conversa com a API do site ──────────────────────────────────────

def _post(caminho, corpo, token=None, timeout=15):
    h = {'Content-Type': 'application/json'}
    if token:
        h['Authorization'] = 'Bearer ' + token
    return requests.post(API_URL + caminho, json=corpo, headers=h, timeout=timeout)


def link_vincular(porta) -> str:
    return f'{SITE_URL}/parear.html?porta={porta}'


def _anunciar(codigo, site):
    linha = '=' * 64
    print(f"\n{linha}\n  VINCULE ESTE SERVIDOR A SUA CONTA DO ARGOS EPI\n"
          f"  Neste computador, abra: {site}\n"
          f"  (ou no painel: Ajustes > Servidores > Adicionar este computador)\n"
          f"  O site encontra o servidor sozinho. Entre na conta e clique em Vincular.\n"
          f"  Se o site nao achar, digite o codigo: {codigo}   (vale 15 minutos)\n{linha}\n", flush=True)


def _parear(info, abrir_navegador, porta, ja_abriu):
    corpo = {'nome': os.environ.get('ARGOS_SERVIDOR_NOME') or socket.gethostname(),
             'node_id': info.get('node_id', ''), 'hardware': info.get('hardware') or {}}
    r = _post('/parear/iniciar', corpo)
    r.raise_for_status()
    p = r.json()
    site = link_vincular(porta)
    with _lock:
        _estado.update(codigo=p['codigo'], link=p['link'], expira_em=p['expira_em'], erro='')
    _anunciar(p['codigo'], site)
    if abrir_navegador and not ja_abriu:
        try:
            webbrowser.open(site)
        except Exception:
            pass
    while time.time() < float(p['expira_em']):
        time.sleep(float(p.get('intervalo_s') or 3))
        r = _post(f"/parear/{p['codigo']}/concluir", {'segredo': p['segredo']})
        if r.status_code in (403, 404, 409):
            return False                       # venceu ou foi usado: pede outro codigo
        r.raise_for_status()
        d = r.json()
        if d.get('pendente'):
            continue
        _gravar('credencial', d['credencial'])
        _gravar('conta', json.dumps(d.get('conta') or {}))
        _gravar('servidor_nome', (d.get('servidor') or {}).get('nome') or '')
        _carregar_credencial()
        print(f"[conta] Servidor vinculado a conta de {(d.get('conta') or {}).get('nome')}.", flush=True)
        _avisar()
        return True
    return False


def _sinal(info):
    r = _post('/servidores/sinal', info, token=credencial())
    if r.status_code == 401:
        print('[conta] Este servidor foi desvinculado no site. Pedindo um vinculo novo...', flush=True)
        desvincular()
        return
    r.raise_for_status()
    d = r.json()
    conta = d.get('conta') or {}
    mudou_conta = conta and conta != (_estado.get('conta') or {})
    if conta:
        _gravar('conta', json.dumps(conta))
    nome = (d.get('servidor') or {}).get('nome')
    if nome:
        _gravar('servidor_nome', nome)
    with _lock:
        pares_antes = [(p.get('id'), p.get('url'), p.get('url_local'), p.get('online')) for p in _estado['pares']]
        _estado.update(pares=d.get('pares') or [], ultimo_sinal=time.time(), erro='')
        if conta:
            _estado['conta'] = conta
        if nome:
            _estado['servidor'] = {'id': servidor_id(), 'nome': nome}
        pares_depois = [(p.get('id'), p.get('url'), p.get('url_local'), p.get('online')) for p in _estado['pares']]
    if mudou_conta or pares_antes != pares_depois:
        _avisar()


def _loop(info_fn, abrir_navegador, porta):
    ja_abriu = False
    while True:
        try:
            if not _estado['vinculado'] and not _carregar_credencial():
                if _parear(info_fn(), abrir_navegador, porta, ja_abriu):
                    _avisar()
                ja_abriu = True
                continue
            _sinal(info_fn())
        except requests.RequestException as e:
            with _lock:
                _estado['erro'] = 'Sem conexão com o site do Argos (' + type(e).__name__ + ')'
            print(f'[conta] {_estado["erro"]}', flush=True)
            time.sleep(15)
            continue
        except Exception as e:
            with _lock:
                _estado['erro'] = str(e)
            print(f'[conta] erro: {e}', flush=True)
            time.sleep(15)
            continue
        _acordar.wait(SINAL_S)
        _acordar.clear()


def sinal_agora():
    """Antecipa o proximo sinal (o endereco do tunel mudou, por exemplo)."""
    _acordar.set()


def iniciar(info_fn, abrir_navegador=False, porta=8088):
    """info_fn() -> dict com url, url_local, tipo, versao, node_id, hardware, estado."""
    global _thread_iniciada
    if _thread_iniciada:
        return
    _thread_iniciada = True
    _carregar_credencial()
    threading.Thread(target=_loop, args=(info_fn, abrir_navegador, porta), daemon=True,
                     name='conta-vinculo').start()
