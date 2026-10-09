"""
rtc.py - Video direto (WebRTC) entre o celular/navegador e este servidor.

Antes todo quadro era um JPEG mandado por WebSocket pelo tunel (TCP, passando pela Cloudflare).
Agora, no modo tempo real, a camera manda video de verdade (H.264/VP8) por WebRTC:
  - o aperto de maos (troca de SDP) continua pelo endereco que ja existe (tunel ou rede local),
    por estas rotas do servidor, que conferem o login e repassam ao MediaMTX;
  - o video vai por UDP direto para este computador. O STUN descobre o endereco publico dos dois
    lados; quando a rede nao deixa fechar a conexao direta, o navegador usa o TURN (so se a conta
    tiver as chaves cadastradas no site) e, se nem assim, volta sozinho para o caminho antigo.

O MediaMTX (midia/mediamtx) e um programa separado que fica ao lado do servidor: recebe o WebRTC,
entrega os quadros para a analise por RTSP em 127.0.0.1 e repassa o mesmo video, sem recodificar,
para quem assiste pelo painel. As portas de controle ficam so em 127.0.0.1.

O video NAO usa porta fixa: cada conexao abre uma porta UDP qualquer, como o navegador faz, e e
este computador que manda o primeiro pacote. Assim o Firewall do Windows nao mostra aviso nem
pede administrador (porta fixa de escuta faz ele perguntar).

  ARGOS_RTC=0                desliga o video direto (fica so o caminho antigo)
  ARGOS_RTC_HOST=127.0.0.1   escuta o video so neste computador (testes)
  ARGOS_RTC_PORTA_UDP=8189   porta UDP fixa, para quem abre a porta no roteador ou usa Docker
                             (no Windows faz o Firewall perguntar uma vez)
  ARGOS_RTC_HOSTS=ip1,ip2    com porta fixa: enderecos extras a anunciar (Docker: o IP do computador)
  ARGOS_RTC_LOG=debug        registro detalhado em dados/rtc/mediamtx.log
"""
import atexit
import os
import re
import socket
import struct
import subprocess
import threading
import time
from collections import deque

import requests

import conta
import midia
import pastas

ATIVO = os.environ.get('ARGOS_RTC', '1').strip().lower() not in ('0', 'false', 'nao', 'não', 'off')
HOST = os.environ.get('ARGOS_RTC_HOST', '').strip()
HOSTS_EXTRAS = [h.strip() for h in os.environ.get('ARGOS_RTC_HOSTS', '').split(',') if h.strip()]
STUN = 'stun:stun.cloudflare.com:3478'
LOG = os.environ.get('ARGOS_RTC_LOG', 'warn').strip().lower()
if LOG not in ('error', 'warn', 'info', 'debug'):
    LOG = 'warn'
PASTA = os.path.join(pastas.RAIZ, 'dados', 'rtc')

_lock = threading.Lock()
_proc = None
_portas = {}
_erro = ''
_ice = {'ate': 0.0, 'servidores': None}


def _porta_livre(tipo=socket.SOCK_STREAM, preferida=0, host='127.0.0.1'):
    for porta in ([preferida] if preferida else []) + [0]:
        s = socket.socket(socket.AF_INET, tipo)
        try:
            s.bind((host, porta))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    return 0


def _config(p) -> str:
    so_local = HOST in ('127.0.0.1', 'localhost')
    hosts = (['127.0.0.1'] if so_local else []) + HOSTS_EXTRAS
    # sem porta fixa: nada de escuta (o MediaMTX abre uma porta qualquer por conexao)
    udp = f"{HOST}:{p['udp']}" if p.get('udp') else "''"
    linhas = [
        f'logLevel: {LOG}',
        'logDestinations: [stdout]',
        'api: true',
        f"apiAddress: 127.0.0.1:{p['api']}",
        'rtsp: true',
        'rtspTransports: [tcp]',
        f"rtspAddress: 127.0.0.1:{p['rtsp']}",
        'rtmp: false',
        'hls: false',
        'srt: false',
        'moq: false',
        'webrtc: true',
        f"webrtcAddress: 127.0.0.1:{p['http']}",
        'webrtcAllowOrigins: []',
        f'webrtcLocalUDPAddress: {udp}',
        "webrtcLocalTCPAddress: ''",
        f"webrtcIPsFromInterfaces: {'false' if so_local else 'true'}",
        'webrtcAdditionalHosts: [' + ', '.join(hosts) + ']',
        'webrtcSTUNGatherTimeout: 2s',
        'webrtcHandshakeTimeout: 10s',
    ]
    if so_local:
        linhas.append('webrtcICEServers2: []')
    else:
        linhas += ['webrtcICEServers2:', f'  - url: {STUN}']
    linhas += ['paths:', '  all_others:', '']
    return '\n'.join(linhas)


def _vivo() -> bool:
    return _proc is not None and _proc.poll() is None


def _http_responde(porta) -> bool:
    try:
        with socket.create_connection(('127.0.0.1', porta), timeout=0.3):
            return True
    except OSError:
        return False


def iniciar() -> bool:
    """Liga o MediaMTX se ainda nao estiver ligado. False = sem video direto por enquanto."""
    global _proc, _portas, _erro
    if not ATIVO:
        _erro = 'desligado (ARGOS_RTC=0)'
        return False
    with _lock:
        if _vivo():
            return True
        exe = midia.caminho('mediamtx')
        if not exe:
            midia.garantir('mediamtx')
            _erro = 'o programa de vídeo direto ainda não está instalado'
            return False
        try:
            fixa = int(os.environ.get('ARGOS_RTC_PORTA_UDP', '0') or 0)
        except ValueError:
            fixa = 0
        p = {'api': _porta_livre(), 'rtsp': _porta_livre(), 'http': _porta_livre(), 'udp': 0}
        com_porta = bool(fixa) or HOST in ('127.0.0.1', 'localhost')
        if com_porta:
            p['udp'] = _porta_livre(socket.SOCK_DGRAM, fixa or 8189)     # conferida so em 127.0.0.1
        tcp = [p['api'], p['rtsp'], p['http']]
        if not all(tcp) or len(set(tcp)) < 3 or (com_porta and not p['udp']):
            _erro = 'sem portas livres para o vídeo direto'
            return False
        os.makedirs(PASTA, exist_ok=True)
        cfg = os.path.join(PASTA, 'mediamtx.yml')
        with open(cfg, 'w', encoding='utf-8') as f:
            f.write(_config(p))
        try:
            _proc = midia.abrir([exe, cfg], cwd=PASTA, stdout=open(os.path.join(PASTA, 'mediamtx.log'), 'wb'),
                                stderr=subprocess.STDOUT)
        except OSError as e:
            _erro = f'não foi possível abrir o vídeo direto: {e}'
            return False
        _portas = p
        fim = time.time() + 6
        while time.time() < fim and _vivo() and not _http_responde(p['http']):
            time.sleep(0.1)
        if not _vivo() or not _http_responde(p['http']):
            _erro = 'o programa de vídeo direto não ligou'
            parar_sem_trava()
            return False
        _erro = ''
        print('[rtc] video direto ligado (' + (f"UDP {p['udp']}" if p['udp'] else 'sem porta fixa') + ')', flush=True)
        return True


def parar_sem_trava():
    global _proc
    p, _proc = _proc, None
    if p is not None:
        try:
            p.kill()
        except OSError:
            pass


def parar():
    with _lock:
        parar_sem_trava()


atexit.register(parar)


def situacao() -> dict:
    est = midia.estado()['mediamtx']
    return {'ativo': ATIVO, 'ligado': _vivo(), 'instalado': est['status'] == 'pronto',
            'baixando': est['status'] == 'baixando', 'erro': _erro,
            'porta_udp': (_portas.get('udp') or None) if _vivo() else None}


def url_leitura(sid) -> str:
    return f"rtsp://127.0.0.1:{_portas.get('rtsp')}/{sid}"


def _trocar(sid, tipo, sdp):
    """Repassa a oferta do navegador ao MediaMTX. (resposta SDP, id da sessao)."""
    if not iniciar():
        raise RuntimeError(_erro or 'vídeo direto indisponível')
    r = requests.post(f"http://127.0.0.1:{_portas['http']}/{sid}/{tipo}", data=sdp.encode('utf-8'),
                      headers={'Content-Type': 'application/sdp'}, timeout=15)
    if r.status_code not in (200, 201):
        try:
            motivo = r.json().get('error') or ''
        except ValueError:
            motivo = r.text[:120]
        raise RuntimeError(motivo or f'o vídeo direto recusou a conexão ({r.status_code})')
    sessao = (r.headers.get('Location') or '').rstrip('/').rsplit('/', 1)[-1]
    return r.text, sessao


def publicar(sid, sdp):
    return _trocar(sid, 'whip', sdp)


def assistir(sid, sdp):
    return _trocar(sid, 'whep', sdp)


def encerrar(sid, tipo, sessao):
    if not _vivo() or tipo not in ('whip', 'whep') or not sessao.replace('-', '').isalnum():
        return
    try:
        requests.delete(f"http://127.0.0.1:{_portas['http']}/{sid}/{tipo}/{sessao}", timeout=5)
    except requests.RequestException:
        pass


def publicando(sid) -> bool:
    """Ha video direto chegando para esta camera agora?"""
    if not _vivo():
        return False
    try:
        r = requests.get(f"http://127.0.0.1:{_portas['api']}/v3/paths/get/{sid}", timeout=2)
        return r.status_code == 200 and bool(r.json().get('ready'))
    except (requests.RequestException, ValueError):
        return False


def chegada(sid):
    """(pacotes recebidos, pacotes perdidos) do video direto que esta camera esta mandando, contados
    pelo MediaMTX depois de esperar as retransmissoes. None se nao ha ninguem publicando."""
    if not _vivo():
        return None
    try:
        r = requests.get(f"http://127.0.0.1:{_portas['api']}/v3/webrtcsessions/list", timeout=2)
        itens = r.json().get('items') or []
    except (requests.RequestException, ValueError):
        return None
    rec = per = 0
    achou = False
    for s in itens:
        if s.get('path') == sid and s.get('state') == 'publish':
            achou = True
            rec += int(s.get('inboundRTPPackets') or s.get('rtpPacketsReceived') or 0)
            per += int(s.get('inboundRTPPacketsLost') or s.get('rtpPacketsLost') or 0)
    return (rec, per) if achou else None


class RelogioRtp(threading.Thread):
    """Descobre o tempo RTP dos quadros que a analise recebe.

    Quem assiste pelo painel sabe, de cada quadro que o navegador mostra, o tempo RTP dele (o numero
    que o celular carimbou ao filmar; o MediaMTX repassa igual). O leitor de video da analise (OpenCV)
    so informa o tempo contado a partir do primeiro quadro que ELE leu. Para a caixa cair exatamente
    no quadro em que foi calculada, falta a ponte entre os dois, e ela sai dos quadros-chave: esta
    thread le o mesmo video por RTSP so olhando os cabecalhos (sem decodificar) e anota o tempo RTP e
    a hora de chegada de cada quadro-chave; o OpenCV diz quais dos quadros dele sao quadros-chave. Um
    par basta (eles vem a cada ~2 s); o segundo confirma e a thread encerra."""

    def __init__(self, url, segundos=20.0):
        super().__init__(daemon=True, name='relogio-rtp')
        self.url = url
        self.segundos = segundos
        self.chaves = deque(maxlen=12)      # (tempo rtp, hora de chegada em perf_counter)
        self.fim = threading.Event()
        self.erro = ''

    def parar(self):
        self.fim.set()

    def base_para(self, ticks, lido_em):
        """Quadro-chave lido pelo OpenCV em lido_em (perf_counter), com tempo proprio 'ticks' (1/90000 s).
        Devolve o que somar a qualquer tempo proprio para ter o tempo RTP, ou None se ainda nao da
        para ter certeza (nenhum quadro-chave visto perto dessa hora, ou mais de um)."""
        perto = [rtp for rtp, chegou in list(self.chaves) if -0.1 < lido_em - chegou < 0.7]
        if len(perto) != 1:
            return None
        return (perto[0] - int(ticks)) & 0xFFFFFFFF

    def run(self):
        try:
            self._ler()
        except Exception as e:      # e so uma ajuda: sem ela o painel usa a estimativa pelos relogios
            self.erro = str(e)[:120]

    def _ler(self):
        m = re.match(r'rtsp://([^:/]+):(\d+)/', self.url)
        s = socket.create_connection((m.group(1), int(m.group(2))), timeout=4)
        try:
            s.settimeout(4)
            buf = b''
            cseq = [0]

            def pedir(metodo, alvo, extra=''):
                nonlocal buf
                cseq[0] += 1
                s.sendall(f'{metodo} {alvo} RTSP/1.0\r\nCSeq: {cseq[0]}\r\nUser-Agent: argos\r\n{extra}\r\n'.encode())
                while b'\r\n\r\n' not in buf:
                    parte = s.recv(65536)
                    if not parte:
                        raise OSError('conexao fechada')
                    buf += parte
                cab, buf = buf.split(b'\r\n\r\n', 1)
                cab = cab.decode('latin1')
                if ' 200 ' not in cab.split('\r\n', 1)[0]:
                    raise OSError(cab.split('\r\n', 1)[0])
                tam = re.search(r'Content-Length:\s*(\d+)', cab, re.I)
                corpo = b''
                if tam:
                    n = int(tam.group(1))
                    while len(buf) < n:
                        buf += s.recv(65536)
                    corpo, buf = buf[:n], buf[n:]
                return cab, corpo.decode('latin1')

            pedir('OPTIONS', self.url)
            _, sdp = pedir('DESCRIBE', self.url, 'Accept: application/sdp\r\n')
            codec = (re.search(r'a=rtpmap:\d+ ([A-Za-z0-9]+)/90000', sdp) or [None, ''])[1].upper()
            if codec not in ('H264', 'VP8'):
                self.erro = f'codec {codec or "desconhecido"}'
                return
            faixas = [c for c in re.findall(r'a=control:(\S+)', sdp) if c != '*']
            if not faixas:
                raise OSError('sem faixa de video')
            alvo = faixas[0] if faixas[0].startswith('rtsp://') else self.url.rstrip('/') + '/' + faixas[0]
            cab, _ = pedir('SETUP', alvo, 'Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n')
            sessao = re.search(r'Session:\s*([^;\r\n]+)', cab).group(1)
            pedir('PLAY', self.url, f'Session: {sessao}\r\nRange: npt=0.000-\r\n')
            fim = time.perf_counter() + self.segundos
            ultimo = None
            while not self.fim.is_set() and time.perf_counter() < fim:
                while len(buf) < 4:
                    parte = s.recv(65536)
                    if not parte:
                        return
                    buf += parte
                if buf[0] != 0x24:              # resposta RTSP no meio (nao esperada): acha o proximo pacote
                    i = buf.find(b'$', 1)
                    buf = buf[i:] if i >= 0 else b''
                    continue
                canal, n = buf[1], struct.unpack('>H', buf[2:4])[0]
                while len(buf) < 4 + n:
                    parte = s.recv(65536)
                    if not parte:
                        return
                    buf += parte
                pkt, buf = buf[4:4 + n], buf[4 + n:]
                if canal != 0 or n < 14:
                    continue
                inicio = 12 + 4 * (pkt[0] & 0x0F)
                if pkt[0] & 0x10 and len(pkt) >= inicio + 4:       # cabecalho com extensao
                    inicio += 4 + 4 * struct.unpack('>H', pkt[inicio + 2:inicio + 4])[0]
                if len(pkt) < inicio + 2:
                    continue
                tempo = struct.unpack('>I', pkt[4:8])[0]
                try:
                    chave = tempo != ultimo and _quadro_chave(codec, pkt, inicio)
                except IndexError:              # pacote curto demais para dizer
                    chave = False
                if chave:
                    ultimo = tempo
                    self.chaves.append((tempo, time.perf_counter()))
        finally:
            try:
                s.close()
            except OSError:
                pass


def _quadro_chave(codec, pkt, i):
    """Este pacote RTP pertence a um quadro-chave?"""
    if codec == 'H264':
        nal = pkt[i] & 0x1F
        if nal in (5, 7):                       # IDR ou SPS
            return True
        if nal == 28:                           # FU-A: pedaco de uma unidade maior
            return (pkt[i + 1] & 0x1F) == 5
        if nal == 24:                           # STAP-A: varias unidades pequenas juntas
            j = i + 1
            while j + 2 < len(pkt):
                tam = struct.unpack('>H', pkt[j:j + 2])[0]
                if (pkt[j + 2] & 0x1F) in (5, 7):
                    return True
                j += 2 + tam
        return False
    # VP8: descritor do pacote e, no comeco do quadro, o bit P do proprio VP8 (0 = quadro-chave)
    b = pkt[i]
    j = i + 1
    if b & 0x80:
        x = pkt[j]
        j += 1
        if x & 0x80:
            j += 2 if pkt[j] & 0x80 else 1
        if x & 0x40:
            j += 1
        if x & 0x30:
            j += 1
    return bool(b & 0x10) and (b & 0x07) == 0 and j < len(pkt) and not pkt[j] & 0x01


def servidores_ice() -> list:
    """STUN sempre; TURN quando a conta tem as chaves no site (credencial temporaria, renovada sozinha)."""
    if HOST in ('127.0.0.1', 'localhost'):
        return []
    agora = time.time()
    if _ice['servidores'] is not None and agora < _ice['ate']:
        return _ice['servidores']
    lista, validade = [{'urls': [STUN]}], 300
    cred = conta.credencial()
    if cred:
        try:
            r = conta._post('/servidores/ice', {}, token=cred, timeout=8)
            if r.status_code == 200:
                d = r.json()
                if isinstance(d.get('iceServers'), list) and d['iceServers']:
                    lista = d['iceServers']
                    validade = max(300, min(int(d.get('validade_s') or 3600) // 2, 6 * 3600))
        except (requests.RequestException, ValueError):
            pass
    _ice.update(servidores=lista, ate=agora + validade)
    return lista
