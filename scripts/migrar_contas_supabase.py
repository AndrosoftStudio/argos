"""
migrar_contas_supabase.py - Leva as contas do banco local para o Supabase.

As contas agora ficam no site (Supabase). Esta migracao copia cada conta da
tabela usuarios deste servidor para a tabela contas do Supabase MANTENDO O
MESMO ID: assim, depois de vincular o servidor, os EPIs, a equipe e o historico
de cada conta continuam sendo dela. A senha vai como esta (hash), entao cada um
continua entrando com a senha de sempre.

Pode rodar mais de uma vez: conta que ja existe no Supabase (mesmo id ou mesmo
e-mail) e pulada. CPF/CNPJ que ja esta em outra conta vai sem documento.

Uso (a secret key NUNCA vai para arquivo; so na variavel de ambiente):
    set SUPABASE_URL=https://<projeto>.supabase.co
    set SUPABASE_SECRET_KEY=sb_secret_...
    set DATABASE_URL=postgresql://argos:argos@127.0.0.1:5432/argosepi   (opcional)
    python scripts\\migrar_contas_supabase.py            -> mostra o que faria
    python scripts\\migrar_contas_supabase.py --aplicar  -> migra
"""
import datetime
import os
import re
import sys

import psycopg
import requests
from psycopg.rows import dict_row

SB_URL = re.sub(r'/rest/v1/?$', '', os.environ.get('SUPABASE_URL', '').strip().rstrip('/'))
SB_KEY = os.environ.get('SUPABASE_SECRET_KEY', '').strip()
DB_URL = os.environ.get('DATABASE_URL', '').strip() or 'postgresql://argos:argos@127.0.0.1:5432/argosepi'
APLICAR = '--aplicar' in sys.argv
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


def sb(metodo, caminho, **kw):
    h = {'apikey': SB_KEY, 'Content-Type': 'application/json'}
    h.update(kw.pop('headers', {}))
    r = requests.request(metodo, f'{SB_URL}/rest/v1/{caminho}', headers=h, timeout=30, **kw)
    if not r.ok:
        raise RuntimeError(f'Supabase {r.status_code}: {r.text[:300]}')
    return r.json() if r.text else None


def main():
    if not SB_URL or not SB_KEY:
        sys.exit('Defina SUPABASE_URL e SUPABASE_SECRET_KEY (variaveis de ambiente).')
    with psycopg.connect(DB_URL, row_factory=dict_row) as c:
        locais = c.execute('SELECT * FROM usuarios ORDER BY criado_em').fetchall()
    remotas = sb('GET', 'contas?select=id,email,doc') or []
    ids = {r['id'] for r in remotas}
    emails = {(r['email'] or '').lower() for r in remotas}
    docs = {r['doc'] for r in remotas if r['doc']}

    novas = []
    for u in locais:
        uid, email = u['uid'], (u['email'] or '').strip().lower()
        if not UUID.match(uid) or email.startswith(('conta:', 'legado:')):
            continue                      # copia de conta do site ou conta renomeada: nao migra
        if uid in ids or email in emails:
            print(f'  ja existe : {u["nome"]} <{email}>')
            continue
        doc = re.sub(r'\D', '', u['doc'] or '') or None
        if doc in docs:
            print(f'  aviso     : CPF/CNPJ de {u["nome"]} ja esta em outra conta; vai sem documento')
            doc = None
        if doc:
            docs.add(doc)
        emails.add(email)
        criado = datetime.datetime.fromtimestamp(float(u['criado_em'] or 0), datetime.timezone.utc).isoformat()
        novas.append({'id': uid, 'nome': u['nome'] or email, 'doc': doc, 'telefone': u.get('telefone') or '',
                      'email': email, 'setor': u.get('setor') or '', 'senha_hash': u['senha_hash'] or None,
                      'role': u.get('role') or 'user', 'bloqueada': bool(u.get('blocked')), 'criado_em': criado})
        print(f'  migrar    : {u["nome"]} <{email}>')

    if not novas:
        print('Nada para migrar.')
        return
    if not APLICAR:
        print(f'\n{len(novas)} conta(s) seriam migradas. Rode de novo com --aplicar para migrar.')
        return
    sb('POST', 'contas', json=novas, headers={'Prefer': 'return=minimal'})
    print(f'\n{len(novas)} conta(s) migradas para o Supabase com o mesmo id.')


if __name__ == '__main__':
    main()
