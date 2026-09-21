"""
_comum.py - Acha o PC que esta treinando e busca o status dele.

E o mesmo codigo nos dois jeitos de rodar o site:
  - Vercel: as funcoes api/status.py e api/conexao.py importam daqui;
  - Docker: app.py importa daqui e ainda serve a pagina.

Na Vercel cada pedido roda numa funcao sem estado, entao a descoberta acontece no
proprio pedido e fica guardada so enquanto a funcao continua quente.

Variaveis de ambiente (todas opcionais):
    HUB_URL         hub de discovery (padrao: o hub do Argos no Render)
    NODE_ID         fixa um painel especifico, quando ha mais de um PC treinando
    PAINEL_URL      pula a descoberta e usa esta URL
    PAINEL_TOKEN    mesmo valor do --token do painel, se ele exigir
    HUB_API_KEY     so se o hub exigir chave
    DESCOBERTA_S    por quanto tempo a resposta do hub vale (padrao 20 s)
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request


def _env(nome, padrao=''):
    """Variavel vazia conta como nao definida: na Vercel e facil deixar uma sem valor."""
    return (os.environ.get(nome) or padrao).strip()


HUB_URL = _env('HUB_URL', 'https://backend-hub-vigilanciaepi.onrender.com').rstrip('/')
NODE_ID = _env('NODE_ID')
PAINEL_URL = _env('PAINEL_URL').rstrip('/')
PAINEL_TOKEN = _env('PAINEL_TOKEN')
HUB_API_KEY = _env('HUB_API_KEY')
try:
    DESCOBERTA_S = float(_env('DESCOBERTA_S', '20'))
except ValueError:
    DESCOBERTA_S = 20.0
# a Vercel corta a funcao em 10 s; dois pedidos curtos cabem com folga
ESPERA_HUB_S = 6
ESPERA_PAINEL_S = 6

JSON = 'application/json; charset=utf-8'

_trava = threading.Lock()
_cache = {'no': None, 'erro': None, 'em': 0.0}


def pedir(url, headers=None, timeout=ESPERA_HUB_S):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8') or '{}')


def _perguntar_ao_hub():
    headers = {'Authorization': 'Bearer ' + HUB_API_KEY} if HUB_API_KEY else {}
    caminho = '/training/nodes' if NODE_ID else '/training/best'
    dados = pedir(HUB_URL + caminho, headers)
    nos = dados.get('nodes') or ([dados['node']] if dados.get('node') else [])
    if NODE_ID:
        nos = [n for n in nos if n.get('node_id') == NODE_ID]
    if not nos:
        raise LookupError('nenhum painel de treino registrado no hub')
    return nos[0]


def descobrir(forcar=False):
    """Devolve o registro do painel, ou None. Guarda a resposta por DESCOBERTA_S."""
    if PAINEL_URL:
        return {'url': PAINEL_URL, 'node_id': 'manual', 'host': None, 'origem': 'PAINEL_URL'}
    with _trava:
        if not forcar and _cache['no'] and (time.time() - _cache['em']) < DESCOBERTA_S:
            return _cache['no']
    try:
        no = _perguntar_ao_hub()
        no['origem'] = 'hub'
        erro = None
    except (urllib.error.URLError, OSError, ValueError, LookupError) as ex:
        no, erro = None, f'{type(ex).__name__}: {ex}'
    with _trava:
        _cache.update(no=no, erro=erro, em=time.time())
    return no


def info():
    """O que o site conta para a pagina: de onde vieram os dados e quem esta treinando."""
    with _trava:
        no, erro, em = _cache['no'], _cache['erro'], _cache['em']
    if PAINEL_URL and not no:
        no = {'url': PAINEL_URL, 'node_id': 'manual', 'origem': 'PAINEL_URL'}
    no = no or {}
    return {'hub': None if PAINEL_URL else HUB_URL, 'painel': no.get('url'), 'node_id': no.get('node_id'),
            'host': no.get('host'), 'nome': no.get('nome'), 'origem': no.get('origem'),
            'visto_ha_s': no.get('age_seconds'),
            'checado_ha_s': round(time.time() - em, 1) if em else None, 'erro': erro}


def status():
    """(codigo, dados) do /api/status do painel. Se o painel conhecido nao responde, repergunta ao
    hub e tenta de novo: o endereco do tunel muda toda vez que o PC religa."""
    headers = {'X-Painel-Token': PAINEL_TOKEN} if PAINEL_TOKEN else {}
    ultimo_erro = None
    for tentativa in range(2):
        no = descobrir(forcar=bool(tentativa))
        if not no:
            break
        try:
            dados = pedir(no['url'] + '/api/status', headers, timeout=ESPERA_PAINEL_S)
            dados['conexao'] = info()      # a pagina mostra de qual PC vieram os numeros
            return 200, dados
        except (urllib.error.URLError, OSError, ValueError) as ex:
            ultimo_erro = f'{type(ex).__name__}: {ex}'
    if ultimo_erro:
        return 502, {'falha': f'O painel do PC nao respondeu ({ultimo_erro}).', 'conexao': info()}
    return 503, {'falha': 'Nenhum PC esta com o painel de treino publicado.', 'conexao': info()}


def responder(handler, codigo, dados):
    """Escreve um JSON na resposta de um BaseHTTPRequestHandler (Vercel e Docker usam o mesmo)."""
    corpo = json.dumps(dados, ensure_ascii=False).encode('utf-8')
    handler.send_response(codigo)
    handler.send_header('Content-Type', JSON)
    handler.send_header('Content-Length', str(len(corpo)))
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    if getattr(handler, 'command', 'GET') != 'HEAD':
        handler.wfile.write(corpo)
