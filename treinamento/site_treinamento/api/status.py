"""Funcao /api/status da Vercel: repassa o status do PC que esta treinando."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _comum  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        codigo, dados = _comum.status()
        _comum.responder(self, codigo, dados)

    def do_HEAD(self):
        self.do_GET()

    def log_message(self, formato, *args):
        print(formato % args, flush=True)
