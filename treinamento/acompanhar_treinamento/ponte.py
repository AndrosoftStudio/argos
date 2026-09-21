"""
ponte.py - Deixa o painel de treino visivel na internet e avisa o hub onde ele esta.

Abre um tunel do Cloudflare para a porta do painel e registra a URL publica no hub
(o mesmo servico que o frontend do Argos usa para achar o backend de inferencia).
O site em https://github.com/AndrosoftStudio/treinamentoargosepi pergunta ao hub
qual e o PC que esta treinando e mostra o painel, sem ninguem digitar endereco nenhum.

So e usado quando o painel roda com --publico. Sem isso, o painel continua local.
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
import comum  # noqa: E402

def _env_projeto():
    """Le o v19/.env, onde o backend ja guarda BACKEND_HUB_URL e HUB_API_KEY."""
    valores = {}
    try:
        with open(os.path.join(comum.RAIZ_PROJETO, '.env'), encoding='utf-8') as f:
            for linha in f:
                linha = linha.strip()
                if linha and not linha.startswith('#') and '=' in linha:
                    chave, valor = linha.split('=', 1)
                    valores[chave.strip()] = valor.strip().strip('"').strip("'")
    except OSError:
        pass
    return valores


ENV = _env_projeto()
HUB_PADRAO = (os.environ.get('ARGOS_HUB_URL') or ENV.get('BACKEND_HUB_URL')
              or 'https://backend-hub-vigilanciaepi.onrender.com').strip().rstrip('/')
# o hub exige chave para escrever (registro e heartbeat); so leitura e aberta
CHAVE_PADRAO = (os.environ.get('HUB_API_KEY') or ENV.get('HUB_API_KEY') or '').strip()
INTERVALO_S = 30            # o hub esquece um painel depois de 180 s sem noticias
RE_TUNEL = re.compile(r'https://[a-z0-9\-]+\.trycloudflare\.com')


def _log(msg):
    # o painel roda com a saida redirecionada para um .log; sem flush, nada aparece la
    print(msg, flush=True)


def _executavel_cloudflared():
    """O cloudflared.exe ja vem no projeto (mesmo binario que o backend usa)."""
    candidatos = [os.path.join(comum.RAIZ_PROJETO, 'cloudflared.exe'),
                  os.path.join(comum.RAIZ_PROJETO, 'cloudflared'), 'cloudflared']
    for c in candidatos:
        try:
            if subprocess.run([c, '--version'], capture_output=True, timeout=5).returncode == 0:
                return c
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def node_id_padrao(nome_treino):
    return f"{socket.gethostname().lower()}-{nome_treino}"


class Ponte:
    """Tunel + heartbeat. Todo erro e tratado: o painel local nunca pode cair por causa disto."""

    def __init__(self, porta, nome, hub=HUB_PADRAO, node_id=None, token='', url_publica='',
                 chave_hub='', resumo=None, log=_log):
        self.porta = porta
        self.nome = nome
        self.hub = (hub or '').strip().rstrip('/')
        self.node_id = node_id or node_id_padrao(nome)
        self.token = token or ''
        self.url = (url_publica or '').strip().rstrip('/')
        self.chave_hub = chave_hub or ''
        self.resumo = resumo or (lambda: {})
        self.log = log
        self.registrado = False
        self._parar = threading.Event()
        self._proc = None

    # ── tunel ─────────────────────────────────────────────────────
    def _abrir_tunel(self):
        cmd = _executavel_cloudflared()
        if not cmd:
            self.log('[ponte] cloudflared nao encontrado; rode com --url-publica se ja tiver um endereco')
            return
        self.log(f'[ponte] abrindo tunel para a porta {self.porta}...')
        try:
            self._proc = subprocess.Popen([cmd, 'tunnel', '--url', f'http://localhost:{self.porta}'],
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                          encoding='utf-8', errors='replace', bufsize=1,
                                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as ex:
            self.log(f'[ponte] falha ao iniciar o cloudflared: {ex}')
            return
        for linha in iter(self._proc.stdout.readline, ''):
            if self._parar.is_set():
                break
            m = RE_TUNEL.search(linha)
            if m and not self.url:
                self.url = m.group(0)
                self.log(f'[ponte] endereco publico: {self.url}')
                self._enviar('/training/register')
        # o tunel caiu: sem URL, o hub esquece este painel sozinho
        if not self._parar.is_set():
            self.log('[ponte] o tunel do Cloudflare fechou')

    # ── hub ───────────────────────────────────────────────────────
    def _pedir(self, caminho, dados=None, metodo='POST'):
        req = urllib.request.Request(self.hub + caminho, method=metodo,
                                     data=json.dumps(dados).encode('utf-8') if dados is not None else None)
        req.add_header('Content-Type', 'application/json')
        if self.chave_hub:
            req.add_header('Authorization', 'Bearer ' + self.chave_hub)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode('utf-8') or '{}')

    def _payload(self):
        try:
            resumo = self.resumo() or {}
        except Exception:
            resumo = {}
        return {
            'node_id': self.node_id,
            'url': self.url,
            'host': socket.gethostname(),
            'porta': self.porta,
            'nome': self.nome,
            'protegido': bool(self.token),
            'version': 'painel-1',
            **resumo,
        }

    def _enviar(self, caminho):
        if not (self.hub and self.url):
            return False
        try:
            self._pedir(caminho, self._payload())
            if not self.registrado:
                self.log(f'[ponte] registrado no hub: {self.hub}')
            self.registrado = True
            return True
        except (urllib.error.URLError, OSError, ValueError) as ex:
            self.registrado = False
            self.log(f'[ponte] falha ao falar com o hub: {ex}')
            return False

    def _loop(self):
        while not self._parar.wait(INTERVALO_S):
            self._enviar('/training/heartbeat' if self.registrado else '/training/register')

    # ── ciclo de vida ─────────────────────────────────────────────
    def iniciar(self):
        threading.Thread(target=self._abrir_tunel, daemon=True, name='ponte-tunel').start()
        threading.Thread(target=self._loop, daemon=True, name='ponte-hub').start()
        if self.url:  # URL fixa passada na linha de comando: nem espera o tunel
            self._enviar('/training/register')

    def parar(self):
        """Some da lista do hub na hora, em vez de deixar o site tentando um painel fechado."""
        self._parar.set()
        if self.registrado:
            try:
                self._pedir(f'/training/{self.node_id}', metodo='DELETE')
            except (urllib.error.URLError, OSError, ValueError):
                pass
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except OSError:
                pass
