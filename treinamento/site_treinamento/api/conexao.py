"""Funcao /api/conexao da Vercel: so a parte da descoberta (hub, painel, ultimo erro)."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _comum  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        _comum.descobrir()
        _comum.responder(self, 200, _comum.info())

    def do_HEAD(self):
        self.do_GET()

    def log_message(self, formato, *args):
        print(formato % args, flush=True)
