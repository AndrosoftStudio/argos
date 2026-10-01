"""
malha.py - Servidores da mesma conta sincronizam o banco entre si.

Cada servidor tem o proprio PostgreSQL. O que e da conta e vale em qualquer
servidor (funcionarios com os rostos, EPIs, areas, desenho das areas, links de
celular e a auditoria) e copiado entre eles; o que e da maquina (modelos .pt,
cameras ligadas, sessoes) fica so nela.

Como funciona:
  - Toda linha sincronizada tem `alterado_em` (hora da mudanca) e `seq` (numero
    crescente deste banco, dado por um gatilho a cada insert/update).
  - Apagar deixa uma marca em malha_apagados (tambem com seq), para a exclusao
    chegar nos outros servidores e um par atrasado nao "ressuscitar" a linha.
  - De tempos em tempos cada servidor pergunta a cada par: "o que mudou depois
    do seq N?" (GET /malha/mudancas) e aplica. Conflito: vale a mudanca mais
    recente (alterado_em maior). Linha aplicada ganha seq novo aqui, entao a
    mudanca segue adiante para os pares dos pares.
  - Fotos (rostos, EPIs, evidencias) vem por /malha/arquivo quando faltam.
  - Os pares se autenticam com a credencial que o site deu a cada servidor
    (assinada pela API): so servidores da mesma conta conversam.
"""
import base64
import os
import re
import threading
import time

import requests
from psycopg import sql
from psycopg.types.json import Jsonb

import conta
import db

INTERVALO_S = 20
LIMITE = 500

# (tabela, chave). A ordem importa: pais antes dos filhos (chaves estrangeiras).
TABELAS = [
    ('epis', 'id'), ('epi_fotos', 'id'), ('funcionarios', 'id'), ('func_fotos', 'id'),
    ('areas', 'id'), ('zonas', 'id'), ('cam_tokens', 'token'), ('auditoria', 'gid'),
]
CHAVE = dict(TABELAS)
ORDEM = {t: i for i, (t, _) in enumerate(TABELAS)}
# so o que e da conta sai daqui; as tabelas filhas nao tem uid, vem pelo pai
FILTRO_CONTA = {
    'epi_fotos': 'epi_id IN (SELECT id FROM epis WHERE uid = %(uid)s)',
    'func_fotos': 'func_id IN (SELECT id FROM funcionarios WHERE uid = %(uid)s)',
}
LOCAIS = {'seq'}                       # colunas que nao viajam
LOCAIS_POR_TABELA = {'auditoria': {'id'}}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS servidor_local (
  chave TEXT PRIMARY KEY,
  valor TEXT
);
CREATE SEQUENCE IF NOT EXISTS malha_seq;
CREATE TABLE IF NOT EXISTS malha_apagados (
  tabela      TEXT NOT NULL,
  chave       TEXT NOT NULL,
  uid         TEXT,
  alterado_em DOUBLE PRECISION NOT NULL,
  seq         BIGINT NOT NULL,
  PRIMARY KEY (tabela, chave)
);
CREATE INDEX IF NOT EXISTS ix_malha_apagados_seq ON malha_apagados(seq);
CREATE TABLE IF NOT EXISTS malha_cursor (
  par             TEXT PRIMARY KEY,
  seq             BIGINT NOT NULL DEFAULT 0,
  sincronizado_em DOUBLE PRECISION
);

ALTER TABLE auditoria ADD COLUMN IF NOT EXISTS gid TEXT;
UPDATE auditoria SET gid = gen_random_uuid()::text WHERE gid IS NULL;
ALTER TABLE auditoria ALTER COLUMN gid SET DEFAULT gen_random_uuid()::text;
CREATE UNIQUE INDEX IF NOT EXISTS ux_auditoria_gid ON auditoria(gid);

-- Marca cada mudanca. Durante a sincronizacao (argos.malha = '1') a hora que
-- veio do par e mantida; o seq e sempre novo, deste banco.
CREATE OR REPLACE FUNCTION malha_marcar() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.seq IS NOT NULL
     AND (to_jsonb(NEW) - 'seq' - 'alterado_em') = (to_jsonb(OLD) - 'seq' - 'alterado_em') THEN
    NEW.seq := OLD.seq;
    NEW.alterado_em := OLD.alterado_em;
    RETURN NEW;
  END IF;
  IF coalesce(current_setting('argos.malha', true), '') <> '1' OR NEW.alterado_em IS NULL THEN
    NEW.alterado_em := extract(epoch from clock_timestamp());
  END IF;
  NEW.seq := nextval('malha_seq');
  IF TG_OP = 'INSERT' THEN
    DELETE FROM malha_apagados WHERE tabela = TG_TABLE_NAME AND chave = to_jsonb(NEW) ->> TG_ARGV[0];
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION malha_apagou() RETURNS trigger AS $$
DECLARE quando DOUBLE PRECISION;
BEGIN
  IF coalesce(current_setting('argos.malha', true), '') = '1'
     AND coalesce(current_setting('argos.malha_ts', true), '') <> '' THEN
    quando := current_setting('argos.malha_ts')::double precision;
  ELSE
    quando := extract(epoch from clock_timestamp());
  END IF;
  INSERT INTO malha_apagados (tabela, chave, uid, alterado_em, seq)
  VALUES (TG_TABLE_NAME, to_jsonb(OLD) ->> TG_ARGV[0], to_jsonb(OLD) ->> 'uid', quando, nextval('malha_seq'))
  ON CONFLICT (tabela, chave) DO UPDATE
    SET alterado_em = EXCLUDED.alterado_em, seq = EXCLUDED.seq, uid = EXCLUDED.uid;
  RETURN OLD;
END $$ LANGUAGE plpgsql;
"""


def criar_esquema():
    with db.pool().connection() as c:
        c.execute(ESQUEMA)
        for tabela, chave in TABELAS:
            t = sql.Identifier(tabela)
            c.execute(sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS alterado_em DOUBLE PRECISION,'
                              ' ADD COLUMN IF NOT EXISTS seq BIGINT').format(t))
            # linhas de antes da malha: numera uma vez, antes de ligar o gatilho
            c.execute(sql.SQL("UPDATE {} SET alterado_em = coalesce(alterado_em, extract(epoch from now())),"
                              " seq = nextval('malha_seq') WHERE seq IS NULL").format(t))
            c.execute(sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {}(seq)').format(
                sql.Identifier(f'ix_{tabela}_seq'), t))
            for nome, quando, funcao in (('malha_marcar', 'BEFORE INSERT OR UPDATE', 'malha_marcar'),
                                         ('malha_apagou', 'AFTER DELETE', 'malha_apagou')):
                gatilho = sql.Identifier(f'{nome}_{tabela}')
                c.execute(sql.SQL('DROP TRIGGER IF EXISTS {} ON {}').format(gatilho, t))
                c.execute(sql.SQL('CREATE TRIGGER {} ' + quando + ' ON {} FOR EACH ROW EXECUTE FUNCTION {}({})')
                          .format(gatilho, t, sql.Identifier(funcao), sql.Literal(chave)))
    _colunas.clear()


# ── Colunas de cada tabela (lista branca: nome de coluna vindo do par nao vira SQL) ──
_colunas = {}


def colunas(tabela: str) -> dict:
    if tabela not in _colunas:
        linhas = db.consultar("SELECT column_name, data_type FROM information_schema.columns"
                              " WHERE table_schema = current_schema() AND table_name = %s", (tabela,))
        _colunas[tabela] = {l['column_name']: l['data_type'] for l in linhas}
    return _colunas[tabela]


# ── Caminhos de arquivo: viajam relativos a pasta dados/ ────────────
BASE_DADOS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'dados')


def relativo(caminho: str) -> str:
    partes = [p for p in re.split(r'[\\/]+', str(caminho or '')) if p]
    if 'dados' not in partes:
        return ''
    i = len(partes) - 1 - partes[::-1].index('dados')
    return '/'.join(partes[i + 1:])


def absoluto(rel: str) -> str:
    return os.path.join(BASE_DADOS, *[p for p in str(rel or '').split('/') if p])


def rel_seguro(rel: str, uid: str) -> str:
    """Arquivo pedido por um par: so dentro de dados/users/<conta>/ e sem '..'."""
    rel = str(rel or '').replace('\\', '/')
    partes = [p for p in rel.split('/') if p]
    if len(partes) < 3 or partes[0] != 'users' or partes[1] != uid or any(p in ('.', '..') for p in partes):
        return ''
    alvo = os.path.realpath(absoluto(rel))
    base = os.path.realpath(os.path.join(BASE_DADOS, 'users', uid))
    return alvo if alvo.startswith(base + os.sep) and os.path.isfile(alvo) else ''


# ── Exportar (o par pergunta o que mudou) ───────────────────────────

def _empacotar(tabela: str, linha: dict) -> dict:
    fora = LOCAIS | LOCAIS_POR_TABELA.get(tabela, set())
    d = {}
    for k, v in linha.items():
        if k in fora:
            continue
        if isinstance(v, (bytes, bytearray, memoryview)):
            v = {'__b64': base64.b64encode(bytes(v)).decode()}
        d[k] = v
    if tabela in ('epi_fotos', 'func_fotos') and d.get('caminho'):
        d['caminho'] = {'__rel': relativo(d['caminho'])}
    return d


def mudancas(uid: str, desde: int, limite: int = LIMITE) -> dict:
    limite = max(1, min(int(limite or LIMITE), 2000))
    itens = []
    for tabela, chave in TABELAS:
        filtro = FILTRO_CONTA.get(tabela, 'uid = %(uid)s')
        for l in db.consultar(f'SELECT * FROM {tabela} WHERE seq > %(desde)s AND {filtro}'
                              ' ORDER BY seq LIMIT %(lim)s', {'uid': uid, 'desde': int(desde), 'lim': limite}):
            itens.append({'t': tabela, 'op': 'gravar', 'seq': int(l['seq']), 'dados': _empacotar(tabela, l)})
    for l in db.consultar('SELECT * FROM malha_apagados WHERE seq > %(desde)s AND (uid IS NULL OR uid = %(uid)s)'
                          ' ORDER BY seq LIMIT %(lim)s', {'uid': uid, 'desde': int(desde), 'lim': limite}):
        if l['tabela'] in CHAVE:
            itens.append({'t': l['tabela'], 'op': 'apagar', 'seq': int(l['seq']), 'chave': l['chave'],
                          'alterado_em': float(l['alterado_em'])})
    itens.sort(key=lambda x: x['seq'])
    mais = len(itens) > limite
    itens = itens[:limite]
    return {'linhas': itens, 'ate': itens[-1]['seq'] if itens else int(desde), 'mais': mais}


# ── Aplicar (o que veio do par) ─────────────────────────────────────

def _desempacotar(tabela: str, dados: dict, uid: str):
    cols = colunas(tabela)
    fora = LOCAIS | LOCAIS_POR_TABELA.get(tabela, set())
    d = {}
    for k, v in (dados or {}).items():
        if k not in cols or k in fora:
            continue
        if isinstance(v, dict) and '__b64' in v:
            v = base64.b64decode(v['__b64'])
        elif isinstance(v, dict) and '__rel' in v:
            v = absoluto(v['__rel'])
        elif cols[k] in ('jsonb', 'json') and v is not None:
            v = Jsonb(v)
        d[k] = v
    if 'uid' in cols and d.get('uid') != uid:
        return None                          # linha de outra conta: nao entra
    if not d.get(CHAVE[tabela]):
        return None
    return d


def _gravar(c, tabela: str, d: dict) -> bool:
    chave = CHAVE[tabela]
    valor = d[chave]
    chegou = float(d.get('alterado_em') or 0)
    t = sql.Identifier(tabela)
    atual = c.execute(sql.SQL('SELECT alterado_em FROM {} WHERE {} = %s').format(t, sql.Identifier(chave)),
                      (valor,)).fetchone()
    if atual and float(atual['alterado_em'] or 0) >= chegou:
        return False
    apagado = c.execute('SELECT alterado_em FROM malha_apagados WHERE tabela=%s AND chave=%s',
                        (tabela, str(valor))).fetchone()
    if apagado and float(apagado['alterado_em']) >= chegou:
        return False
    nomes = list(d.keys())
    atualizar = [n for n in nomes if n != chave]
    c.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) DO UPDATE SET {}').format(
        t, sql.SQL(', ').join(map(sql.Identifier, nomes)),
        sql.SQL(', ').join(sql.Placeholder() * len(nomes)), sql.Identifier(chave),
        sql.SQL(', ').join(sql.SQL('{0} = EXCLUDED.{0}').format(sql.Identifier(n)) for n in atualizar)),
        [d[n] for n in nomes])
    return True


def _apagar(c, tabela: str, valor: str, quando: float, uid: str) -> bool:
    chave = CHAVE[tabela]
    t = sql.Identifier(tabela)
    atual = c.execute(sql.SQL('SELECT alterado_em FROM {} WHERE {} = %s').format(t, sql.Identifier(chave)),
                      (valor,)).fetchone()
    if atual and float(atual['alterado_em'] or 0) > quando:
        return False                         # mudou aqui depois da exclusao: fica
    c.execute("SELECT set_config('argos.malha_ts', %s, true)", (repr(float(quando)),))
    if atual:
        c.execute(sql.SQL('DELETE FROM {} WHERE {} = %s').format(t, sql.Identifier(chave)), (valor,))
    else:
        # nunca existiu aqui: guarda a marca para nao aceitar a linha velha depois
        c.execute("INSERT INTO malha_apagados (tabela, chave, uid, alterado_em, seq)"
                  " VALUES (%s,%s,%s,%s,nextval('malha_seq')) ON CONFLICT (tabela, chave) DO UPDATE"
                  " SET alterado_em = GREATEST(malha_apagados.alterado_em, EXCLUDED.alterado_em)",
                  (tabela, valor, uid, quando))
    return True


def aplicar(uid: str, itens: list) -> tuple:
    """(tabelas que mudaram, arquivos que faltam, itens que falharam)."""
    mudou, arquivos, falhas = set(), [], []
    # pais primeiro dentro do lote; exclusoes na ordem em que aconteceram
    itens = sorted(itens, key=lambda x: (x.get('op') == 'apagar', ORDEM.get(x.get('t'), 99), x.get('seq', 0)))
    with db.pool().connection() as c:
        with c.transaction():
            c.execute("SELECT set_config('argos.malha', '1', true)")
            for it in itens:
                tabela = it.get('t')
                if tabela not in CHAVE:
                    continue
                try:
                    with c.transaction():        # savepoint: um erro nao derruba o lote
                        if it.get('op') == 'apagar':
                            if _apagar(c, tabela, str(it.get('chave')), float(it.get('alterado_em') or 0), uid):
                                mudou.add(tabela)
                            continue
                        d = _desempacotar(tabela, it.get('dados'), uid)
                        if d is None:
                            continue
                        if _gravar(c, tabela, d):
                            mudou.add(tabela)
                            arquivos.extend(_arquivos_da_linha(tabela, d, uid))
                except Exception as e:
                    falhas.append((it, str(e)))
    return mudou, arquivos, falhas


def _arquivos_da_linha(tabela, d, uid) -> list:
    if tabela in ('epi_fotos', 'func_fotos') and d.get('caminho'):
        return [d['caminho']]
    if tabela == 'auditoria':
        fotos = d.get('fotos')
        fotos = fotos.obj if isinstance(fotos, Jsonb) else (fotos or [])
        return [os.path.join(BASE_DADOS, 'users', uid, 'evidencias', *str(f).split('/')) for f in fotos]
    return []


# ── Laco de sincronizacao ───────────────────────────────────────────
_estado = {}               # par -> {nome, ok, ultimo, erro, linhas, url}
_lock = threading.Lock()
_acordar = threading.Event()
_ao_aplicar = []
_url_boa = {}              # par -> url que respondeu da ultima vez


def ao_aplicar(funcao):
    _ao_aplicar.append(funcao)


def estado() -> dict:
    atuais = {p.get('id') for p in conta.pares()}   # par desvinculado sai da lista
    with _lock:
        pares = [dict(v, id=k) for k, v in _estado.items() if k in atuais]
    return {'pares': pares, 'ultimo': max([p.get('ultimo') or 0 for p in pares] or [0])}


def _cabecalho():
    return {'Authorization': 'Bearer ' + conta.credencial()}


def _urls(par) -> list:
    urls = [u for u in (_url_boa.get(par['id']), par.get('url_local'), par.get('url')) if u]
    return list(dict.fromkeys(urls))


def _pedir(par, caminho, params, stream=False):
    erro = None
    for u in _urls(par):
        try:
            r = requests.get(u.rstrip('/') + caminho, params=params, headers=_cabecalho(),
                             timeout=(3, 30), stream=stream)
            if r.status_code in (401, 403):
                raise RuntimeError(f'o par recusou ({r.status_code})')
            r.raise_for_status()
            _url_boa[par['id']] = u
            return r
        except Exception as e:
            erro = e
    raise RuntimeError(str(erro) if erro else 'par sem endereço')


def _baixar(par, caminhos, uid):
    baixados = 0
    for caminho in dict.fromkeys(caminhos):
        if os.path.exists(caminho):
            continue
        rel = relativo(caminho)
        if not rel:
            continue
        try:
            r = _pedir(par, '/malha/arquivo', {'rel': rel}, stream=True)
            os.makedirs(os.path.dirname(caminho), exist_ok=True)
            tmp = caminho + '.parcial'
            with open(tmp, 'wb') as f:
                for bloco in r.iter_content(65536):
                    f.write(bloco)
            os.replace(tmp, caminho)
            baixados += 1
        except Exception:
            pass
    return baixados


def sincronizar_par(par, uid) -> int:
    l = db.consultar_um('SELECT seq FROM malha_cursor WHERE par=%s', (par['id'],))
    desde = int(l['seq']) if l else 0
    total, pendentes, mudou, ultimo_erro = 0, [], set(), ''
    for _ in range(40):                       # ate 20 mil linhas por rodada
        d = _pedir(par, '/malha/mudancas', {'desde': desde, 'limite': LIMITE}).json()
        itens = d.get('linhas') or []
        # o que falhou na pagina anterior (um filho antes do pai, por exemplo) tenta de novo
        m, arquivos, falhas = aplicar(uid, itens + pendentes)
        pendentes = [it for it, _ in falhas]
        ultimo_erro = falhas[-1][1] if falhas else ''
        mudou |= m
        _baixar(par, arquivos, uid)
        total += len(itens)
        desde = int(d.get('ate') or desde)
        # o cursor nao passa de uma linha que nao entrou: na proxima rodada ela vem de novo
        seguro = min([desde] + [int(it.get('seq') or 0) - 1 for it in pendentes])
        db.executar('INSERT INTO malha_cursor (par, seq, sincronizado_em) VALUES (%s,%s,%s)'
                    ' ON CONFLICT (par) DO UPDATE SET seq=EXCLUDED.seq, sincronizado_em=EXCLUDED.sincronizado_em',
                    (par['id'], seguro, time.time()))
        if not d.get('mais'):
            break
    if mudou:
        for f in list(_ao_aplicar):
            try:
                f(uid, mudou)
            except Exception as e:
                print(f'[malha] aviso falhou: {e}')
    if pendentes:
        raise RuntimeError(f'{len(pendentes)} mudança(s) não aplicada(s): {ultimo_erro[:150]}')
    return total


def _rodada():
    uid = conta.conta_id()
    if not (conta.vinculado() and uid):
        return
    for par in conta.pares():
        if not par.get('online') or not _urls(par):
            continue
        with _lock:
            st = _estado.setdefault(par['id'], {})
            st.update(nome=par.get('nome'))
        try:
            n = sincronizar_par(par, uid)
            with _lock:
                st.update(ok=True, ultimo=time.time(), erro='', linhas=st.get('linhas', 0) + n,
                          url=_url_boa.get(par['id']))
            if n:
                print(f"[malha] {n} mudança(s) recebida(s) de {par.get('nome')}", flush=True)
        except Exception as e:
            with _lock:
                st.update(ok=False, erro=str(e)[:200])
            print(f"[malha] {par.get('nome')}: {e}", flush=True)


def _loop():
    time.sleep(5)
    while True:
        try:
            _rodada()
        except Exception as e:
            print(f'[malha] rodada falhou: {e}')
        _acordar.wait(INTERVALO_S)
        _acordar.clear()


def sincronizar_agora():
    _acordar.set()


def iniciar():
    conta.ao_mudar(sincronizar_agora)
    threading.Thread(target=_loop, daemon=True, name='malha').start()


# ── Quem pode perguntar ─────────────────────────────────────────────

def par_autorizado(token: str):
    """Credencial de outro servidor desta mesma conta? Devolve o id do par ou None."""
    d = conta.ler_token(token)
    if not d or d.get('tipo') != 'servidor' or not conta.vinculado():
        return None
    if d.get('conta') != conta.conta_id() or d.get('sub') == conta.servidor_id():
        return None
    # servidor desvinculado no site sai da lista de pares: deixa de ser atendido
    if d.get('sub') not in {p.get('id') for p in conta.pares()}:
        return None
    return d.get('sub')
