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
import socket
import subprocess
import threading
import time

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
