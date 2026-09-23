"""
seguranca.py - Regras de seguranca compartilhadas pelo backend.

Fica num modulo proprio, sem torch, OpenCV nem banco, para poder ser testado
sozinho (ver tests/test_seguranca.py) e para as regras nao se espalharem pelas
rotas: senha, nome de arquivo enviado, id vindo da URL e dono de cada camera.
"""
import hashlib
import hmac
import os
import re
import secrets
import threading
import time

# ── Senhas ──────────────────────────────────────────────────────────
# PBKDF2-SHA256 com sal aleatorio. Antes era SHA-256 puro, sem sal: a mesma
# senha dava sempre o mesmo hash e uma tabela pronta quebrava o banco inteiro.
# 600 mil iteracoes e o minimo recomendado pela OWASP para PBKDF2-SHA256.
ITERACOES = int(os.environ.get('ARGOS_PBKDF2_ITERACOES', '600000'))
_PREFIXO = 'pbkdf2_sha256'


def hash_senha(senha: str) -> str:
    sal = secrets.token_bytes(16)
    chave = hashlib.pbkdf2_hmac('sha256', str(senha).encode(), sal, ITERACOES)
    return f'{_PREFIXO}${ITERACOES}${sal.hex()}${chave.hex()}'


def conferir_senha(senha: str, guardado: str):
    """(confere, precisa_regravar). Aceita o formato antigo (SHA-256 puro) para
    quem ja tinha conta: no primeiro login certo o hash e regravado no novo."""
    guardado = str(guardado or '')
    if guardado.startswith(_PREFIXO + '$'):
        try:
            _, iteracoes, sal, chave = guardado.split('$')
            iteracoes = int(iteracoes)
            calculado = hashlib.pbkdf2_hmac('sha256', str(senha).encode(), bytes.fromhex(sal), iteracoes)
        except (ValueError, TypeError):
            return False, False
        ok = hmac.compare_digest(calculado.hex(), chave)
        return ok, ok and iteracoes < ITERACOES
    antigo = hashlib.sha256(str(senha).encode()).hexdigest()
    ok = hmac.compare_digest(antigo, guardado)
    return ok, ok


# ── Documento e e-mail ──────────────────────────────────────────────
def so_digitos(valor) -> str:
    return re.sub(r'\D', '', str(valor or ''))


def documento_valido(doc) -> bool:
    """CPF tem 11 digitos, CNPJ 14. Pontos, barras e tracos sao ignorados."""
    return len(so_digitos(doc)) in (11, 14)


def email_valido(email) -> bool:
    return bool(re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', str(email or '').strip()))


# ── Ids e arquivos vindos do cliente ────────────────────────────────
_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_SID_RE = re.compile(r'^[A-Za-z0-9_-]{1,96}$')
EXTENSOES_FOTO = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
EXTENSOES_VIDEO = ('.webm', '.mp4', '.mov', '.mkv', '.avi', '.m4v', '.3gp')


def id_valido(valor) -> bool:
    """Id que vira nome de pasta (EPI, funcionario, foto). Sem '.', '/' ou '\\':
    um '..' vindo da URL apagaria a pasta de dados inteira da conta."""
    return bool(_ID_RE.match(str(valor or '')))


def extensao_permitida(nome_arquivo, permitidas, padrao) -> str:
    """Extensao do arquivo enviado, se estiver na lista; senao a padrao.
    Sem isso uma "foto" .html seria servida como pagina pelo proprio backend."""
    ext = os.path.splitext(str(nome_arquivo or ''))[1].lower()
    return ext if ext in permitidas else padrao


# ── Cameras (streams) ───────────────────────────────────────────────
def sid_valido(sid) -> bool:
    return bool(_SID_RE.match(str(sid or '')))


def sid_do_token_camera(token) -> str:
    """Mesmo calculo do cam.html (buildStreamId): o celular so fala com o proprio stream."""
    seguro = re.sub(r'[^a-zA-Z0-9_-]', '', str(token or ''))[:24]
    return 'remote_' + seguro if seguro else ''


def stream_da_conta(sid, uid, tokens_camera=()) -> bool:
    """O stream e desta conta? Os ids que o painel cria comecam com o uid; os do
    celular sao derivados de um token de camera da conta."""
    if not (sid_valido(sid) and uid):
        return False
    if sid.startswith(str(uid) + '_'):
        return True
    if sid.startswith('remote_'):
        return any(sid_do_token_camera(t) == sid for t in tokens_camera or ())
    return False


FONTES_REDE = ('rtsp://', 'rtsps://', 'rtmp://', 'http://', 'https://')


def fonte_camera_valida(fonte) -> bool:
    """Fonte que o painel pode pedir: camera do navegador/celular ('webcam'),
    camera ligada no proprio servidor (numero) ou endereco de rede. Caminho de
    arquivo do servidor fica de fora: com ele qualquer conta abria videos de outra."""
    s = str(fonte if fonte is not None else '').strip()
    if s == 'webcam' or s.isdigit():
        return True
    return s.lower().startswith(FONTES_REDE)


# ── Tentativas de login ─────────────────────────────────────────────
class LimiteTentativas:
    """Bloqueia por alguns minutos a credencial que errou a senha varias vezes seguidas.
    Chave por credencial (CPF/e-mail): atras do tunel todo mundo chega com o mesmo IP."""

    def __init__(self, maximo=8, janela_s=15 * 60):
        self.maximo, self.janela_s = maximo, janela_s
        self._falhas = {}
        self._lock = threading.Lock()

    def _recentes(self, chave, agora):
        return [t for t in self._falhas.get(chave, ()) if agora - t < self.janela_s]

    def bloqueado(self, chave) -> int:
        """Segundos que faltam para liberar (0 = liberado)."""
        agora = time.time()
        with self._lock:
            recentes = self._recentes(chave, agora)
            if len(recentes) < self.maximo:
                return 0
            return int(self.janela_s - (agora - recentes[0])) + 1

    def falhou(self, chave):
        agora = time.time()
        with self._lock:
            recentes = self._recentes(chave, agora)
            recentes.append(agora)
            self._falhas[chave] = recentes[-self.maximo:]
            if len(self._falhas) > 10000:  # nao deixa o dicionario crescer sem fim
                for k in [k for k, v in self._falhas.items() if not self._recentes(k, agora)]:
                    self._falhas.pop(k, None)

    def acertou(self, chave):
        with self._lock:
            self._falhas.pop(chave, None)
