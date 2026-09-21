"""
servidor.py - Painel local para acompanhar o treinamento do detector de EPIs em tempo real.

So LE arquivos (estado do pipeline, results.csv, log, checkpoints) e consulta GPU/RAM.
Nunca escreve nada nem mexe nos processos do treino.

Uso:
  python treinamento/acompanhar_treinamento/servidor.py              # http://127.0.0.1:8765
  python treinamento/acompanhar_treinamento/servidor.py --porta 9000
  python treinamento/acompanhar_treinamento/servidor.py --publico    # + tunel e registro no hub

Com --publico, o painel abre um tunel do Cloudflare e avisa o hub onde ele esta, para o site
https://github.com/AndrosoftStudio/treinamentoargosepi achar este PC sozinho (ver ponte.py).
"""
import argparse
import csv
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
import comum  # noqa: E402
import ponte  # noqa: E402

try:
    import psutil
except ImportError:  # sem psutil o painel funciona, so sem CPU/RAM e deteccao exata do processo
    psutil = None
# num container o psutil so ve o proprio container: o treino roda no Windows, entao vale a idade do log
if os.environ.get('ARGOS_PAINEL_CONTAINER', '').strip() == '1':
    psutil = None

ETAPAS = ['baixar', 'montar', 'professor', 'pseudo', 'final', 'avaliar']
NOMES = {'baixar': 'Download dos datasets', 'montar': 'Montagem do dataset', 'professor': 'Treino do professor',
         'pseudo': 'Pseudo-rótulos', 'final': 'Treino final', 'avaliar': 'Avaliação'}
UNID_PSEUDO, UNID_AVALIAR = 2, 1        # peso no progresso geral, em "epocas equivalentes"
PSEUDO_IMG_S, AVALIAR_S = 70, 300       # ritmos medidos neste PC
ESTATICOS = {'/': 'index.html', '/index.html': 'index.html', '/logo.png': 'logo.png', '/favicon.ico': 'favicon.ico'}
# o logo do Argos fica em frontend/ ou em coisas/, na raiz acima do v19
PASTAS_MARCA = [os.path.join(comum.RAIZ_PROJETO, 'frontend'), os.path.join(os.path.dirname(comum.RAIZ_PROJETO), 'coisas')]
TIPOS = {'.html': 'text/html; charset=utf-8', '.png': 'image/png', '.ico': 'image/x-icon'}

ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
RE_TREINO = re.compile(r'^\s*(\d+)/(\d+)\s+([\d.]+)G\s+.*?:\s*(\d+)%\S*\s*\S*\s+(\d+)/(\d+)\s+([\d.]+)(it/s|s/it)'
                       r'(?:\s+([^\s<]+)(?:<(\S+))?)?')
RE_VAL = re.compile(r'Class\s+Images\s+Instances.*?:\s*(\d+)%.*?(\d+)/(\d+)')
RE_RESUMO_VAL = re.compile(r'^\s*all\s+(\d+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$')
RE_PSEUDO_SPLIT = re.compile(r'^\[(train|val)\] (\d+) imagens')
RE_PSEUDO_N = re.compile(r'^\s+(\d+)/(\d+)$')
RE_RUIDO = re.compile(r"'half' is deprecated|Scanning |^\s*\d+\s+(-1|\[)|^\s*from\s+n\s+params|ultralytics\.nn\.modules"
                      r"|^\s*Epoch\s+GPU_mem|^\s*\d+/\d+\s+[\d.]+G\s|^\s*Class\s+Images\s+Instances")


def segundos(txt):
    if not txt:
        return None
    try:
        if txt.endswith('s'):
            return float(txt[:-1])
        total = 0.0
        for parte in txt.split(':'):
            total = total * 60 + float(parte)
        return total
    except ValueError:
        return None


def duracao(s):
    s = int(max(0, s))
    h, m = divmod(s // 60, 60)
    return f"{h} h {m:02d} min" if h else (f"{m} min" if m else f"{s} s")


# ── Leitura dos arquivos do treino ─────────────────────────────────
def ler_log(caminho, max_bytes=400_000):
    info = {'existe': False, 'linhas': [], 'erro': None}
    try:
        tamanho, mtime = os.path.getsize(caminho), os.path.getmtime(caminho)
        with open(caminho, 'rb') as f:
            f.seek(max(0, tamanho - max_bytes))
            bruto = f.read()
    except OSError:
        return info
    info.update(existe=True, idade_s=time.time() - mtime)
    linhas = [ANSI.sub('', l).rstrip() for l in re.split(r'[\r\n]+', bruto.decode('utf-8', 'replace'))]
    if tamanho > max_bytes:
        linhas = linhas[1:]  # a primeira linha do trecho lido quase sempre vem cortada no meio
    i_treino = i_val = i_marco = i_erro = -1
    uteis, progresso = [], None
    for i, l in enumerate(linhas):
        if not l.strip():
            continue
        m = RE_TREINO.match(l)
        if m:
            i_treino = i
            info['treino'] = {'epoca': int(m.group(1)), 'epocas': int(m.group(2)), 'vram_gb': float(m.group(3)),
                              'pct': int(m.group(4)), 'it': int(m.group(5)), 'its': int(m.group(6)),
                              'ritmo': f"{m.group(7)} {m.group(8)}", 'decorrido_s': segundos(m.group(9)),
                              'restante_s': segundos(m.group(10))}
            progresso = l.strip()
            continue
        m = RE_VAL.search(l)
        if m:
            i_val = i
            info['val'] = {'pct': int(m.group(1)), 'n': int(m.group(2)), 'total': int(m.group(3))}
            progresso = l.strip()
            continue
        m = RE_PSEUDO_SPLIT.match(l)
        if m:
            info['pseudo'] = {'split': m.group(1), 'total': int(m.group(2)), 'n': 0}
        m = RE_PSEUDO_N.match(l)
        if m and info.get('pseudo'):
            info['pseudo']['n'] = int(m.group(1))
            progresso = f"pseudo-rótulos [{info['pseudo']['split']}] {l.strip()}"
            continue
        if RE_RUIDO.search(l):
            continue
        if l.startswith('>>> ') or l.startswith('====='):
            i_marco = i
        if 'Traceback' in l or re.search(r'\b(Error|out of memory)\b', l):
            i_erro = i
            info['erro'] = l.strip()[:300]
        m = RE_RESUMO_VAL.match(l)
        if m:  # tabela crua do Ultralytics ("all 3385 12834 0.534 ...") vira uma frase
            l = (f"validação ({m.group(1)} imagens): precisão {m.group(3)} · recall {m.group(4)} · "
                 f"mAP50 {m.group(5)} · mAP50-95 {m.group(6)}")
        uteis.append(l.strip()[:300])
    if i_erro < i_marco:  # erro antigo, de antes do ultimo relancamento
        info['erro'] = None
    info['fase'] = 'validacao' if i_val > i_treino else 'treino'
    info['linhas'] = uteis[-14:] + ([progresso] if progresso else [])
    return info


def ler_run(pasta):
    if not os.path.isdir(pasta):
        return None
    epocas = None
    try:
        with open(os.path.join(pasta, 'args.yaml'), encoding='utf-8') as f:
            for l in f:
                m = re.match(r'epochs:\s*(\d+)', l)
                if m:
                    epocas = int(m.group(1))
                    break
    except OSError:
        pass
    resultados = []
    try:
        with open(os.path.join(pasta, 'results.csv'), newline='', encoding='utf-8') as f:
            for bruto in csv.DictReader(f):
                r = {(k or '').strip(): v for k, v in bruto.items()}

                def num(chave):
                    try:
                        return float(r.get(chave))
                    except (TypeError, ValueError):
                        return None
                resultados.append({
                    'epoca': int(float(r['epoch'])), 'tempo': num('time'),
                    'box_treino': num('train/box_loss'), 'cls_treino': num('train/cls_loss'),
                    'box_val': num('val/box_loss'), 'cls_val': num('val/cls_loss'),
                    'precisao': num('metrics/precision(B)'), 'recall': num('metrics/recall(B)'),
                    'map50': num('metrics/mAP50(B)'), 'map5095': num('metrics/mAP50-95(B)'),
                })
    except (OSError, KeyError, ValueError, csv.Error):
        pass
    anterior = None
    for r in resultados:  # 'time' e acumulado; zera quando o treino e retomado
        t = r['tempo']
        r['duracao'] = (t - anterior) if (t is not None and anterior is not None and t > anterior) else (
            t if r['epoca'] == 1 else None)
        anterior = t
    pesos = []
    pasta_pesos = os.path.join(pasta, 'weights')
    if os.path.isdir(pasta_pesos):
        for a in os.listdir(pasta_pesos):
            if a.endswith('.pt'):
                try:
                    st = os.stat(os.path.join(pasta_pesos, a))
                except OSError:
                    continue
                pesos.append({'arquivo': a, 'mb': round(st.st_size / 1048576, 1), 'gravado': st.st_mtime})
        pesos.sort(key=lambda p: p['gravado'], reverse=True)
    return {'epocas': epocas, 'resultados': resultados, 'pesos': pesos,
            'resumo': comum.carregar_json(os.path.join(pasta, 'resumo.json'), None)}


def seg_por_epoca(run, minimo=1):
    duracoes = [r['duracao'] for r in (run or {}).get('resultados', []) if r.get('duracao')]
    return statistics.median(duracoes[-5:]) if len(duracoes) >= minimo else None


# ── Processo, GPU e sistema (com cache curto) ──────────────────────
_cache = {}


def em_cache(chave, validade, funcao):
    agora = time.time()
    if chave not in _cache or agora - _cache[chave][0] > validade:
        _cache[chave] = (agora, funcao())
    return _cache[chave][1]


def processos():
    if psutil is None:
        return None
    achado = {'pipeline': False, 'script': None}
    for p in psutil.process_iter(['cmdline']):
        linha = ' '.join(p.info.get('cmdline') or [])
        if 'pipeline.py' in linha:
            achado['pipeline'] = True
        for script in ('treinar.py', 'pseudo_rotular.py', 'avaliar.py', 'montar_dataset.py', 'baixar_datasets.py'):
            if script in linha:
                achado['script'] = script
    return achado


def gpu():
    try:
        r = subprocess.run(['nvidia-smi', '--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,'
                            'power.draw', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=5,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        nome, util, usada, total, temp, potencia = [v.strip() for v in r.stdout.strip().splitlines()[0].split(',')]

        def f(v):
            try:
                return float(v)
            except ValueError:
                return None
        return {'nome': nome, 'util': f(util), 'usada_mb': f(usada), 'total_mb': f(total), 'temp': f(temp),
                'potencia': f(potencia)}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


_cpu_anterior = None


def sistema():
    """RAM e CPU. O cpu_percent(interval=None) do psutil devolve 0 quando chamado nas threads do servidor,
    entao a CPU e calculada aqui pela diferenca de cpu_times entre duas leituras."""
    global _cpu_anterior
    if psutil is None:
        return None
    mem = psutil.virtual_memory()
    tempos = psutil.cpu_times()
    total, ocioso = sum(tempos), tempos.idle
    cpu = None
    if _cpu_anterior and total > _cpu_anterior[0]:
        cpu = 100 * (1 - (ocioso - _cpu_anterior[1]) / (total - _cpu_anterior[0]))
    _cpu_anterior = (total, ocioso)
    return {'ram_usada_gb': (mem.total - mem.available) / 1024 ** 3, 'ram_total_gb': mem.total / 1024 ** 3,
            'cpu_pct': cpu}


# ── Montagem do status ────────────────────────────────────────────
def montar_status(cfg):
    estado = comum.carregar_json(cfg.estado, {}) or {}
    # o estado guarda o caminho do Windows; no container a pasta chega por --pasta-dataset
    pasta_ds = (cfg.pasta_dataset or estado.get('pasta_dataset')
                or os.path.join(comum.pastas(cfg.base)['datasets'], cfg.nome))
    pasta_runs = comum.pastas(cfg.base)['runs']
    runs = {'professor': ler_run(os.path.join(pasta_runs, cfg.nome + '_professor')),
            'final': ler_run(os.path.join(pasta_runs, cfg.nome))}
    log = ler_log(cfg.log)
    proc = em_cache('proc', 5, processos)
    rodando = proc['pipeline'] if proc is not None else log.get('idade_s', 1e9) < 300
    em_andamento = estado.get('_em_andamento')
    splits = (comum.carregar_json(os.path.join(pasta_ds, 'relatorio.json'), {}) or {}).get('splits', {})
    n_train = (splits.get('train') or {}).get('imagens') or 0
    n_val = (splits.get('val') or {}).get('imagens') or 0

    if estado.get('avaliar') and not rodando:
        situacao = 'concluido'
    elif rodando:
        situacao = 'rodando'
    elif log.get('erro'):
        situacao = 'erro'
    else:
        situacao = 'parado'

    etapas = []
    for e in ETAPAS:
        if estado.get(e):
            st = 'concluida'
        elif e == em_andamento:
            st = 'andamento' if rodando else ('erro' if log.get('erro') else 'parada')
        else:
            st = 'pendente'
        etapas.append({'id': e, 'nome': NOMES[e], 'estado': st, 'concluida_em': estado.get(e)})

    P = (runs['professor'] or {}).get('epocas') or cfg.epocas_professor
    F = (runs['final'] or {}).get('epocas') or cfg.epocas_final
    etapa_atual = em_andamento or next((e for e in ETAPAS if not estado.get(e)), None)
    atual = {'etapa': etapa_atual, 'tipo': None}
    if etapa_atual in ('professor', 'final'):
        run = runs[etapa_atual] or {'resultados': []}
        total = P if etapa_atual == 'professor' else F
        feitas = len(run['resultados'])
        atual.update(tipo='treino', epocas=total, epocas_feitas=feitas, epoca=min(feitas + 1, total), fracao_epoca=0.0,
                     fase=None)
        t = log.get('treino')
        if feitas >= total:
            atual['fase'] = 'teste'
        elif rodando and t and t['epocas'] == total and t['epoca'] == feitas + 1:
            if log.get('fase') == 'validacao' and log.get('val'):
                atual.update(fase='validacao', pct_fase=log['val']['pct'], fracao_epoca=0.9 + 0.1 * log['val']['pct'] / 100)
            else:
                atual.update(fase='treino', pct_fase=t['pct'], fracao_epoca=0.9 * t['pct'] / 100, iteracao=t['it'],
                             iteracoes=t['its'], ritmo=t['ritmo'], decorrido_s=t['decorrido_s'],
                             restante_fase_s=t['restante_s'])
            atual['vram_gb'] = t['vram_gb']
        atual['epocas_feitas_frac'] = feitas + atual['fracao_epoca']
    elif etapa_atual == 'pseudo':
        total_imgs, feitas_imgs = n_train + n_val, 0
        p = log.get('pseudo')
        if p and em_andamento == 'pseudo':
            feitas_imgs = p['n'] if p['split'] == 'train' else n_train + p['n']
        feitas_imgs = min(feitas_imgs, total_imgs)
        atual.update(tipo='pseudo', imagens_total=total_imgs, imagens_feitas=feitas_imgs,
                     fracao=feitas_imgs / total_imgs if total_imgs else 0)

    # progresso geral e previsao de termino
    spe_p = seg_por_epoca(runs['professor'])
    spe_f = seg_por_epoca(runs['final'], minimo=3) or spe_p
    feito, restante, conhecido = 0.0, 0.0, True
    for etapa, total, spe in (('professor', P, spe_p), ('final', F, spe_f)):
        if estado.get(etapa):
            feito += total
            continue
        f = atual['epocas_feitas_frac'] if etapa_atual == etapa else len((runs[etapa] or {}).get('resultados', []))
        feito += f
        if spe:
            restante += (total - f) * spe
        else:
            conhecido = False
    if estado.get('pseudo'):
        feito += UNID_PSEUDO
    else:
        fp = atual.get('fracao', 0) if etapa_atual == 'pseudo' else 0
        feito += UNID_PSEUDO * fp
        restante += (1 - fp) * max(n_train + n_val, 1) / PSEUDO_IMG_S
    if estado.get('avaliar'):
        feito += UNID_AVALIAR
    else:
        restante += AVALIAR_S
    agora = time.time()
    eta = {'restante_s': restante, 'fim_ts': agora + restante,
           'seg_por_epoca': spe_f if etapa_atual == 'final' else spe_p} if conhecido and situacao != 'concluido' else None

    avisos = []
    if situacao == 'erro':
        avisos.append({'nivel': 'critico', 'texto': 'O treino parou com um erro. Peça ao Claude para verificar e retomar; '
                                                    'ele continua do último checkpoint.', 'detalhe': log.get('erro')})
    elif situacao == 'parado' and em_andamento:
        avisos.append({'nivel': 'atencao', 'texto': 'O treino não está rodando. Se o PC desligou ou o processo foi fechado, '
                                                    'peça ao Claude para retomar: ele continua do último checkpoint.'})
    spe_atual = spe_f if etapa_atual == 'final' else spe_p
    if (situacao == 'rodando' and atual.get('fase') == 'treino' and atual.get('pct_fase', 0) >= 5 and spe_atual
            and atual.get('decorrido_s') and atual.get('restante_fase_s') is not None):
        estimada = atual['decorrido_s'] + atual['restante_fase_s']
        if estimada > 1.6 * 0.9 * spe_atual:
            atual['lento'] = True
            avisos.append({'nivel': 'atencao', 'texto': f"Esta época está bem mais lenta que o normal (~{duracao(estimada)} "
                                                        f"só de treino; o normal é ~{duracao(spe_atual)} por época inteira). "
                                                        "Algum programa pode estar usando a GPU, como um jogo."})
    if situacao == 'rodando' and log.get('idade_s', 0) > 900:
        avisos.append({'nivel': 'atencao', 'texto': f"O log não muda há {duracao(log['idade_s'])}."})

    dataset = comum.carregar_json(os.path.join(pasta_ds, 'pseudo_rotulos.json'), None)
    return {
        'agora': agora, 'nome': cfg.nome, 'situacao': situacao, 'etapas': etapas, 'etapa_atual': etapa_atual,
        'progresso_geral': 100 * feito / (P + UNID_PSEUDO + F + UNID_AVALIAR), 'eta': eta, 'atual': atual,
        'avisos': avisos, 'runs': runs, 'pseudo_rotulos': dataset,
        'dataset': {'pasta': pasta_ds, 'imagens': {s: (v or {}).get('imagens') for s, v in splits.items()}},
        'gpu': em_cache('gpu', 3, gpu), 'sistema': em_cache('sis', 2, sistema), 'processo': proc,
        'log': {'linhas': log['linhas'], 'idade_s': log.get('idade_s'), 'erro': log.get('erro')},
    }


# ── HTTP ─────────────────────────────────────────────────────────
class Painel(BaseHTTPRequestHandler):
    cfg = None
    trava = threading.Lock()
    ultimo = (0.0, b'')

    def _enviar(self, codigo, corpo, tipo):
        self.send_response(codigo)
        self.send_header('Content-Type', tipo)
        self.send_header('Content-Length', str(len(corpo)))
        self.send_header('Cache-Control', 'no-store')
        # o painel so le: liberar a origem deixa o site de fora consultar direto, sem proxy
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'X-Painel-Token')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(corpo)

    def _autorizado(self):
        token = getattr(self.cfg, 'token', '')
        if not token:
            return True
        enviado = self.headers.get('X-Painel-Token') or ''
        if not enviado:
            enviado = (parse_qs(urlsplit(self.path).query).get('token') or [''])[0]
        return enviado == token

    def do_OPTIONS(self):
        self._enviar(204, b'', 'text/plain')

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        rota = self.path.split('?', 1)[0]
        if rota == '/health':
            corpo = json.dumps({'ok': True, 'nome': self.cfg.nome, 'ts': time.time()}).encode('utf-8')
            return self._enviar(200, corpo, 'application/json; charset=utf-8')
        if rota == '/api/status':
            if not self._autorizado():
                return self._enviar(401, json.dumps({'falha': 'token invalido'}).encode('utf-8'),
                                    'application/json; charset=utf-8')
            with Painel.trava:
                if time.time() - Painel.ultimo[0] > 1.5:
                    try:
                        corpo = json.dumps(montar_status(self.cfg), ensure_ascii=False).encode('utf-8')
                    except Exception as ex:  # o painel nunca pode cair por causa de um arquivo meio escrito
                        corpo = json.dumps({'falha': f"{type(ex).__name__}: {ex}"}).encode('utf-8')
                    Painel.ultimo = (time.time(), corpo)
                corpo = Painel.ultimo[1]
            return self._enviar(200, corpo, 'application/json; charset=utf-8')
        if rota in ESTATICOS:
            arquivo = ESTATICOS[rota]
            for pasta in ([AQUI] if arquivo == 'index.html' else PASTAS_MARCA):
                try:
                    with open(os.path.join(pasta, arquivo), 'rb') as f:
                        return self._enviar(200, f.read(), TIPOS[os.path.splitext(arquivo)[1]])
                except OSError:
                    continue
        self._enviar(404, b'nao encontrado', 'text/plain; charset=utf-8')

    def log_message(self, *args):
        pass


def resumo_para_hub(cfg):
    """O pouco que o hub guarda de cada painel: da para listar os treinos sem abrir cada um."""
    st = montar_status(cfg)
    atual = st.get('atual') or {}
    return {'situacao': st.get('situacao'), 'etapa': st.get('etapa_atual'), 'progresso': st.get('progresso_geral'),
            'eta_s': (st.get('eta') or {}).get('restante_s'),
            'epoca': atual.get('epoca'), 'epocas': atual.get('epocas')}


def main():
    ap = argparse.ArgumentParser(description='Painel local do treinamento')
    ap.add_argument('--base', default=comum.base_padrao())
    ap.add_argument('--nome', default='argos_epi_v1')
    ap.add_argument('--log', default='', help='padrao: <base>/logs/treino.log')
    ap.add_argument('--pasta-dataset', default=os.environ.get('ARGOS_PAINEL_DATASET', ''),
                    help='pasta do dataset montado (padrao: a gravada no estado do pipeline)')
    ap.add_argument('--host', default='')
    ap.add_argument('--porta', type=int, default=8765)
    ap.add_argument('--epocas-professor', type=int, default=40)
    ap.add_argument('--epocas-final', type=int, default=120)
    ap.add_argument('--publico', action='store_true',
                    help='abre um tunel do Cloudflare e registra no hub, para o site achar este PC sozinho')
    ap.add_argument('--hub', default=ponte.HUB_PADRAO, help='hub onde o painel se registra (com --publico)')
    ap.add_argument('--url-publica', default='', help='endereco publico ja pronto; sem isso, abre um tunel')
    ap.add_argument('--token', default=os.environ.get('ARGOS_PAINEL_TOKEN', ''),
                    help='exige X-Painel-Token (ou ?token=) em /api/status; o site precisa do mesmo valor')
    ap.add_argument('--chave-hub', default=ponte.CHAVE_PADRAO,
                    help='chave do hub (padrao: HUB_API_KEY do ambiente ou do v19/.env)')
    cfg = ap.parse_args()
    cfg.log = cfg.log or os.path.join(cfg.base, 'logs', 'treino.log')
    cfg.estado = os.path.join(cfg.base, f'estado_{cfg.nome}.json')
    # publicado, o painel precisa aceitar a conexao que vem do tunel, e nao so do proprio PC
    cfg.host = cfg.host or ('0.0.0.0' if cfg.publico else '127.0.0.1')
    Painel.cfg = cfg
    sistema()  # primeira leitura de CPU, para a primeira resposta ja ter um valor
    try:
        servidor = ThreadingHTTPServer((cfg.host, cfg.porta), Painel)
    except OSError as ex:
        sys.exit(f"Nao foi possivel abrir a porta {cfg.porta} ({ex}). O painel ja esta aberto? Tente --porta 8766.")
    servidor.daemon_threads = True
    print(f"Acompanhar treinamento: http://{'127.0.0.1' if cfg.host == '0.0.0.0' else cfg.host}:{cfg.porta}"
          "  (Ctrl+C para fechar)", flush=True)
    conexao = None
    if cfg.publico:
        conexao = ponte.Ponte(porta=cfg.porta, nome=cfg.nome, hub=cfg.hub, token=cfg.token,
                              url_publica=cfg.url_publica, chave_hub=cfg.chave_hub,
                              resumo=lambda: resumo_para_hub(cfg))
        conexao.iniciar()

    def _encerrar(*_):  # docker stop manda SIGTERM: sair como no Ctrl+C, para avisar o hub
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _encerrar)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if conexao:
            conexao.parar()


if __name__ == '__main__':
    main()
