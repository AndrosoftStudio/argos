"""Argos EPI Servidor - supervisor do programa com janela.

Roda escondido (sem console), aberto pela janela do programa (ArgosEPI.exe no
Windows, Electron no Linux). Cuida de tudo que o iniciar.bat fazia:

  - liga o PostgreSQL portatil (bin/pgsql, banco em dados/pgdata) numa porta livre;
  - confere os modelos de rosto (ja vem na instalacao; so baixa se alguem apagou);
  - liga o backend (run.py) numa porta livre de 8088 a 8097 e reinicia se cair;
  - serve a interface da janela (pasta ui) e uma API so para ela, em 127.0.0.1;
  - aplica "iniciar com o sistema" e confere se ha versao nova.

Quando a janela fecha de vez (ou morre), desliga o backend e o banco.
Uso: python supervisor.py --pai <pid da janela> --exe <caminho do programa>
No programa instalado este arquivo vai compilado dentro do motor (ver motor.py):
a interface sai do recursos.pak e o backend e o proprio motor com o papel "servidor".
"""
import argparse
import collections
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CONGELADO = bool(getattr(sys, 'frozen', False))
if CONGELADO:   # <instalacao>/motor/ArgosMotor.exe
    MOTOR = os.path.dirname(os.path.abspath(sys.executable))
    RAIZ = os.path.dirname(MOTOR)
    AQUI = RAIZ
    import pastas
    PAK = pastas.PAK
else:
    AQUI = os.path.dirname(os.path.abspath(__file__))
    RAIZ = os.path.dirname(AQUI)
    MOTOR = os.path.join(RAIZ, 'python')
    PAK = None
UI = os.path.join(AQUI, 'ui')
DADOS = os.path.join(RAIZ, 'dados')
LOGS = os.path.join(DADOS, 'logs')
CONFIG = os.path.join(DADOS, 'servidor-app.json')
WIN = os.name == 'nt'
SEM_JANELA = 0x08000000 if WIN else 0          # CREATE_NO_WINDOW
NOME_APP = 'Argos EPI Servidor'
SITE = 'https://argosepi.vercel.app'
DOWNLOADS_PADRAO = 'https://pub-6b5befc214654d93bdc0345875151ea0.r2.dev/argosepi-discovery/'
INSIGHTFACE_ZIP = 'https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip'
ROSTO_DIR = os.path.join(RAIZ, 'models', 'insightface', 'models', 'buffalo_l')
ROSTO_ARQS = ('det_10g.onnx', 'w600k_r50.onnx')

PADRAO = {'iniciar_com_sistema': False, 'iniciar_minimizado': True, 'ao_fechar': 'perguntar',
          'tema': 'sistema', 'avisar_atualizacao': True, 'rede_local': False}


def _ler(caminho, padrao=''):
    try:
        with open(caminho, encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return padrao


VERSAO = _ler(os.path.join(RAIZ, 'VERSAO.txt'), 'dev')
VARIANTE = _ler(os.path.join(MOTOR, 'argos-variante.txt'), '')
FONTE = (os.environ.get('ARGOS_DOWNLOADS_URL') or _ler(os.path.join(AQUI, 'fonte.txt')) or DOWNLOADS_PADRAO).rstrip('/') + '/'


# ── registro (o que aparece na aba "Registro" da janela) ─────────────
class Registro:
    ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')

    def __init__(self):
        self.linhas = collections.deque(maxlen=3000)
        self.n = 0
        self.lock = threading.Lock()
        os.makedirs(LOGS, exist_ok=True)
        self.arquivo = os.path.join(LOGS, 'servidor.log')
        try:   # guarda o anterior e comeca um novo a cada abertura
            if os.path.getsize(self.arquivo) > 0:
                os.replace(self.arquivo, self.arquivo + '.1')
        except OSError:
            pass

    def __call__(self, texto, origem='app'):
        texto = self.ANSI.sub('', str(texto)).rstrip()
        if not texto:
            return
        with self.lock:
            self.n += 1
            self.linhas.append((self.n, time.time(), origem, texto))
            try:
                with open(self.arquivo, 'a', encoding='utf-8') as f:
                    f.write(time.strftime('%H:%M:%S ') + f'[{origem}] {texto}\n')
            except OSError:
                pass

    def desde(self, n):
        with self.lock:
            return [list(l) for l in self.linhas if l[0] > n]


log = Registro()


def _rodar(cmd, timeout=120, **kw):
    """Comando curto escondido (sem janela de console no Windows)."""
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                          creationflags=SEM_JANELA, **kw)


def _porta_livre(p, host='127.0.0.1'):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        if s.connect_ex((host, p)) == 0:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('0.0.0.0' if host == '127.0.0.1' else host, p))
        except OSError:
            return False
    return True


def _abrir(alvo):
    try:
        if WIN:
            os.startfile(alvo)           # noqa: S606 - navegador/pasta padrao do Windows
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', alvo])
        else:
            subprocess.Popen(['xdg-open', alvo], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        webbrowser.open(alvo)


# ── configuracao do programa ─────────────────────────────────────────
class Config:
    def __init__(self, exe):
        self.exe = exe
        self.dados = dict(PADRAO)
        try:
            with open(CONFIG, encoding='utf-8') as f:
                self.dados.update({k: v for k, v in json.load(f).items() if k in PADRAO})
        except (OSError, ValueError):
            pass

    def salvar(self):
        os.makedirs(DADOS, exist_ok=True)
        tmp = CONFIG + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.dados, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG)

    def mudar(self, novos):
        for k, v in (novos or {}).items():
            if k not in PADRAO:
                continue
            if k == 'ao_fechar' and v not in ('perguntar', 'bandeja', 'sair'):
                continue
            if k == 'tema' and v not in ('sistema', 'claro', 'escuro'):
                continue
            self.dados[k] = v if k in ('ao_fechar', 'tema') else bool(v)
        self.salvar()
        self.aplicar_inicio()
        return dict(self.dados)

    # "iniciar com o sistema": Run do Windows / autostart do Linux
    def aplicar_inicio(self):
        ligado = self.dados['iniciar_com_sistema'] and self.exe
        cmd = f'"{self.exe}"' + (' --minimizado' if self.dados['iniciar_minimizado'] else '') if self.exe else ''
        try:
            if WIN:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run',
                                    0, winreg.KEY_SET_VALUE) as k:
                    if ligado:
                        winreg.SetValueEx(k, NOME_APP, 0, winreg.REG_SZ, cmd)
                    else:
                        try:
                            winreg.DeleteValue(k, NOME_APP)
                        except FileNotFoundError:
                            pass
            else:
                pasta = os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'autostart')
                arq = os.path.join(pasta, 'argos-epi-servidor.desktop')
                if ligado:
                    os.makedirs(pasta, exist_ok=True)
                    icone = os.path.join(RAIZ, 'argos-epi-servidor.png') if CONGELADO else os.path.join(UI, 'icone-512.png')
                    with open(arq, 'w', encoding='utf-8') as f:
                        f.write('[Desktop Entry]\nType=Application\nName=Argos EPI Servidor\n'
                                f'Exec={cmd}\nIcon={icone}\nX-GNOME-Autostart-enabled=true\nTerminal=false\n')
                elif os.path.exists(arq):
                    os.remove(arq)
        except Exception as e:
            log(f'Nao consegui mudar o "iniciar com o sistema": {e}')


# ── banco (PostgreSQL portatil) ──────────────────────────────────────
class Banco:
    def __init__(self):
        exe = '.exe' if WIN else ''
        self.bin = os.path.join(RAIZ, 'bin', 'pgsql', 'bin')
        self.pg_ctl = os.path.join(self.bin, 'pg_ctl' + exe)
        self.initdb = os.path.join(self.bin, 'initdb' + exe)
        self.dados = os.path.join(DADOS, 'pgdata')
        self.arq_log = os.path.join(LOGS, 'postgres.log')
        self.estado, self.porta, self.erro = 'parado', None, ''
        self.liguei = False

    @property
    def existe(self):
        return os.path.exists(self.pg_ctl)

    def url(self):
        return f'postgresql://argos:argos@127.0.0.1:{self.porta}/argosepi'

    def _rodando(self):
        if not os.path.exists(os.path.join(self.dados, 'postmaster.pid')):
            return None
        r = _rodar([self.pg_ctl, '-D', self.dados, 'status'], timeout=30)
        if r.returncode != 0:
            return None
        try:   # 4a linha do postmaster.pid = porta
            with open(os.path.join(self.dados, 'postmaster.pid'), encoding='utf-8', errors='replace') as f:
                return int(f.read().splitlines()[3])
        except (OSError, ValueError, IndexError):
            return None

    def _criar(self):
        log('Criando o banco de dados (primeira vez)...')
        os.makedirs(DADOS, exist_ok=True)
        share = os.path.join(RAIZ, 'bin', 'pgsql', 'share')
        temp_share = None
        # Com acento no caminho (ex.: "André") o initdb do Windows falha
        # ("invalid byte sequence for encoding UTF8"): a pasta share vai para
        # um lugar sem acento so durante a criacao.
        if WIN and not share.isascii():
            base = os.environ.get('PUBLIC') or os.environ.get('SystemDrive', 'C:') + '\\'
            temp_share = os.path.join(base, 'ArgosEPI-pgshare-' + secrets.token_hex(3))
            shutil.copytree(share, temp_share)
            share = temp_share
        fd, pw = tempfile.mkstemp(prefix='argos_pw_', suffix='.txt')
        try:
            with os.fdopen(fd, 'w') as f:
                f.write('argos\n')
            cmd = [self.initdb, '-D', self.dados, '-U', 'argos', f'--pwfile={pw}', '-E', 'UTF8',
                   '--no-locale', '-A', 'scram-sha-256']
            if temp_share:   # no resto o initdb acha o share sozinho (no Linux fica em share/postgresql)
                cmd += ['-L', share]
            r = _rodar(cmd, timeout=600)
            saida = (r.stdout + r.stderr).decode('utf-8', 'replace')
            if r.returncode != 0:
                for linha in saida.splitlines()[-8:]:
                    log(linha, 'banco')
                raise RuntimeError('o initdb falhou (veja o Registro)')
        finally:
            os.remove(pw)
            if temp_share:
                shutil.rmtree(temp_share, ignore_errors=True)
        with open(os.path.join(self.dados, 'postgresql.conf'), 'a', encoding='utf-8') as f:
            f.write("\n# Argos EPI Servidor\nlisten_addresses = '127.0.0.1'\n")
            if not WIN:
                f.write("unix_socket_directories = ''\n")

    def ligar(self):
        if not self.existe:
            self.estado, self.erro = 'erro', 'PostgreSQL portatil nao encontrado (bin/pgsql). Reinstale o programa.'
            raise RuntimeError(self.erro)
        if not WIN and hasattr(os, 'geteuid') and os.geteuid() == 0:
            self.estado, self.erro = 'erro', 'O PostgreSQL nao roda como root: abra o programa com o seu usuario.'
            raise RuntimeError(self.erro)
        self.estado, self.erro = 'ligando', ''
        try:
            if not os.path.exists(os.path.join(self.dados, 'PG_VERSION')):
                self._criar()
            porta = self._rodando()
            if porta:
                self.porta = porta
                log(f'Banco ja estava ligado (porta {porta}).', 'banco')
            else:
                self.porta = next((p for p in range(5433, 5460) if _porta_livre(p)), None)
                if not self.porta:
                    raise RuntimeError('nenhuma porta livre entre 5433 e 5459')
                os.makedirs(LOGS, exist_ok=True)
                # saida para DEVNULL: o postgres herda os canais e travaria quem le
                r = subprocess.run([self.pg_ctl, '-D', self.dados, '-l', self.arq_log, '-w', '-t', '90',
                                    '-o', f'-p {self.porta}', 'start'],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=SEM_JANELA, timeout=150)
                if r.returncode != 0:
                    raise RuntimeError(f'o banco nao ligou (veja {self.arq_log})')
                self.liguei = True
                log(f'Banco ligado na porta {self.porta}.', 'banco')
            self._garantir_base()
            self.estado = 'ligado'
        except Exception as e:
            self.estado, self.erro = 'erro', str(e)
            raise

    def _garantir_base(self):
        import psycopg
        with psycopg.connect(f'postgresql://argos:argos@127.0.0.1:{self.porta}/postgres',
                             autocommit=True, connect_timeout=15) as c:
            if not c.execute("SELECT 1 FROM pg_database WHERE datname='argosepi'").fetchone():
                c.execute('CREATE DATABASE argosepi')
                log('Base "argosepi" criada.', 'banco')

    def desligar(self):
        if self.estado in ('parado', 'erro') and not self.liguei:
            return
        self.estado = 'parando'
        try:
            _rodar([self.pg_ctl, '-D', self.dados, '-m', 'fast', '-w', '-t', '60', 'stop'], timeout=90)
            log('Banco desligado.', 'banco')
        except Exception as e:
            log(f'Falha ao desligar o banco: {e}', 'banco')
        self.estado, self.liguei = 'parado', False


# ── modelos de rosto (baixados uma vez, da fonte oficial) ────────────
class Modelos:
    def __init__(self):
        self.estado = 'ok' if self.prontos() else 'faltando'
        self.pct, self.erro = 0, ''

    @staticmethod
    def prontos():
        return all(os.path.exists(os.path.join(ROSTO_DIR, a)) for a in ROSTO_ARQS)

    def baixar(self):
        if self.prontos():
            self.estado = 'ok'
            return
        self.estado, self.pct, self.erro = 'baixando', 0, ''
        log('Baixando os modelos de reconhecimento facial (InsightFace buffalo_l, cerca de 280 MB)...')
        tmp = os.path.join(tempfile.gettempdir(), 'argos-buffalo_l.zip')
        try:
            req = urllib.request.Request(INSIGHTFACE_ZIP, headers={'User-Agent': 'ArgosEPI-Servidor'})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, 'wb') as f:
                total = int(r.headers.get('Content-Length') or 0)
                feito = 0
                while True:
                    bloco = r.read(1 << 20)
                    if not bloco:
                        break
                    f.write(bloco)
                    feito += len(bloco)
                    if total:
                        self.pct = int(feito * 100 / total)
            os.makedirs(ROSTO_DIR, exist_ok=True)
            with zipfile.ZipFile(tmp) as z:
                for nome in z.namelist():
                    base = os.path.basename(nome)
                    if base in ROSTO_ARQS:
                        with z.open(nome) as o, open(os.path.join(ROSTO_DIR, base), 'wb') as d:
                            shutil.copyfileobj(o, d)
            if not self.prontos():
                raise RuntimeError('o pacote baixado nao tem os modelos esperados')
            self.estado, self.pct = 'ok', 100
            log('Modelos de rosto prontos.')
        except Exception as e:
            self.estado, self.erro = 'erro', str(e)
            log(f'Nao consegui baixar os modelos de rosto ({e}). O servidor liga sem reconhecimento '
                'facial; tento de novo na proxima vez.')
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass


# ── backend (run.py) ─────────────────────────────────────────────────
class Backend:
    def __init__(self, banco, rede_local=lambda: False):
        self.banco = banco
        self.rede_local = rede_local
        self.proc = None
        self.estado, self.porta, self.erro = 'parado', None, ''
        self.desde = 0.0
        self.resumo = None
        self.reinicios = []
        self.querer_ligado = False
        self.lock = threading.Lock()

    def ligar(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return
            self.querer_ligado = True
            self.porta = next((p for p in range(8088, 8098) if _porta_livre(p)), None)
            if not self.porta:
                self.estado, self.erro = 'erro', 'Todas as portas de 8088 a 8097 estao ocupadas.'
                log(self.erro)
                return
            env = dict(os.environ)
            env.update({'ARGOS_PORTA': str(self.porta), 'ARGOS_ABRIR_NAVEGADOR': '0',
                        'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8', 'ARGOS_PROGRAMA': '1',
                        'ARGOS_DOWNLOADS_URL': FONTE})   # de onde o backend baixa o pacote do TensorRT
            if self.banco.porta:
                env['DATABASE_URL'] = self.banco.url()
            # So este computador e o tunel acessam: assim o Windows nao mostra o aviso do
            # firewall (que pede administrador). "Aceitar a rede local" fica nos Ajustes.
            if not os.environ.get('ARGOS_HOST'):
                env['ARGOS_HOST'] = '0.0.0.0' if self.rede_local() else '127.0.0.1'
            self.estado, self.erro, self.resumo = 'iniciando', '', None
            self.desde = time.time()
            log(f'Ligando o servidor na porta {self.porta}...')
            kw = {'creationflags': SEM_JANELA} if WIN else {'start_new_session': True}
            cmd = [sys.executable, 'servidor'] if CONGELADO else [sys.executable, '-u', 'run.py']
            self.proc = subprocess.Popen(cmd, cwd=RAIZ, env=env,
                                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, **kw)
            threading.Thread(target=self._ler, args=(self.proc,), daemon=True).start()

    def _ler(self, proc):
        for linha in iter(proc.stdout.readline, b''):
            texto = linha.decode('utf-8', 'replace')
            if '"GET /local/' in texto:     # consultas desta janela a cada 2 s: so poluiriam o registro
                continue
            log(texto, 'servidor')
        proc.wait()
        if proc is self.proc and self.querer_ligado:
            self.estado = 'erro'
            self.erro = f'O servidor parou sozinho (codigo {proc.returncode}).'
            log(self.erro)
            agora = time.time()
            self.reinicios = [t for t in self.reinicios if agora - t < 300] + [agora]
            if len(self.reinicios) <= 4:
                log('Ligando de novo em 5 segundos...')
                time.sleep(5)
                if self.querer_ligado and proc is self.proc:
                    self.ligar()
            else:
                log('Caiu muitas vezes seguidas: confira o Registro e toque em "Ligar".')

    def desligar(self):
        with self.lock:
            self.querer_ligado = False
            p = self.proc
            if not p or p.poll() is not None:
                self.estado = 'parado'
                return
            self.estado = 'parando'
            log('Desligando o servidor...')
            # mata a arvore toda: o run.py abre o cloudflared como filho
            try:
                if WIN:
                    _rodar(['taskkill', '/PID', str(p.pid), '/T', '/F'], timeout=30)
                else:
                    import signal
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                p.wait(timeout=20)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
            self.estado, self.resumo = 'parado', None
            log('Servidor desligado.')

    def acompanhar(self):
        """Pergunta ao backend como ele esta (conta, cameras) a cada 2 s."""
        while True:
            time.sleep(2)
            p = self.proc
            if not p or p.poll() is not None or not self.porta:
                continue
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{self.porta}/local/resumo', timeout=4) as r:
                    self.resumo = json.loads(r.read().decode('utf-8'))
                if self.estado in ('iniciando', 'erro'):
                    log('Servidor pronto.')
                self.estado, self.erro = 'ligado', ''
            except Exception:
                if self.estado == 'ligado' and time.time() - self.desde > 30:
                    self.estado = 'iniciando'


# ── atualizacao ──────────────────────────────────────────────────────
def _num_versao(v):
    return tuple(int(x) for x in re.findall(r'\d+', str(v))[:4]) or (0,)


class Atualizacao:
    def __init__(self):
        self.disponivel = None
        self.baixando = False
        self.pct = 0
        self.erro = ''
        self.manifesto = None

    def conferir(self):
        if VERSAO == 'dev':
            return
        try:
            req = urllib.request.Request(FONTE + 'latest.json', headers={'User-Agent': 'ArgosEPI-Servidor'})
            with urllib.request.urlopen(req, timeout=20) as r:
                m = json.loads(r.read().decode('utf-8'))
            self.manifesto = m
            nova = m.get('versao')
            if not WIN:
                # o pacote do Linux pode estar numa versao anterior a do Windows: vale a do arquivo dele
                url = ((m.get('linux') or {}).get(VARIANTE) or {}).get('url') or ''
                achada = re.search(r'servidor-(\d+(?:\.\d+)+)-', url)
                nova = achada.group(1) if achada else nova
            if _num_versao(nova) > _num_versao(VERSAO):
                self.disponivel = {'versao': nova, 'notas': m.get('notas') or ''}
        except Exception:
            pass

    def laco(self):
        while True:
            self.conferir()
            time.sleep(6 * 3600)

    def instalar(self, ao_pronto):
        """Windows: baixa o instalador novo e roda em modo atualizacao. Linux: abre o site."""
        if not WIN:
            _abrir(SITE + '/#servidores')
            return
        m = self.manifesto or {}
        url = ((m.get('windows') or {}).get('setup') or {}).get('url')
        if not url:
            self.erro = 'Instalador novo nao encontrado.'
            return
        self.baixando, self.pct, self.erro = True, 0, ''
        try:
            destino = os.path.join(tempfile.gettempdir(), 'ArgosEPI-Servidor-Setup.exe')
            with urllib.request.urlopen(urllib.request.Request(url if '://' in url else FONTE + url,
                                        headers={'User-Agent': 'ArgosEPI-Servidor'}), timeout=60) as r, \
                    open(destino, 'wb') as f:
                total = int(r.headers.get('Content-Length') or 0)
                feito = 0
                while True:
                    b = r.read(1 << 16)
                    if not b:
                        break
                    f.write(b)
                    feito += len(b)
                    self.pct = int(feito * 100 / total) if total else 0
            subprocess.Popen([destino, '--atualizar', RAIZ, '--fonte', FONTE], close_fds=True,
                             creationflags=0x00000008 | 0x00000200)   # DETACHED_PROCESS | NEW_PROCESS_GROUP
            ao_pronto()
        except Exception as e:
            self.erro = f'Falha ao baixar a atualizacao: {e}'
        finally:
            self.baixando = False


# ── o supervisor em si ───────────────────────────────────────────────
class Supervisor:
    def __init__(self, exe, pai):
        self.config = Config(exe)
        self.banco = Banco()
        self.modelos = Modelos()
        self.backend = Backend(self.banco, lambda: bool(self.config.dados.get('rede_local')))
        self.atualizacao = Atualizacao()
        self.pai = pai
        self.saindo = False
        self.etapa = 'Preparando...'
        self.token = secrets.token_urlsafe(24)
        self.parar = threading.Event()

    def iniciar(self):
        """Banco -> modelos -> backend, numa thread (a janela ja abre mostrando o progresso)."""
        try:
            self.etapa = 'Ligando o banco de dados...'
            self.banco.ligar()
        except Exception as e:
            log(f'Banco: {e}')
            self.etapa = 'Banco de dados com problema'
            return
        if not self.modelos.prontos():
            self.etapa = 'Baixando os modelos de rosto...'
            self.modelos.baixar()
        self.etapa = 'Ligando o servidor...'
        self.backend.ligar()
        self.etapa = ''

    def estado(self):
        b = self.backend
        return {
            'app': {'nome': NOME_APP, 'versao': VERSAO, 'variante': VARIANTE, 'pasta': RAIZ,
                    'sistema': 'windows' if WIN else sys.platform, 'etapa': self.etapa, 'saindo': self.saindo},
            'servidor': {'estado': b.estado, 'porta': b.porta, 'erro': b.erro, 'desde': b.desde},
            'banco': {'estado': self.banco.estado, 'porta': self.banco.porta, 'erro': self.banco.erro},
            'modelos': {'estado': self.modelos.estado, 'pct': self.modelos.pct, 'erro': self.modelos.erro},
            'resumo': b.resumo if b.estado == 'ligado' else None,
            'config': dict(self.config.dados),
            'atualizacao': dict(self.atualizacao.disponivel, baixando=self.atualizacao.baixando,
                                pct=self.atualizacao.pct, erro=self.atualizacao.erro)
                           if self.atualizacao.disponivel else None,
        }

    def acao(self, nome, dados):
        b = self.backend
        r = b.resumo or {}
        if nome == 'ligar':
            threading.Thread(target=self._religar, daemon=True).start()
        elif nome == 'desligar':
            threading.Thread(target=b.desligar, daemon=True).start()
        elif nome == 'reiniciar':
            def _r():
                b.desligar()
                self._religar()
            threading.Thread(target=_r, daemon=True).start()
        elif nome == 'vincular':
            _abrir(r.get('link_vincular') or f'{SITE}/parear.html?porta={b.porta or 8088}')
        elif nome == 'desvincular':
            if not b.porta or b.estado != 'ligado':
                return False
            try:
                pedido = urllib.request.Request(f'http://127.0.0.1:{b.porta}/local/desvincular', method='POST',
                                                data=b'{}', headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(pedido, timeout=20) as resp:
                    d = json.loads(resp.read().decode('utf-8'))
            except Exception as e:
                log(f'Nao consegui desvincular: {e}')
                return False
            log('Servidor desvinculado da conta.' + ('' if d.get('site_avisado') else
                ' O site nao respondeu: remova este servidor tambem em Ajustes > Servidores, no painel.'))
            if isinstance(b.resumo, dict):   # a tela ja mostra "Vincule a sua conta"
                b.resumo.update(vinculado=False, conta=None, servidor=None, pares=0, codigo='', cameras=[])
        elif nome == 'painel':
            _abrir(SITE)
        elif nome == 'painel_local':
            _abrir(f'http://localhost:{b.porta}' if b.porta else SITE)
        elif nome == 'pasta_dados':
            _abrir(DADOS)
        elif nome == 'pasta_logs':
            _abrir(LOGS)
        elif nome == 'link':
            url = str(dados.get('url') or '')
            if url.startswith(('https://', 'http://localhost', 'http://127.0.0.1')):
                _abrir(url)
        elif nome == 'atualizar':
            threading.Thread(target=self.atualizacao.instalar, args=(self.encerrar,), daemon=True).start()
        elif nome == 'conferir_atualizacao':
            self.atualizacao.conferir()
        elif nome == 'sair':
            threading.Thread(target=self.encerrar, daemon=True).start()
        else:
            return False
        return True

    def _religar(self):
        if self.banco.estado != 'ligado':
            try:
                self.banco.ligar()
            except Exception as e:
                log(f'Banco: {e}')
                return
        self.backend.reinicios = []
        self.backend.ligar()

    def encerrar(self):
        if self.saindo:
            return
        self.saindo = True
        log('Encerrando...')
        self.backend.desligar()
        self.banco.desligar()
        self.parar.set()

    def vigiar_pai(self):
        """Janela fechada a forca (ou travou e foi morta): desliga tudo junto."""
        if not self.pai:
            return
        import psutil
        while not self.parar.wait(2):
            if not psutil.pid_exists(self.pai):
                log('A janela do programa fechou: desligando.')
                self.encerrar()
                return


def _servidor_http(sup):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _local(self):
            host = (self.headers.get('Host') or '').split(':')[0]
            return host in ('127.0.0.1', 'localhost')

        def _autorizado(self, qs):
            t = self.headers.get('X-Argos-Token') or (qs.get('t') or [''])[0]
            return secrets.compare_digest(t, sup.token)

        def _json(self, obj, status=200):
            corpo = json.dumps(obj, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def _arquivo(self, rel):
            if PAK is not None:   # programa compilado: a interface vem do recursos.pak
                corpo = PAK.ler('ui/' + rel)
                if corpo is None:
                    self.send_error(404)
                    return
            else:
                caminho = os.path.realpath(os.path.join(UI, rel))
                if not caminho.startswith(os.path.realpath(UI) + os.sep) or not os.path.isfile(caminho):
                    self.send_error(404)
                    return
                with open(caminho, 'rb') as f:
                    corpo = f.read()
            tipos = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
                     '.js': 'text/javascript; charset=utf-8', '.png': 'image/png', '.svg': 'image/svg+xml',
                     '.jpg': 'image/jpeg', '.woff2': 'font/woff2', '.ico': 'image/x-icon'}
            self.send_response(200)
            self.send_header('Content-Type', tipos.get(os.path.splitext(rel)[1], 'application/octet-stream'))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def do_GET(self):
            if not self._local():
                return self.send_error(403)
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            if not u.path.startswith('/api/'):
                return self._arquivo(u.path.lstrip('/') or 'index.html')
            if not self._autorizado(qs):
                return self._json({'erro': 'nao autorizado'}, 401)
            if u.path == '/api/estado':
                return self._json(sup.estado())
            if u.path == '/api/registro':
                return self._json({'linhas': log.desde(int((qs.get('desde') or ['0'])[0] or 0))})
            if u.path.startswith('/api/quadro/') and sup.backend.porta:
                sid = u.path.rsplit('/', 1)[-1]
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{sup.backend.porta}/local/quadro/{sid}',
                                                timeout=4) as r:
                        corpo = r.read()
                    if corpo:
                        self.send_response(200)
                        self.send_header('Content-Type', 'image/jpeg')
                        self.send_header('Cache-Control', 'no-store')
                        self.send_header('Content-Length', str(len(corpo)))
                        self.end_headers()
                        self.wfile.write(corpo)
                        return
                except Exception:
                    pass
                self.send_response(204)
                self.end_headers()
                return
            return self._json({'erro': 'rota desconhecida'}, 404)

        def do_POST(self):
            if not self._local():
                return self.send_error(403)
            u = urlparse(self.path)
            if not self._autorizado(parse_qs(u.query)):
                return self._json({'erro': 'nao autorizado'}, 401)
            try:
                tam = int(self.headers.get('Content-Length') or 0)
                corpo = json.loads(self.rfile.read(tam).decode('utf-8') or '{}') if tam else {}
            except ValueError:
                return self._json({'erro': 'json invalido'}, 400)
            if u.path == '/api/config':
                rede = bool(sup.config.dados.get('rede_local'))
                cfg = sup.config.mudar(corpo)
                if bool(cfg.get('rede_local')) != rede and sup.backend.querer_ligado:
                    sup.acao('reiniciar', {})   # o endereco em que o servidor escuta so muda ao religar
                return self._json({'config': cfg})
            if u.path == '/api/acao':
                ok = sup.acao(str(corpo.get('acao') or ''), corpo)
                return self._json({'ok': ok}, 200 if ok else 400)
            if u.path == '/api/hardware':   # placa de video ou CPU: quem guarda e aplica e o backend
                if not sup.backend.porta:
                    return self._json({'erro': 'Ligue o servidor para trocar.'}, 409)
                pedido = urllib.request.Request(
                    f'http://127.0.0.1:{sup.backend.porta}/local/hardware', method='POST',
                    data=json.dumps({'dispositivo': corpo.get('dispositivo')}).encode('utf-8'),
                    headers={'Content-Type': 'application/json'})
                try:
                    with urllib.request.urlopen(pedido, timeout=10) as r:
                        hw = json.loads(r.read().decode('utf-8'))
                except Exception as e:
                    return self._json({'erro': f'O servidor nao respondeu: {e}'}, 502)
                log(f"Processar cameras com: {hw.get('dispositivo')}", 'servidor')
                if isinstance(sup.backend.resumo, dict):   # a tela nao volta para a escolha antiga
                    sup.backend.resumo['hardware'] = hw
                return self._json(hw)
            return self._json({'erro': 'rota desconhecida'}, 404)

    return ThreadingHTTPServer(('127.0.0.1', 0), H)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pai', type=int, default=0)
    ap.add_argument('--exe', default='')
    ap.add_argument('--porta-ui', type=int, default=0)
    a = ap.parse_args()

    sup = Supervisor(a.exe, a.pai)
    log(f'{NOME_APP} {VERSAO} ({VARIANTE or "sem variante"}) em {RAIZ}')
    httpd = _servidor_http(sup)
    porta = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    # a janela le esta linha para saber o endereco da interface
    print(f'ARGOS_UI http://127.0.0.1:{porta}/?t={sup.token}', flush=True)

    if not WIN:   # a janela do Linux pede para sair com SIGTERM
        import signal
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=sup.encerrar, daemon=True).start())
    sup.config.aplicar_inicio()
    threading.Thread(target=sup.iniciar, daemon=True).start()
    threading.Thread(target=sup.backend.acompanhar, daemon=True).start()
    threading.Thread(target=sup.atualizacao.laco, daemon=True).start()
    threading.Thread(target=sup.vigiar_pai, daemon=True).start()
    try:
        while not sup.parar.wait(1):
            pass
    except KeyboardInterrupt:
        sup.encerrar()
    httpd.shutdown()
    log('Fim.')


if __name__ == '__main__':
    main()
