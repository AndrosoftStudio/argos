"""
app.py - Site que acompanha o treinamento do Argos EPI de qualquer lugar (versao Docker).

Serve a pagina (public/) e repassa /api/status para o PC que esta treinando. Quem acha esse PC
e o api/_comum.py - o mesmo modulo que as funcoes da Vercel usam - perguntando ao hub, o mesmo
servico de discovery que o frontend do Argos usa para achar o backend de inferencia.

    navegador  ->  este site  ->  hub (/training/best)  ->  URL publica do painel no PC
                        \\________ /api/status _________/

Quem publica o painel e o proprio PC:
    python treinamento/acompanhar_treinamento/servidor.py --publico

O repasse acontece no servidor, entao o navegador so fala com este site: nada de CORS,
de conteudo misto ou de colar URL de tunel na mao.

As variaveis de ambiente estao documentadas em api/_comum.py; aqui so entra PORT (padrao 8080).
"""
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
ESTATICOS_DIR = os.path.join(AQUI, 'public')
sys.path.insert(0, os.path.join(AQUI, 'api'))
import _comum  # noqa: E402

PORTA = int(os.environ.get('PORT', '8080'))
ESTATICOS = {'/': 'index.html', '/index.html': 'index.html', '/logo.png': 'logo.png', '/favicon.ico': 'favicon.ico'}
TIPOS = {'.html': 'text/html; charset=utf-8', '.png': 'image/png', '.ico': 'image/x-icon',
         '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8'}


class Site(BaseHTTPRequestHandler):
    server_version = 'ArgosTreinamento'

    def _enviar(self, codigo, corpo, tipo):
        self.send_response(codigo)
        self.send_header('Content-Type', tipo)
        self.send_header('Content-Length', str(len(corpo)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(corpo)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        rota = urllib.parse.urlsplit(self.path).path
        if rota == '/health':
            return _comum.responder(self, 200, {'ok': True, 'hub': _comum.HUB_URL,
                                                'painel': _comum.info().get('painel')})
        if rota == '/api/conexao':
            _comum.descobrir()
            return _comum.responder(self, 200, _comum.info())
        if rota == '/api/status':
            codigo, dados = _comum.status()
            return _comum.responder(self, codigo, dados)
        arquivo = ESTATICOS.get(rota)
        if arquivo:
            try:
                with open(os.path.join(ESTATICOS_DIR, arquivo), 'rb') as f:
                    return self._enviar(200, f.read(), TIPOS[os.path.splitext(arquivo)[1]])
            except OSError:
                pass
        self._enviar(404, b'nao encontrado', 'text/plain; charset=utf-8')

    def log_message(self, formato, *args):   # uma linha por pedido, sem o ruido padrao
        print(f'{self.address_string()} {formato % args}', flush=True)


def vigiar():
    """Fora da Vercel da para manter a descoberta em dia sem esperar um pedido do navegador."""
    while True:
        try:
            _comum.descobrir(forcar=True)
        except Exception as ex:
            print(f'[descoberta] {type(ex).__name__}: {ex}', flush=True)
        time.sleep(max(5.0, _comum.DESCOBERTA_S))


def main():
    threading.Thread(target=vigiar, daemon=True, name='descoberta').start()
    servidor = ThreadingHTTPServer(('0.0.0.0', PORTA), Site)
    servidor.daemon_threads = True
    print(f'Site do treinamento em http://0.0.0.0:{PORTA}', flush=True)
    print(f'Painel fixo: {_comum.PAINEL_URL}' if _comum.PAINEL_URL else f'Hub: {_comum.HUB_URL}', flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
