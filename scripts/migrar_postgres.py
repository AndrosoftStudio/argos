"""
migrar_postgres.py - Leva os dados antigos (SQLite + JSON) para o PostgreSQL.

Antes: dados/index.db, dados/users/<uid>/user.db e um punhado de arquivos JSON
por usuario. Agora: tudo no container do banco.

O script so LE os arquivos antigos -- nada e apagado nem alterado. Pode rodar
mais de uma vez: cada linha entra com ON CONFLICT DO NOTHING/UPDATE, entao
repetir nao duplica. Depois de conferir, os arquivos antigos podem ser
guardados como backup.

  docker compose --profile gpu run --rm backend-gpu python scripts/migrar_postgres.py
  docker compose --profile gpu run --rm backend-gpu python scripts/migrar_postgres.py --rostos
"""
import json
import os
import sqlite3
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'backend'))

import db  # noqa: E402

DADOS = os.path.join(BASE, 'dados', 'users')
INDEX_DB = os.path.join(BASE, 'dados', 'index.db')


def _sqlite(caminho):
    if not os.path.exists(caminho):
        return None
    c = sqlite3.connect(caminho)
    c.row_factory = sqlite3.Row
    return c


def _json(caminho, padrao):
    try:
        with open(caminho, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return padrao


def _tabelas(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def migrar_usuarios() -> list:
    """Devolve os uids migrados."""
    idx = _sqlite(INDEX_DB)
    if idx is None:
        print('  index.db nao encontrado: nada de contas para migrar')
        return []
    uids = []
    for linha in idx.execute('SELECT uid, doc, email, token FROM idx'):
        uid = linha['uid']
        ucon = _sqlite(os.path.join(DADOS, uid, 'user.db'))
        if ucon is None:
            print(f'  {uid[:8]}: sem user.db, pulando')
            continue
        tabs = _tabelas(ucon)
        p = ucon.execute('SELECT * FROM profile WHERE uid=?', (uid,)).fetchone() if 'profile' in tabs else None
        if not p:
            print(f'  {uid[:8]}: sem perfil, pulando')
            continue
        db.executar(
            'INSERT INTO usuarios (uid,nome,doc,telefone,email,setor,senha_hash,role,blocked,restricoes,criado_em)'
            ' VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (uid) DO NOTHING',
            (uid, p['nome'], p['doc'] or linha['doc'], p['telefone'] or '', p['email'] or linha['email'],
             p['setor'] or '', p['senha_hash'], p['role'] or 'user', bool(p['blocked']),
             p['restricoes'] or '[]', float(p['created_at'] or time.time())))
        uids.append(uid)

        # o token ativo do index vira uma sessao
        if linha['token']:
            db.executar('INSERT INTO sessoes (token, uid, criado_em, visto_em) VALUES (%s,%s,%s,%s)'
                        ' ON CONFLICT (token) DO NOTHING',
                        (linha['token'], uid, time.time(), time.time()))
        if 'sessions' in tabs:
            for s in ucon.execute('SELECT * FROM sessions'):
                db.executar('INSERT INTO sessoes (token, uid, criado_em, visto_em) VALUES (%s,%s,%s,%s)'
                            ' ON CONFLICT (token) DO NOTHING',
                            (s['token'], uid, float(s['created_at'] or 0), float(s['last_seen'] or 0)))
        if 'events' in tabs:
            for e in ucon.execute('SELECT * FROM events'):
                db.executar('INSERT INTO eventos_conta (uid, ts, tipo, descricao) VALUES (%s,%s,%s,%s)',
                            (uid, float(e['ts'] or 0), e['tipo'], e['descricao']))
        if 'cam_tokens' in tabs:
            for t in ucon.execute('SELECT * FROM cam_tokens'):
                db.executar('INSERT INTO cam_tokens (token, uid, nome, criado_em) VALUES (%s,%s,%s,%s)'
                            ' ON CONFLICT (token) DO NOTHING',
                            (t['token'], uid, t['nome'], float(t['created_at'] or 0)))
        ucon.close()
    idx.close()
    return uids


def migrar_epis(uid):
    n = 0
    for e in _json(os.path.join(DADOS, uid, 'epis_meta.json'), []):
        if not isinstance(e, dict) or not e.get('id'):
            continue
        conhecidos = {'id', 'nome', 'descricao', 'cor', 'fotos', 'modelo_treinado', 'criado_em'}
        extra = {k: v for k, v in e.items() if k not in conhecidos}
        db.executar('INSERT INTO epis (id,uid,nome,descricao,cor,modelo_treinado,extra,criado_em)'
                    ' VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (id) DO NOTHING',
                    (e['id'], uid, e.get('nome') or '', e.get('descricao') or '', e.get('cor') or '',
                     bool(e.get('modelo_treinado')), json.dumps(extra),
                     float(e.get('criado_em') or time.time())))
        for f in e.get('fotos') or []:
            if f.get('id'):
                db.executar('INSERT INTO epi_fotos (id, epi_id, caminho) VALUES (%s,%s,%s)'
                            ' ON CONFLICT (id) DO NOTHING', (f['id'], e['id'], f.get('path') or ''))
        n += 1
    return n


def migrar_funcionarios(uid):
    n = 0
    for f in _json(os.path.join(DADOS, uid, 'funcionarios_meta.json'), []):
        if not isinstance(f, dict) or not f.get('id'):
            continue
        db.executar('INSERT INTO funcionarios (id,uid,nome,cargo,matricula,criado_em)'
                    ' VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING',
                    (f['id'], uid, f.get('nome') or '', f.get('cargo') or '',
                     f.get('matricula') or '', float(f.get('criado_em') or time.time())))
        for x in f.get('fotos_rosto') or []:
            if x.get('id'):
                db.executar('INSERT INTO func_fotos (id, func_id, caminho) VALUES (%s,%s,%s)'
                            ' ON CONFLICT (id) DO NOTHING', (x['id'], f['id'], x.get('path') or ''))
        n += 1
    return n


def migrar_modelos(uid):
    meta = _json(os.path.join(DADOS, uid, 'models_meta.json'), {})
    if not isinstance(meta, dict):
        return 0
    for nome, rec in meta.items():
        db.executar('INSERT INTO modelos (uid, nome, dados) VALUES (%s,%s,%s::jsonb)'
                    ' ON CONFLICT (uid, nome) DO NOTHING', (uid, nome, json.dumps(rec or {})))
    return len(meta)


def migrar_areas_e_zonas(uid):
    areas = _json(os.path.join(DADOS, uid, 'areas.json'), [])
    for a in areas:
        if not isinstance(a, dict) or not a.get('id'):
            continue
        db.executar('INSERT INTO areas (id,uid,nome,descricao,cor,epis_obrigatorios,alarme,criado_em)'
                    ' VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s) ON CONFLICT (id) DO NOTHING',
                    (a['id'], uid, a.get('nome') or '', a.get('descricao') or '', a.get('cor') or '',
                     json.dumps(a.get('epis_obrigatorios') or []), bool(a.get('alarme', True)),
                     float(a.get('criado_em') or time.time())))
    zonas = _json(os.path.join(DADOS, uid, 'zonas.json'), {})
    nz = 0
    validas = {a['id'] for a in areas if isinstance(a, dict) and a.get('id')}
    for sid, lista in (zonas or {}).items():
        for z in lista or []:
            if not z.get('id') or z.get('area_id') not in validas:
                continue
            db.executar('INSERT INTO zonas (id,uid,stream_id,area_id,pontos) VALUES (%s,%s,%s,%s,%s::jsonb)'
                        ' ON CONFLICT (id) DO NOTHING',
                        (z['id'], uid, sid, z['area_id'], json.dumps(z.get('pontos') or [])))
            nz += 1
    return len(areas), nz


def migrar_cameras(uid):
    cams = _json(os.path.join(DADOS, uid, 'cameras.json'), {})
    if not isinstance(cams, dict):
        return 0
    for sid, cfg in cams.items():
        db.executar('INSERT INTO cameras (uid, stream_id, config) VALUES (%s,%s,%s::jsonb)'
                    ' ON CONFLICT (uid, stream_id) DO NOTHING', (uid, sid, json.dumps(cfg or {})))
    return len(cams)


def migrar_auditoria(uid):
    con = _sqlite(os.path.join(DADOS, uid, 'auditoria.db'))
    if con is None:
        return 0
    if 'eventos' not in _tabelas(con):
        con.close()
        return 0
    n = 0
    for e in con.execute('SELECT * FROM eventos'):
        fotos = [x for x in (e['fotos'] or '').split(',') if x]
        db.executar(
            'INSERT INTO auditoria (uid,ts,fim,duracao,tipo,func_id,func_nome,track_id,stream_id,'
            'area_id,area_nome,epi,epi_label,detalhe,fotos)'
            ' VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)',
            (uid, e['ts'], e['fim'], e['duracao'], e['tipo'], e['func_id'], e['func_nome'],
             e['track_id'], e['stream_id'], e['area_id'], e['area_nome'], e['epi'],
             e['epi_label'], e['detalhe'], json.dumps(fotos)))
        n += 1
    con.close()
    return n


def extrair_rostos():
    """Calcula o embedding das fotos de rosto que ainda nao tem. Opcional e lento."""
    import face_id
    if not face_id.disponivel():
        print('  insightface indisponivel; rostos ficam para depois')
        return
    pend = db.consultar('SELECT id, caminho FROM func_fotos WHERE embedding IS NULL')
    print(f'  {len(pend)} foto(s) de rosto para processar')
    ok = falhou = 0
    for f in pend:
        emb, motivo = face_id.embedding_de_foto(f['caminho'], os.environ.get('ARGOS_DEVICE', 'cpu'))
        if emb is None:
            falhou += 1
            print(f'    sem rosto em {os.path.basename(f["caminho"])}: {motivo}')
            continue
        db.executar('UPDATE func_fotos SET embedding=%s, dim=%s, extraido_em=%s WHERE id=%s',
                    (emb.tobytes(), int(emb.size), time.time(), f['id']))
        ok += 1
    print(f'  rostos: {ok} extraidos, {falhou} sem rosto reconhecivel')


def main():
    print('=' * 60)
    print('  Migracao para PostgreSQL — os arquivos antigos nao sao tocados')
    print('=' * 60)
    db.esperar_banco()
    db.criar_esquema()

    uids = migrar_usuarios()
    print(f'\ncontas migradas: {len(uids)}')
    for uid in uids:
        e = migrar_epis(uid)
        f = migrar_funcionarios(uid)
        m = migrar_modelos(uid)
        a, z = migrar_areas_e_zonas(uid)
        c = migrar_cameras(uid)
        au = migrar_auditoria(uid)
        partes = [f'{e} EPIs' if e else '', f'{f} funcionarios' if f else '',
                  f'{m} modelos' if m else '', f'{a} areas' if a else '',
                  f'{z} zonas' if z else '', f'{c} cameras' if c else '',
                  f'{au} eventos' if au else '']
        resumo = ', '.join(x for x in partes if x) or 'sem dados extras'
        print(f'  {uid[:8]}: {resumo}')

    if '--rostos' in sys.argv:
        print('\nextraindo embeddings de rosto:')
        extrair_rostos()
    else:
        print('\n(use --rostos para ja calcular os embeddings das fotos)')

    print('\nconferencia:')
    for t in ('usuarios', 'sessoes', 'cam_tokens', 'epis', 'epi_fotos', 'funcionarios',
              'func_fotos', 'modelos', 'areas', 'zonas', 'cameras', 'auditoria'):
        n = db.consultar_um(f'SELECT COUNT(*) AS n FROM {t}')
        print(f'  {t:14} {n["n"]}')
    print('\nOK. Os arquivos antigos seguem em dados/ como backup.')


if __name__ == '__main__':
    main()
