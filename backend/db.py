"""
db.py - Acesso ao PostgreSQL do Argos EPI

O banco vive num container proprio (servico "db" do docker-compose). Aqui ficam
a conexao, o esquema e os utilitarios; quem usa nao escreve SQL de conexao.

O que vai para o banco: contas, sessoes, EPIs, funcionarios (com os embeddings
de rosto), modelos, areas, zonas, configuracao de camera e a auditoria.
O que continua em disco: os binarios grandes -- fotos, arquivos .pt e as
evidencias em JPEG. O banco guarda o caminho, nao o arquivo. Misturar blob
grande com dado relacional so incha o banco e atrapalha backup.
"""
import os
import threading
import time

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# Dentro do compose o endereco vem pronto no ambiente. Fora dele (iniciar.bat)
# vale o padrao, que aponta para o container do banco publicado em localhost.
PADRAO = 'postgresql://argos:argos@localhost:5432/argosepi'
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip() or PADRAO

_pool = None
_pool_lock = threading.Lock()

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
  uid         TEXT PRIMARY KEY,
  nome        TEXT NOT NULL,
  doc         TEXT UNIQUE NOT NULL,
  telefone    TEXT DEFAULT '',
  email       TEXT UNIQUE NOT NULL,
  setor       TEXT DEFAULT '',
  senha_hash  TEXT NOT NULL,
  role        TEXT NOT NULL DEFAULT 'user',
  blocked     BOOLEAN NOT NULL DEFAULT FALSE,
  restricoes  JSONB NOT NULL DEFAULT '[]'::jsonb,
  criado_em   DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS sessoes (
  token      TEXT PRIMARY KEY,
  uid        TEXT NOT NULL REFERENCES usuarios(uid) ON DELETE CASCADE,
  criado_em  DOUBLE PRECISION NOT NULL,
  visto_em   DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessoes_uid ON sessoes(uid);

CREATE TABLE IF NOT EXISTS eventos_conta (
  id        BIGSERIAL PRIMARY KEY,
  uid       TEXT NOT NULL REFERENCES usuarios(uid) ON DELETE CASCADE,
  ts        DOUBLE PRECISION NOT NULL,
  tipo      TEXT,
  descricao TEXT
);
CREATE INDEX IF NOT EXISTS ix_eventos_conta_uid ON eventos_conta(uid, ts DESC);

CREATE TABLE IF NOT EXISTS cam_tokens (
  token     TEXT PRIMARY KEY,
  uid       TEXT NOT NULL REFERENCES usuarios(uid) ON DELETE CASCADE,
  nome      TEXT,
  criado_em DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cam_tokens_uid ON cam_tokens(uid);

CREATE TABLE IF NOT EXISTS epis (
  id              TEXT PRIMARY KEY,
  uid             TEXT NOT NULL REFERENCES usuarios(uid) ON DELETE CASCADE,
  nome            TEXT NOT NULL,
  descricao       TEXT DEFAULT '',
  cor             TEXT DEFAULT '',
  modelo_treinado BOOLEAN NOT NULL DEFAULT FALSE,
  extra           JSONB NOT NULL DEFAULT '{}'::jsonb,
  criado_em       DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_epis_uid ON epis(uid);

CREATE TABLE IF NOT EXISTS epi_fotos (
  id      TEXT PRIMARY KEY,
  epi_id  TEXT NOT NULL REFERENCES epis(id) ON DELETE CASCADE,
  caminho TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_epi_fotos_epi ON epi_fotos(epi_id);

CREATE TABLE IF NOT EXISTS funcionarios (
  id        TEXT PRIMARY KEY,
  uid       TEXT NOT NULL REFERENCES usuarios(uid) ON DELETE CASCADE,
  nome      TEXT NOT NULL,
  cargo     TEXT DEFAULT '',
  matricula TEXT DEFAULT '',
  criado_em DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_funcionarios_uid ON funcionarios(uid);

-- Uma foto de rosto do funcionario. O embedding e o vetor ArcFace de 512
-- dimensoes (float32) ja normalizado, guardado cru: a galeria tem dezenas de
-- linhas, entao comparar em memoria e instantaneo e dispensa extensao.
CREATE TABLE IF NOT EXISTS func_fotos (
  id          TEXT PRIMARY KEY,
  func_id     TEXT NOT NULL REFERENCES funcionarios(id) ON DELETE CASCADE,
  caminho     TEXT NOT NULL,
  embedding   BYTEA,
  dim         INTEGER,
  extraido_em DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS ix_func_fotos_func ON func_fotos(func_id);

CREATE TABLE IF NOT EXISTS modelos (
  uid       TEXT NOT NULL REFERENCES usuarios(uid) ON DELETE CASCADE,
  nome      TEXT NOT NULL,
  dados     JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (uid, nome)
);

CREATE TABLE IF NOT EXISTS areas (
  id                TEXT PRIMARY KEY,
  uid               TEXT NOT NULL REFERENCES usuarios(uid) ON DELETE CASCADE,
  nome              TEXT NOT NULL,
  descricao         TEXT DEFAULT '',
  cor               TEXT DEFAULT '',
  epis_obrigatorios JSONB NOT NULL DEFAULT '[]'::jsonb,
  alarme            BOOLEAN NOT NULL DEFAULT TRUE,
  criado_em         DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_areas_uid ON areas(uid);

CREATE TABLE IF NOT EXISTS zonas (
  id        TEXT PRIMARY KEY,
  uid       TEXT NOT NULL REFERENCES usuarios(uid) ON DELETE CASCADE,
  stream_id TEXT NOT NULL,
  area_id   TEXT REFERENCES areas(id) ON DELETE CASCADE,
  pontos    JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_zonas_stream ON zonas(uid, stream_id);

CREATE TABLE IF NOT EXISTS cameras (
  uid       TEXT NOT NULL REFERENCES usuarios(uid) ON DELETE CASCADE,
  stream_id TEXT NOT NULL,
  config    JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (uid, stream_id)
);

CREATE TABLE IF NOT EXISTS auditoria (
  id        BIGSERIAL PRIMARY KEY,
  uid       TEXT NOT NULL REFERENCES usuarios(uid) ON DELETE CASCADE,
  ts        DOUBLE PRECISION NOT NULL,
  fim       DOUBLE PRECISION,
  duracao   DOUBLE PRECISION,
  tipo      TEXT NOT NULL,
  func_id   TEXT,
  func_nome TEXT,
  track_id  TEXT,
  stream_id TEXT,
  area_id   TEXT,
  area_nome TEXT,
  epi       TEXT,
  epi_label TEXT,
  detalhe   TEXT,
  fotos     JSONB NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_auditoria_func ON auditoria(uid, func_id, ts DESC);
CREATE INDEX IF NOT EXISTS ix_auditoria_ts   ON auditoria(uid, ts DESC);
CREATE INDEX IF NOT EXISTS ix_auditoria_tipo ON auditoria(uid, tipo, ts DESC);
"""


def configurado() -> bool:
    return bool(DATABASE_URL)


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                if not DATABASE_URL:
                    raise RuntimeError(
                        'DATABASE_URL nao definido: o backend precisa do container do Postgres.')
                _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=10, timeout=15,
                                       kwargs={'row_factory': dict_row}, open=True)
    return _pool


def esperar_banco(tentativas=30, intervalo=2.0):
    """O backend costuma subir antes do Postgres aceitar conexao."""
    ultimo = None
    for i in range(tentativas):
        try:
            with pool().connection() as c:
                c.execute('SELECT 1')
            return True
        except Exception as e:
            ultimo = e
            if i == 0:
                print('[db] aguardando o PostgreSQL...')
            time.sleep(intervalo)
    raise RuntimeError(f'PostgreSQL nao respondeu: {ultimo}')


def criar_esquema():
    with pool().connection() as c:
        c.execute(ESQUEMA)
    print('[db] esquema pronto')


def consultar(sql, args=()) -> list:
    with pool().connection() as c:
        return c.execute(sql, args).fetchall()


def consultar_um(sql, args=()):
    linhas = consultar(sql, args)
    return linhas[0] if linhas else None


def executar(sql, args=()) -> int:
    with pool().connection() as c:
        cur = c.execute(sql, args)
        return cur.rowcount


def inserir_retornando(sql, args=()):
    with pool().connection() as c:
        linha = c.execute(sql, args).fetchone()
        return linha


def fechar():
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
