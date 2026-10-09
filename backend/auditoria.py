"""
auditoria.py - Historico de eventos por funcionario e evidencias (PostgreSQL)

Guarda cada episodio detectado pelas cameras: violacao de EPI e queda. Os
registros ficam no container do banco; as fotos de evidencia continuam em
disco, e o banco guarda so o caminho relativo.

Um EPISODIO e uma violacao continua: "Joao sem capacete na Solda das 14:02 as
14:07" e uma linha, nao 9000 (uma por quadro). Abre quando o alerta dispara,
fecha quando a violacao some por mais de FIM_EPISODIO_S segundos.

Evidencia: ao abrir o episodio, uma foto do funcionario inteiro (a caixa da
pessoa, com folga), no maximo MAX_FOTOS_EPISODIO por episodio e respeitando
INTERVALO_FOTO_S entre elas. Sao temporarias: limpar_antigas() apaga as que
passaram de RETENCAO_DIAS.
"""
import json
import os
import threading
import time
import uuid

import db
import pastas

# ── Politica de captura e retencao ──────────────────────────────────
FIM_EPISODIO_S = 20.0      # violacao sumiu por mais que isso -> episodio fechado
MAX_FOTOS_EPISODIO = 3     # poucas fotos por ocorrencia, nao um filme
INTERVALO_FOTO_S = 12.0    # espacamento minimo entre fotos do mesmo episodio
RETENCAO_DIAS = 7          # evidencias sao temporarias
RETENCAO_EVENTOS_DIAS = 365
MARGEM_RECORTE = 0.12      # folga ao redor da caixa, para pegar o funcionario todo
# Validacao: falta curta, que o modelo so deduziu (nao achou o EPI) e que terminou com o EPI
# visto de novo na mesma pessoa, e marcada como "provavel engano" e sai dos graficos.
ENGANO_MAX_S = 8.0         # duracao maxima do episodio para ser tratado como engano
ENGANO_VOLTA_S = 8.0       # o EPI precisa reaparecer ate este tempo depois da ultima falta
ENGANO_OK_MIN = 3          # vezes em que o EPI foi visto de novo
# so entra nos graficos e no ranking o que nao foi marcado como engano
VALIDAS = "(validacao IS NULL OR validacao <> 'engano')"

BASE_DIR = pastas.RAIZ
# Fuso usado para separar os dias nos graficos. Antes era 'localtime', que o
# PostgreSQL do Docker (imagem alpine) nao reconhece: a consulta dava erro e a
# pagina de desempenho do funcionario nunca mostrava as faltas registradas.
FUSO = (os.environ.get('ARGOS_FUSO') or os.environ.get('TZ') or 'America/Bahia').strip()
if '/' not in FUSO and FUSO.upper() != 'UTC':   # TZ=UTC0 e afins do Linux: usa o padrao
    FUSO = 'America/Bahia'

_episodios = {}
_ep_lock = threading.Lock()


def dir_evidencias(dados_dir: str, uid: str) -> str:
    d = os.path.join(dados_dir, uid, 'evidencias')
    os.makedirs(d, exist_ok=True)
    return d


# ── Escrita ─────────────────────────────────────────────────────────

def registrar(uid: str, evento: dict) -> int:
    linha = db.inserir_retornando(
        'INSERT INTO auditoria (uid,ts,fim,duracao,tipo,func_id,func_nome,track_id,stream_id,'
        'area_id,area_nome,epi,epi_label,detalhe,fotos,exigidos)'
        ' VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb) RETURNING id',
        (uid, float(evento.get('ts') or time.time()), evento.get('fim'), evento.get('duracao'),
         evento.get('tipo'), evento.get('func_id'), evento.get('func_nome'),
         evento.get('track_id'), evento.get('stream_id'), evento.get('area_id'),
         evento.get('area_nome'), evento.get('epi'), evento.get('epi_label'),
         evento.get('detalhe'), json.dumps(evento.get('fotos') or []),
         json.dumps(evento.get('exigidos')) if evento.get('exigidos') is not None else None))
    return linha['id'] if linha else None


def registrar_aviso(uid: str, rowids, aviso: dict) -> int:
    """A TV falou sobre estas faltas: guarda a frase, a TV, o volume e a hora em cada uma."""
    ids = [int(r) for r in rowids or () if r]
    if not ids:
        return 0
    return db.executar("UPDATE auditoria SET avisos = coalesce(avisos, '[]'::jsonb) || %s::jsonb"
                       ' WHERE uid = %s AND id = ANY(%s)', (json.dumps([aviso]), uid, ids))


def _fechar_linha(uid, rowid, fim, inicio, fotos):
    db.executar('UPDATE auditoria SET fim=%s, duracao=%s, fotos=%s::jsonb WHERE id=%s AND uid=%s',
                (fim, round(max(fim - inicio, 0.0), 2), json.dumps(fotos or []), rowid, uid))


def salvar_evidencia(dados_dir: str, uid: str, img, box) -> str:
    """Recorta a pessoa inteira e grava o JPEG. Devolve o caminho relativo ou ''."""
    try:
        import cv2
    except ImportError:
        return ''
    if img is None:
        return ''
    try:
        h, w = img.shape[:2]
        x1, y1, x2, y2 = [float(v) for v in box]
        mx, my = (x2 - x1) * MARGEM_RECORTE, (y2 - y1) * MARGEM_RECORTE
        x1, y1 = int(max(x1 - mx, 0)), int(max(y1 - my, 0))
        x2, y2 = int(min(x2 + mx, w)), int(min(y2 + my, h))
        if x2 - x1 < 12 or y2 - y1 < 12:
            return ''
        dia = time.strftime('%Y-%m-%d')
        pasta = os.path.join(dir_evidencias(dados_dir, uid), dia)
        os.makedirs(pasta, exist_ok=True)
        nome = uuid.uuid4().hex[:16] + '.jpg'
        cv2.imwrite(os.path.join(pasta, nome), img[y1:y2, x1:x2], [cv2.IMWRITE_JPEG_QUALITY, 82])
        return dia + '/' + nome
    except Exception:
        return ''


def episodio(dados_dir, uid, *, stream_id, chave_pessoa, tipo, epi=None, epi_label=None,
             func_id=None, func_nome=None, track_id=None, area_id=None, area_nome=None,
             detalhe=None, img=None, box=None, t=None, forte=False, exigidos=None):
    """Abre ou mantem um episodio. Chamar a cada quadro-chave em que a violacao existe."""
    t = time.time() if t is None else t
    chave = (uid, stream_id, str(chave_pessoa), tipo, epi or '')
    with _ep_lock:
        est = _episodios.get(chave)
        novo = est is None or (t - est['visto']) > FIM_EPISODIO_S
        if novo:
            if est is not None:
                _encerrar(est)
            est = {'uid': uid, 'dados_dir': dados_dir, 'inicio': t, 'visto': t,
                   'fotos': [], 'ultima_foto': 0.0, 'rowid': None, 'func_id': func_id,
                   'tipo': tipo, 'quadros': 0, 'fortes': 0, 'ok_depois': 0, 'ok_em': None}
            _episodios[chave] = est
            est['rowid'] = registrar(uid, {
                'ts': t, 'tipo': tipo, 'func_id': func_id, 'func_nome': func_nome,
                'track_id': str(track_id) if track_id is not None else None,
                'stream_id': stream_id, 'area_id': area_id, 'area_nome': area_nome,
                'epi': epi, 'epi_label': epi_label, 'detalhe': detalhe, 'fotos': [],
                'exigidos': exigidos})
        est['visto'] = t
        # forte = a ausencia foi vista de fato (classe sem_capacete...), nao so deduzida
        est['quadros'] += 1
        est['fortes'] += 1 if forte else 0
        est['ok_depois'], est['ok_em'] = 0, None
        # o alerta costuma disparar antes de o rosto ser reconhecido: o episodio
        # abria sem dono e a falta nunca chegava ao desempenho do funcionario.
        # Quando a identidade aparece no mesmo episodio, a linha ganha o dono.
        dar_dono = bool(func_id) and not est.get('func_id') and est.get('rowid')
        if dar_dono:
            est['func_id'] = func_id
        pode_fotografar = (img is not None and box is not None
                           and len(est['fotos']) < MAX_FOTOS_EPISODIO
                           and (t - est['ultima_foto']) >= INTERVALO_FOTO_S)
    if dar_dono:
        try:
            db.executar('UPDATE auditoria SET func_id=%s, func_nome=%s WHERE id=%s AND uid=%s AND func_id IS NULL',
                        (func_id, func_nome, est['rowid'], uid))
        except Exception:
            pass
    # o recorte roda fora do lock: e I/O e nao deve travar a thread de analise
    if pode_fotografar:
        rel = salvar_evidencia(dados_dir, uid, img, box)
        if rel:
            with _ep_lock:
                est['fotos'].append(rel)
                est['ultima_foto'] = t
            try:
                _fechar_linha(uid, est['rowid'], t, est['inicio'], est['fotos'])
            except Exception:
                pass
    return est


def viu_com_epi(uid, stream_id, chave_pessoa, itens, t=None):
    """A pessoa esta com estes EPIs agora. So interessa a quem tem episodio aberto do mesmo EPI."""
    t = time.time() if t is None else t
    with _ep_lock:
        for item in itens:
            est = _episodios.get((uid, stream_id, str(chave_pessoa), 'violacao', item or ''))
            if est is not None and t > est['visto']:
                est['ok_depois'] += 1
                if est['ok_em'] is None:
                    est['ok_em'] = t


def avaliar(est):
    """(validacao, motivo) de um episodio que terminou. ('', '') = falta normal."""
    if est.get('tipo') != 'violacao' or not est.get('quadros'):
        return '', ''
    dur = max(est['visto'] - est['inicio'], 0.0)
    voltou = (est['ok_depois'] >= ENGANO_OK_MIN and est['ok_em'] is not None
              and est['ok_em'] - est['visto'] <= ENGANO_VOLTA_S)
    deduzida = est['fortes'] * 2 < est['quadros']
    if dur <= ENGANO_MAX_S and voltou and deduzida:
        return 'engano', (f"Falta de {dur:.0f} s em que o modelo só não achou o EPI "
                          f"({est['quadros'] - est['fortes']} de {est['quadros']} quadros) e o EPI "
                          f"apareceu de novo {max(est['ok_em'] - est['visto'], 0):.0f} s depois.")
    return '', ''


def _encerrar(est):
    if est and est.get('rowid'):
        try:
            _fechar_linha(est['uid'], est['rowid'], est['visto'], est['inicio'], est['fotos'])
            validacao, motivo = avaliar(est)
            if validacao:     # quem ja decidiu na mao (validacao preenchida) nao e contrariado
                db.executar('UPDATE auditoria SET validacao=%s, validacao_motivo=%s'
                            ' WHERE id=%s AND uid=%s AND validacao IS NULL',
                            (validacao, motivo, est['rowid'], est['uid']))
        except Exception:
            pass


def validar(uid: str, evento_id: int, engano: bool) -> bool:
    """Decisao de quem usa o painel: 'foi engano' ou 'foi falta mesmo'. Vale sobre a do sistema."""
    return db.executar('UPDATE auditoria SET validacao=%s, validacao_motivo=%s WHERE id=%s AND uid=%s',
                       ('engano' if engano else 'confirmada', 'Marcado no painel.', int(evento_id), uid)) > 0


def encerrar_vencidos(t=None):
    """Fecha episodios cuja violacao sumiu. Chamar periodicamente."""
    t = time.time() if t is None else t
    with _ep_lock:
        vencidos = [(k, v) for k, v in _episodios.items() if (t - v['visto']) > FIM_EPISODIO_S]
        for k, _v in vencidos:
            _episodios.pop(k, None)
    for _k, v in vencidos:
        _encerrar(v)
    return len(vencidos)


def encerrar_stream(uid: str, stream_id: str):
    with _ep_lock:
        alvos = [(k, v) for k, v in _episodios.items() if k[0] == uid and k[1] == stream_id]
        for k, _v in alvos:
            _episodios.pop(k, None)
    for _k, v in alvos:
        _encerrar(v)


# ── Leitura ─────────────────────────────────────────────────────────

def _linha(l) -> dict:
    d = dict(l)
    fotos = d.get('fotos')
    if isinstance(fotos, str):
        try:
            fotos = json.loads(fotos or '[]')
        except ValueError:
            fotos = []
    d['fotos'] = fotos or []
    for campo in ('exigidos', 'avisos'):
        v = d.get(campo)
        if isinstance(v, str):
            try:
                v = json.loads(v or '[]')
            except ValueError:
                v = []
        d[campo] = v or []
    # episodio ainda aberto: a pessoa continua sem o EPI (a linha fecha quando a falta some)
    d['em_andamento'] = d.get('tipo') == 'violacao' and _aberto(d)
    return d


def _aberto(d) -> bool:
    with _ep_lock:
        return any(e.get('rowid') == d.get('id') for e in _episodios.values())


def eventos(uid: str, func_id=None, tipo=None, desde=None, ate=None,
            stream_id=None, area_id=None, limite=200, offset=0) -> list:
    onde, args = ['uid = %s'], [uid]
    for col, val in (('func_id', func_id), ('tipo', tipo), ('stream_id', stream_id), ('area_id', area_id)):
        if val:
            onde.append(f'{col} = %s'); args.append(val)
    if desde is not None:
        onde.append('ts >= %s'); args.append(float(desde))
    if ate is not None:
        onde.append('ts <= %s'); args.append(float(ate))
    args += [int(limite), int(offset)]
    return [_linha(l) for l in db.consultar(
        'SELECT * FROM auditoria WHERE ' + ' AND '.join(onde) +
        ' ORDER BY ts DESC LIMIT %s OFFSET %s', args)]


def contar(uid: str, **filtros) -> int:
    onde, args = ['uid = %s'], [uid]
    for col in ('func_id', 'tipo', 'stream_id', 'area_id'):
        if filtros.get(col):
            onde.append(f'{col} = %s'); args.append(filtros[col])
    if filtros.get('desde') is not None:
        onde.append('ts >= %s'); args.append(float(filtros['desde']))
    l = db.consultar_um('SELECT COUNT(*) AS n FROM auditoria WHERE ' + ' AND '.join(onde), args)
    return int(l['n']) if l else 0


def _inicio_do_periodo(dias: int) -> float:
    """Meia-noite (no fuso do servidor) de dias-1 atras: "7 dias" = hoje e os 6
    dias anteriores inteiros, igual aos pontos do grafico."""
    try:
        import datetime
        from zoneinfo import ZoneInfo
        agora = datetime.datetime.now(ZoneInfo(FUSO))
        inicio = (agora - datetime.timedelta(days=max(int(dias), 1) - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return inicio.timestamp()
    except Exception:          # sem a base de fusos (tzdata): janela corrida
        return time.time() - dias * 86400


def desempenho(uid: str, func_id=None, dias=30) -> dict:
    """Dados dos graficos de performance do funcionario."""
    desde = _inicio_do_periodo(dias)
    onde, args = ["uid = %s", "tipo = 'violacao'", 'ts >= %s'], [uid, desde]
    if func_id:
        onde.append('func_id = %s'); args.append(func_id)
    # faltas que a validacao tratou como engano do sistema: ficam no historico, fora dos graficos
    l = db.consultar_um('SELECT COUNT(*) AS n FROM auditoria WHERE ' + ' AND '.join(onde) +
                        " AND validacao = 'engano'", args)
    enganos = int(l['n']) if l else 0
    onde.append(VALIDAS)
    filtro = ' WHERE ' + ' AND '.join(onde)

    por_dia = [{'dia': str(l['dia']), 'violacoes': int(l['n']), 'segundos': round(float(l['s'] or 0), 1)}
               for l in db.consultar(
                   "SELECT to_char(to_timestamp(ts) AT TIME ZONE %s, 'YYYY-MM-DD') AS dia,"
                   ' COUNT(*) AS n, SUM(duracao) AS s FROM auditoria' + filtro +
                   ' GROUP BY dia ORDER BY dia', [FUSO] + args)]
    por_epi = [{'epi': l['epi'], 'label': l['epi_label'] or l['epi'], 'violacoes': int(l['n']),
                'segundos': round(float(l['s'] or 0), 1)}
               for l in db.consultar(
                   'SELECT epi, epi_label, COUNT(*) AS n, SUM(duracao) AS s FROM auditoria' + filtro +
                   ' GROUP BY epi, epi_label ORDER BY n DESC, epi', args)]
    por_area = [{'area_id': l['area_id'], 'area': l['area_nome'] or 'Sem área', 'violacoes': int(l['n'])}
                for l in db.consultar(
                    'SELECT area_id, area_nome, COUNT(*) AS n FROM auditoria' + filtro +
                    ' GROUP BY area_id, area_nome ORDER BY n DESC, area_nome', args)]
    # a que horas do dia as faltas acontecem (0 a 23h, no fuso do servidor)
    por_hora = [0] * 24
    for l in db.consultar('SELECT EXTRACT(HOUR FROM to_timestamp(ts) AT TIME ZONE %s)::int AS h,'
                          ' COUNT(*) AS n FROM auditoria' + filtro + ' GROUP BY h', [FUSO] + args):
        por_hora[int(l['h']) % 24] = int(l['n'])
    tot = db.consultar_um('SELECT COUNT(*) AS n, SUM(duracao) AS s FROM auditoria' + filtro, args)
    total = int(tot['n']) if tot else 0
    seg = float(tot['s'] or 0.0) if tot else 0.0

    dias_obs = max(len(por_dia), 1)
    media = total / dias_obs
    # 0 violacoes/dia = 100; 5+ violacoes/dia = 0. Escala simples e legivel.
    conformidade = round(max(0.0, min(100.0, 100.0 - media * 20.0)), 1)

    # Violacao vista pela camera mas sem dono: o rosto nao foi reconhecido naquele
    # episodio. Sem este numero a ficha de quem nunca foi identificado mostra
    # "tudo certo" enquanto o sistema registrou gente sem EPI no mesmo periodo.
    l = db.consultar_um("SELECT COUNT(*) AS n FROM auditoria WHERE uid = %s AND tipo = 'violacao'"
                        ' AND ts >= %s AND func_id IS NULL AND ' + VALIDAS, (uid, desde))
    nao_atribuidas = int(l['n']) if l else 0
    l = db.consultar_um("SELECT COUNT(*) AS n FROM auditoria WHERE uid = %s AND tipo = 'violacao'"
                        ' AND ts >= %s AND ' + VALIDAS, (uid, desde))
    violacoes_no_periodo = int(l['n']) if l else 0

    return {'dias': dias, 'total_violacoes': total, 'segundos_em_violacao': round(seg, 1),
            'media_por_dia': round(media, 2), 'indice_conformidade': conformidade,
            'dias_observados': len(por_dia), 'sem_registros': total == 0,
            'nao_atribuidas': nao_atribuidas, 'violacoes_no_periodo': violacoes_no_periodo,
            'enganos_descartados': enganos,
            'por_dia': por_dia, 'por_epi': por_epi, 'por_area': por_area,
            'por_hora': [{'hora': h, 'violacoes': n} for h, n in enumerate(por_hora)]}


def ranking(uid: str, dias=30, limite=20) -> list:
    desde = time.time() - dias * 86400
    return [{'func_id': l['func_id'], 'nome': l['func_nome'] or 'Não identificado',
             'violacoes': int(l['n']), 'segundos': round(float(l['s'] or 0), 1)}
            for l in db.consultar(
                "SELECT func_id, func_nome, COUNT(*) AS n, SUM(duracao) AS s FROM auditoria"
                " WHERE uid=%s AND tipo='violacao' AND ts >= %s AND " + VALIDAS +
                ' GROUP BY func_id, func_nome ORDER BY n DESC LIMIT %s', (uid, desde, int(limite)))]


def caminho_evidencia(dados_dir: str, uid: str, relativo: str):
    """Caminho absoluto seguro de uma evidencia (bloqueia ../)."""
    base = os.path.realpath(dir_evidencias(dados_dir, uid))
    alvo = os.path.realpath(os.path.join(base, relativo or ''))
    if not alvo.startswith(base + os.sep):
        return None
    return alvo if os.path.exists(alvo) else None


# ── Limpeza ─────────────────────────────────────────────────────────

def limpar_antigas(dados_dir: str, uid: str, dias=RETENCAO_DIAS) -> int:
    """Apaga evidencias vencidas e eventos muito velhos."""
    limite = time.time() - dias * 86400
    apagadas = 0
    base = dir_evidencias(dados_dir, uid)
    for dia in (os.listdir(base) if os.path.isdir(base) else []):
        pasta = os.path.join(base, dia)
        if not os.path.isdir(pasta):
            continue
        try:
            quando = time.mktime(time.strptime(dia, '%Y-%m-%d'))
        except ValueError:
            quando = os.path.getmtime(pasta)
        if quando >= limite:
            continue
        for nome in os.listdir(pasta):
            try:
                os.remove(os.path.join(pasta, nome)); apagadas += 1
            except OSError:
                pass
        try:
            os.rmdir(pasta)
        except OSError:
            pass
    try:
        # a linha do evento continua; so a referencia a foto apagada sai
        db.executar("UPDATE auditoria SET fotos='[]'::jsonb WHERE uid=%s AND fotos <> '[]'::jsonb AND ts < %s",
                    (uid, limite))
        db.executar('DELETE FROM auditoria WHERE uid=%s AND ts < %s',
                    (uid, time.time() - RETENCAO_EVENTOS_DIAS * 86400))
    except Exception:
        pass
    return apagadas
