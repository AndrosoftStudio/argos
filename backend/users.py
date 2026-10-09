"""
users.py - Contas, sessoes e tokens de camera (PostgreSQL)

Antes cada usuario tinha um SQLite proprio (dados/users/<uid>/user.db) mais um
index.db global. Agora tudo vive no container do banco; a interface publica
deste modulo continua igual, entao o resto do backend nao mudou.

Uma diferenca de comportamento: a sessao passa a viver na tabela sessoes, e nao
num unico campo token por usuario. Na pratica, entrar no painel do computador
nao derruba mais a sessao do celular.
"""
import json
import os
import re
import threading
import time
import uuid
from typing import Optional

import db
import seguranca

_lock = threading.Lock()

# Sessao parada por mais que isso precisa de login de novo. Antes o token valia
# para sempre: um celular perdido continuava com acesso ao painel.
SESSAO_DIAS = float(os.environ.get('ARGOS_SESSAO_DIAS', '30'))
# visto_em so e regravado de tempos em tempos, nao a cada pedido do painel
_MARCAR_ATIVIDADE_S = 300


def _norm(v: str) -> str:
    return re.sub(r'\s+', '', str(v or '')).strip().lower()


def _norm_doc(doc: str) -> str:
    """CPF/CNPJ guardado so com digitos: "123.456.789-00" e "12345678900" sao a mesma conta."""
    return seguranca.so_digitos(doc) or _norm(doc)


def _perfil(linha) -> Optional[dict]:
    if not linha:
        return None
    restr = linha.get('restricoes')
    if isinstance(restr, str):
        try:
            restr = json.loads(restr or '[]')
        except ValueError:
            restr = []
    return {
        'id': linha['uid'], 'uid': linha['uid'], 'nome': linha['nome'], 'doc': linha['doc'],
        'telefone': linha.get('telefone') or '', 'email': linha['email'],
        'setor': linha.get('setor') or '', 'role': linha.get('role') or 'user',
        'blocked': bool(linha.get('blocked')), 'restricoes': restr or [],
        'created_at': linha.get('criado_em'),
    }


def _evento(uid: str, tipo: str, descricao: str):
    try:
        db.executar('INSERT INTO eventos_conta (uid, ts, tipo, descricao) VALUES (%s,%s,%s,%s)',
                    (uid, time.time(), tipo, descricao))
    except Exception:
        pass


# ── Contas ──────────────────────────────────────────────────────────

# contas antigas guardaram o documento com pontos e traco: a comparacao ignora a formatacao
_BUSCA_CONTA = ("SELECT * FROM usuarios WHERE email=%s OR doc=%s"
                " OR (%s <> '' AND regexp_replace(doc, '[^0-9]', '', 'g') = %s) LIMIT 1")


def register(nome, doc, telefone, email, setor, senha) -> tuple:
    doc_n, email_n = _norm_doc(doc), _norm(email)
    if not str(nome or '').strip():
        return False, 'Informe o nome completo'
    if not seguranca.documento_valido(doc):
        return False, 'CPF precisa ter 11 números (ou CNPJ, 14)'
    if not seguranca.email_valido(email_n):
        return False, 'E-mail inválido'
    if len(senha or '') < 6:
        return False, 'A senha precisa ter pelo menos 6 caracteres'
    digitos = seguranca.so_digitos(doc)
    with _lock:
        existe = db.consultar_um(_BUSCA_CONTA, (email_n, doc_n, digitos, digitos))
        if existe:
            return False, 'Já existe uma conta com este CPF/CNPJ ou e-mail'
        uid = str(uuid.uuid4())
        agora = time.time()
        db.executar(
            'INSERT INTO usuarios (uid,nome,doc,telefone,email,setor,senha_hash,role,blocked,restricoes,criado_em)'
            " VALUES (%s,%s,%s,%s,%s,%s,%s,'user',FALSE,'[]'::jsonb,%s)",
            (uid, str(nome).strip(), doc_n, telefone or '', email_n, setor or '',
             seguranca.hash_senha(senha), agora))
        _evento(uid, 'register', 'Cadastro realizado')
        return True, uid


_hash_ficticio = None


def _gastar_o_mesmo_tempo(senha):
    """Conta inexistente leva o mesmo tempo que senha errada: senao a demora
    denunciaria quais CPFs e e-mails tem cadastro."""
    global _hash_ficticio
    if _hash_ficticio is None:
        _hash_ficticio = seguranca.hash_senha(uuid.uuid4().hex)
    seguranca.conferir_senha(senha, _hash_ficticio)
    return False, False


def login(credencial: str, senha: str) -> tuple:
    """(ok, token, perfil, motivo). motivo: 'credenciais' ou 'bloqueado'."""
    cred = _norm(credencial)
    digitos = seguranca.so_digitos(credencial) if '@' not in cred else ''
    u = db.consultar_um(_BUSCA_CONTA, (cred, cred, digitos, digitos))
    ok, regravar = seguranca.conferir_senha(senha, u['senha_hash']) if u else _gastar_o_mesmo_tempo(senha)
    if not ok:
        return False, None, None, 'credenciais'
    # a senha confere antes de dizer que a conta esta bloqueada: assim ninguem
    # descobre quais contas existem testando CPFs
    if u['blocked']:
        return False, None, None, 'bloqueado'
    token = str(uuid.uuid4())
    agora = time.time()
    with _lock:
        if regravar:  # conta antiga (SHA-256 sem sal): passa para o formato novo agora
            db.executar('UPDATE usuarios SET senha_hash=%s WHERE uid=%s',
                        (seguranca.hash_senha(senha), u['uid']))
        db.executar('INSERT INTO sessoes (token, uid, criado_em, visto_em) VALUES (%s,%s,%s,%s)',
                    (token, u['uid'], agora, agora))
    _evento(u['uid'], 'login', 'Login bem-sucedido')
    return True, token, _perfil(u), ''


def logout(token: str):
    if not token:
        return
    try:
        db.executar('DELETE FROM sessoes WHERE token=%s', (token,))
    except Exception:
        pass


def get_user_by_token(token: str) -> Optional[dict]:
    if not token:
        return None
    agora = time.time()
    linha = db.consultar_um(
        'SELECT u.*, s.visto_em AS sessao_visto_em FROM sessoes s JOIN usuarios u ON u.uid = s.uid'
        ' WHERE s.token=%s AND s.visto_em >= %s AND NOT u.blocked',
        (token, agora - SESSAO_DIAS * 86400))
    if not linha:
        return None
    if agora - float(linha.get('sessao_visto_em') or 0) > _MARCAR_ATIVIDADE_S:
        try:   # marca atividade, sem travar o pedido se falhar
            db.executar('UPDATE sessoes SET visto_em=%s WHERE token=%s', (agora, token))
        except Exception:
            pass
    return _perfil(linha)


def limpar_sessoes_vencidas() -> int:
    """Apaga sessoes paradas ha mais de SESSAO_DIAS (chamado pela manutencao)."""
    try:
        return db.executar('DELETE FROM sessoes WHERE visto_em < %s', (time.time() - SESSAO_DIAS * 86400,))
    except Exception:
        return 0


def get_user(uid: str) -> Optional[dict]:
    return _perfil(db.consultar_um('SELECT * FROM usuarios WHERE uid=%s', (uid,)))


def get_all_users() -> list:
    return [_perfil(l) for l in db.consultar('SELECT * FROM usuarios ORDER BY criado_em')]


def block_user(uid: str, block: bool = True) -> bool:
    try:
        db.executar('UPDATE usuarios SET blocked=%s WHERE uid=%s', (bool(block), uid))
        if block:  # bloqueio vale na hora: derruba as sessoes abertas
            db.executar('DELETE FROM sessoes WHERE uid=%s', (uid,))
        _evento(uid, 'block' if block else 'unblock', 'Bloqueado' if block else 'Desbloqueado')
        return True
    except Exception:
        return False


def set_restrictions(uid: str, restricoes: list) -> bool:
    try:
        db.executar('UPDATE usuarios SET restricoes=%s::jsonb WHERE uid=%s',
                    (json.dumps(restricoes or []), uid))
        return True
    except Exception:
        return False


# ── Tokens de camera (celular usado como camera) ────────────────────

def create_cam_token(uid: str, nome: str, tipo: str = 'camera') -> Optional[dict]:
    """Link de dispositivo: 'camera' (celular que manda video, token cam_) ou 'tv' (tela que toca
    a sirene e fala os avisos, token tv_). O token de TV nao serve para mais nada."""
    tipo = 'tv' if tipo == 'tv' else 'camera'
    token = ('tv_' if tipo == 'tv' else 'cam_') + uuid.uuid4().hex[:16]
    agora = time.time()
    try:
        db.executar('INSERT INTO cam_tokens (token, uid, nome, criado_em, tipo) VALUES (%s,%s,%s,%s,%s)',
                    (token, uid, nome, agora, tipo))
        return {'token': token, 'nome': nome, 'created_at': agora, 'tipo': tipo}
    except Exception:
        return None


def list_cam_tokens(uid: str, tipo: str = None) -> list:
    try:
        linhas = [{'token': l['token'], 'nome': l['nome'], 'created_at': l['criado_em'],
                   'tipo': 'tv' if str(l['token']).startswith('tv_') else 'camera'}
                  for l in db.consultar(
                      'SELECT token, nome, criado_em FROM cam_tokens WHERE uid=%s ORDER BY criado_em DESC', (uid,))]
        return [l for l in linhas if not tipo or l['tipo'] == tipo]
    except Exception:
        return []


def rename_cam_token(uid: str, token: str, nome: str) -> bool:
    try:
        return db.executar('UPDATE cam_tokens SET nome=%s WHERE uid=%s AND token=%s', (nome, uid, token)) > 0
    except Exception:
        return False


def delete_cam_token(uid: str, token: str) -> bool:
    try:
        db.executar('DELETE FROM cam_tokens WHERE uid=%s AND token=%s', (uid, token))
        return True
    except Exception:
        return False


def validate_cam_token(token: str) -> Optional[dict]:
    """Antes isso varria o SQLite de todos os usuarios; agora e uma consulta."""
    if not token:
        return None
    l = db.consultar_um('SELECT uid, token, nome FROM cam_tokens WHERE token=%s', (token,))
    return {'uid': l['uid'], 'token': l['token'], 'nome': l['nome']} if l else None


# ── Contas do site (Supabase) ───────────────────────────────────────

_espelhadas = {}


def espelhar_conta(uid: str, nome: str = '', email: str = '', doc: str = '', role: str = 'user') -> Optional[dict]:
    """Garante a conta do site na tabela usuarios deste servidor.

    As contas agora vivem no site; aqui fica so uma copia, porque as tabelas do
    servidor (EPIs, funcionarios, cameras...) apontam para usuarios(uid). Uma
    conta antiga daqui com o mesmo e-mail/CPF mas outro uid tem os campos
    renomeados, para nao travar o cadastro (os dados dela continuam no banco)."""
    uid = str(uid or '').strip()
    if not uid:
        return None
    email_n = _norm(email) or f'conta:{uid}'
    doc_n = _norm_doc(doc) if doc else ''
    doc_n = doc_n or f'conta:{uid}'
    role = role if role in ('user', 'admin') else 'user'
    chave = (nome, email_n, doc_n, role)
    if _espelhadas.get(uid) == chave:
        return get_user(uid)
    with _lock:
        for l in db.consultar('SELECT uid, email, doc FROM usuarios WHERE uid<>%s AND (email=%s OR doc=%s)',
                              (uid, email_n, doc_n)):
            db.executar('UPDATE usuarios SET email=%s, doc=%s WHERE uid=%s',
                        (f"legado:{l['uid']}:{l['email']}", f"legado:{l['uid']}:{l['doc']}", l['uid']))
        db.executar(
            'INSERT INTO usuarios (uid,nome,doc,telefone,email,setor,senha_hash,role,blocked,restricoes,criado_em)'
            " VALUES (%s,%s,%s,'',%s,'','',%s,FALSE,'[]'::jsonb,%s)"
            ' ON CONFLICT (uid) DO UPDATE SET nome=EXCLUDED.nome, email=EXCLUDED.email, doc=EXCLUDED.doc,'
            ' role=EXCLUDED.role, blocked=FALSE',
            (uid, str(nome or '').strip() or email_n, doc_n, email_n, role, time.time()))
        _espelhadas[uid] = chave
    return get_user(uid)
