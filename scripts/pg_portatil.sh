# PostgreSQL portatil para Linux/macOS (usado por configurar.sh e iniciar.sh).
# Nao precisa de Docker nem de root: os binarios ficam em bin/pgsql e o banco
# em dados/pgdata (usuario argos, porta 5433, so aceita conexao da propria maquina).
# Os binarios vem do Maven Central (io.zonky embedded-postgres-binaries, ~15 MB).
# Se a plataforma nao tiver binario pronto, usa um PostgreSQL ja instalado.

PG_VERSAO="${PG_VERSAO:-16.15.0}"
PG_PORTA="${PG_PORTA:-5433}"
PG_DIR="$ROOT_DIR/bin/pgsql"
PGDATA="$ROOT_DIR/dados/pgdata"
PG_LOG="$ROOT_DIR/dados/postgres.log"
PG_URL="postgresql://argos:argos@127.0.0.1:${PG_PORTA}/argosepi"

pg_plataforma() {
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64)                 printf "linux-amd64" ;;
    Linux-aarch64|Linux-arm64)    printf "linux-arm64v8" ;;
    Darwin-x86_64)                printf "darwin-amd64" ;;
    Darwin-arm64)                 printf "darwin-arm64v8" ;;
    *) return 1 ;;
  esac
}

# Pasta com pg_ctl/initdb: a portatil ou a de um PostgreSQL instalado.
pg_bin() {
  if [[ -x "$PG_DIR/bin/pg_ctl" ]]; then printf "%s" "$PG_DIR/bin"; return 0; fi
  local p
  p="$(command -v pg_ctl 2>/dev/null || true)"
  if [[ -n "$p" ]]; then dirname "$p"; return 0; fi
  for p in /usr/lib/postgresql/*/bin /usr/pgsql-*/bin /opt/homebrew/opt/postgresql@*/bin /usr/local/opt/postgresql@*/bin; do
    if [[ -x "$p/pg_ctl" ]]; then printf "%s" "$p"; return 0; fi
  done
  return 1
}

pg_baixar() {   # $1 = python do venv
  local plat url jar
  plat="$(pg_plataforma)" || return 1
  url="https://repo1.maven.org/maven2/io/zonky/test/postgres/embedded-postgres-binaries-${plat}/${PG_VERSAO}/embedded-postgres-binaries-${plat}-${PG_VERSAO}.jar"
  jar="$ROOT_DIR/bin/pgsql.jar"
  mkdir -p "$ROOT_DIR/bin"
  if command -v curl >/dev/null 2>&1; then
    curl -L --fail --retry 3 -# -o "$jar" "$url" || return 1
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$jar" "$url" || return 1
  else
    "$1" -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" "$url" "$jar" || return 1
  fi
  rm -rf "$PG_DIR"
  "$1" - "$jar" "$PG_DIR" <<'PY' || return 1
import io, sys, tarfile, zipfile
jar, destino = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(jar) as z:
    nome = next(n for n in z.namelist() if n.endswith('.txz'))
    with tarfile.open(fileobj=io.BytesIO(z.read(nome)), mode='r:xz') as t:
        if hasattr(tarfile, 'tar_filter'):
            t.extractall(destino, filter='tar')
        else:
            t.extractall(destino)
PY
  rm -f "$jar"
  [[ -x "$PG_DIR/bin/pg_ctl" ]]
}

pg_ligado() {   # $1 = pasta dos binarios
  "$1/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1
}

pg_ligar() {    # $1 = pasta dos binarios
  "$1/pg_ctl" -D "$PGDATA" -l "$PG_LOG" -w -t 60 start >/dev/null
}

pg_desligar() { # $1 = pasta dos binarios
  "$1/pg_ctl" -D "$PGDATA" -m fast -w stop >/dev/null 2>&1 || true
}

# Cria dados/pgdata e o banco argosepi. $1 = pasta dos binarios, $2 = python do venv
pg_criar() {
  local pw
  if [[ ! -f "$PGDATA/PG_VERSION" ]]; then
    pw="$(mktemp)"
    printf "argos\n" > "$pw"
    if ! "$1/initdb" -D "$PGDATA" -U argos --pwfile="$pw" -E UTF8 --no-locale -A scram-sha-256 >/dev/null; then
      rm -f "$pw"
      return 1
    fi
    rm -f "$pw"
    {
      echo "port = $PG_PORTA"
      echo "listen_addresses = '127.0.0.1'"
      echo "unix_socket_directories = ''"
    } >> "$PGDATA/postgresql.conf"
  fi
  local liguei=""
  if ! pg_ligado "$1"; then
    pg_ligar "$1" || return 1
    liguei=1
  fi
  "$2" - "$PG_PORTA" <<'PY'
import sys
import psycopg
with psycopg.connect(f'postgresql://argos:argos@127.0.0.1:{sys.argv[1]}/postgres', autocommit=True) as c:
    if not c.execute("SELECT 1 FROM pg_database WHERE datname = 'argosepi'").fetchone():
        c.execute('CREATE DATABASE argosepi')
PY
  local erro=$?
  if [[ -n "$liguei" ]]; then pg_desligar "$1"; fi
  return $erro
}
