"""
users.py - Contas, sessoes e tokens de camera (PostgreSQL)

Antes cada usuario tinha um SQLite proprio (dados/users/<uid>/user.db) mais um
index.db global. Agora tudo vive no container do banco; a interface publica
deste modulo continua igual, entao o resto do backend nao mudou.

Uma diferenca de comportamento: a sessao passa a viver na tabela sessoes, e nao
num unico campo token por usuario. Na pratica, entrar no painel do computador
nao derruba mais a sessao do celular.
"""
import hashlib
import json
import re
import threading
import time
import uuid
from typing import Optional

import db

_lock = threading.Lock()


def _hash(senha: str) -> str:
    return hashlib.sha256(str(senha).encode()).hexdigest()


def _norm(v: str) -> str:
    return re.sub(r'\s+', '', str(v or '')).strip().lower()


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

def register(nome, doc, telefone, email, setor, senha) -> tuple:
    doc_n, email_n = _norm(doc), _norm(email)
    if len(senha or '') < 6:
        return False, 'Senha deve ter ao menos 6 caracteres'
    with _lock:
        existe = db.consultar_um('SELECT uid FROM usuarios WHERE doc=%s OR email=%s', (doc_n, email_n))
        if existe:
            return False, 'CPF/CNPJ ou e-mail já cadastrado'
        uid = str(uuid.uuid4())
        agora = time.time()
        db.executar(
            'INSERT INTO usuarios (uid,nome,doc,telefone,email,setor,senha_hash,role,blocked,restricoes,criado_em)'
            " VALUES (%s,%s,%s,%s,%s,%s,%s,'user',FALSE,'[]'::jsonb,%s)",
            (uid, nome, doc_n, telefone or '', email_n, setor or '', _hash(senha), agora))
        _evento(uid, 'register', 'Cadastro realizado')
        return True, uid


def login(credencial: str, senha: str) -> tuple:
    cred = _norm(credencial)
    with _lock:
        u = db.consultar_um('SELECT * FROM usuarios WHERE doc=%s OR email=%s', (cred, cred))
        if not u or u['blocked'] or u['senha_hash'] != _hash(senha):
            return False, None, None
        token = str(uuid.uuid4())
        agora = time.time()
        db.executar('INSERT INTO sessoes (token, uid, criado_em, visto_em) VALUES (%s,%s,%s,%s)',
                    (token, u['uid'], agora, agora))
        _evento(u['uid'], 'login', 'Login bem-sucedido')
        return True, token, _perfil(u)


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
    linha = db.consultar_um(
        'SELECT u.* FROM sessoes s JOIN usuarios u ON u.uid = s.uid WHERE s.token=%s', (token,))
    if not linha:
        return None
    try:   # marca atividade, sem travar o pedido se falhar
        db.executar('UPDATE sessoes SET visto_em=%s WHERE token=%s', (time.time(), token))
    except Exception:
        pass
    return _perfil(linha)


def get_user(uid: str) -> Optional[dict]:
    return _perfil(db.consultar_um('SELECT * FROM usuarios WHERE uid=%s', (uid,)))


def get_all_users() -> list:
    return [_perfil(l) for l in db.consultar('SELECT * FROM usuarios ORDER BY criado_em')]


def block_user(uid: str, block: bool = True) -> bool:
    try:
        db.executar('UPDATE usuarios SET blocked=%s WHERE uid=%s', (bool(block), uid))
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

def create_cam_token(uid: str, nome: str) -> Optional[dict]:
    token = 'cam_' + uuid.uuid4().hex[:16]
    agora = time.time()
    try:
        db.executar('INSERT INTO cam_tokens (token, uid, nome, criado_em) VALUES (%s,%s,%s,%s)',
                    (token, uid, nome, agora))
        return {'token': token, 'nome': nome, 'created_at': agora}
    except Exception:
        return None


def list_cam_tokens(uid: str) -> list:
    try:
        return [{'token': l['token'], 'nome': l['nome'], 'created_at': l['criado_em']}
                for l in db.consultar(
                    'SELECT token, nome, criado_em FROM cam_tokens WHERE uid=%s ORDER BY criado_em DESC', (uid,))]
    except Exception:
        return []


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
