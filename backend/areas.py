"""
areas.py - Areas de risco e EPIs exigidos por area (PostgreSQL)

Uma AREA e uma entidade do usuario (ex.: "Solda", "Almoxarifado") com a lista de
EPIs obrigatorios naquele local. A area existe uma vez so; varias cameras podem
enxerga-la.

Uma ZONA e o desenho dessa area sobre a imagem de uma camera: um poligono em
coordenadas normalizadas (0..1). A mesma area "Solda" pode ter um poligono na
camera 1 e outro na camera 2.

Na analise, a pessoa e localizada pelo ponto de apoio (base da caixa, entre os
pes). Dentro de uma zona valem os EPIs daquela area; fora de todas, vale o
padrao do stream.
"""
import json
import time
import uuid

import db
import ppe_taxonomy as tax

COR_PADRAO = '#c81e1e'


def _linha_area(l) -> dict:
    epis = l.get('epis_obrigatorios')
    if isinstance(epis, str):
        try:
            epis = json.loads(epis or '[]')
        except ValueError:
            epis = []
    return {'id': l['id'], 'nome': l['nome'], 'descricao': l.get('descricao') or '',
            'cor': l.get('cor') or COR_PADRAO, 'epis_obrigatorios': epis or [],
            'alarme': bool(l.get('alarme', True)), 'criado_em': l.get('criado_em')}


def normalizar(a: dict) -> dict:
    """Garante os campos e converte os EPIs para as chaves da taxonomia."""
    return {
        'id': str(a.get('id') or uuid.uuid4()),
        'nome': str(a.get('nome') or 'Area sem nome').strip(),
        'descricao': str(a.get('descricao') or '').strip(),
        'cor': str(a.get('cor') or COR_PADRAO),
        'epis_obrigatorios': tax.normalize_required(a.get('epis_obrigatorios') or []),
        'alarme': bool(a.get('alarme', True)),
        'criado_em': float(a.get('criado_em') or time.time()),
    }


def carregar(uid: str) -> list:
    try:
        return [_linha_area(l) for l in db.consultar(
            'SELECT * FROM areas WHERE uid=%s ORDER BY criado_em', (uid,))]
    except Exception:
        return []


def criar(uid: str, dados: dict) -> dict:
    a = normalizar({**dados, 'id': str(uuid.uuid4()), 'criado_em': time.time()})
    db.executar(
        'INSERT INTO areas (id,uid,nome,descricao,cor,epis_obrigatorios,alarme,criado_em)'
        ' VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s)',
        (a['id'], uid, a['nome'], a['descricao'], a['cor'],
         json.dumps(a['epis_obrigatorios']), a['alarme'], a['criado_em']))
    return a


def atualizar(uid: str, area_id: str, dados: dict):
    atual = db.consultar_um('SELECT * FROM areas WHERE uid=%s AND id=%s', (uid, area_id))
    if not atual:
        return None
    base = _linha_area(atual)
    mesclado = {**base, **{k: v for k, v in dados.items() if k not in ('id', 'criado_em')}}
    a = normalizar({**mesclado, 'id': area_id, 'criado_em': base['criado_em']})
    db.executar(
        'UPDATE areas SET nome=%s, descricao=%s, cor=%s, epis_obrigatorios=%s::jsonb, alarme=%s'
        ' WHERE uid=%s AND id=%s',
        (a['nome'], a['descricao'], a['cor'], json.dumps(a['epis_obrigatorios']),
         a['alarme'], uid, area_id))
    return a


def remover(uid: str, area_id: str) -> bool:
    # as zonas saem junto, pela chave estrangeira com ON DELETE CASCADE
    return db.executar('DELETE FROM areas WHERE uid=%s AND id=%s', (uid, area_id)) > 0


def indice(uid: str) -> dict:
    return {a['id']: a for a in carregar(uid)}


# ── Zonas (poligonos na imagem da camera) ───────────────────────────

def normalizar_zonas(zonas) -> list:
    """Valida a lista de zonas de um stream. Descarta poligonos invalidos."""
    out = []
    for z in zonas or []:
        if not isinstance(z, dict):
            continue
        pontos = []
        for p in z.get('pontos') or []:
            try:
                x, y = float(p[0]), float(p[1])
            except (TypeError, ValueError, IndexError):
                continue
            pontos.append([min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)])
        if len(pontos) < 3:      # poligono precisa de ao menos 3 vertices
            continue
        out.append({'id': str(z.get('id') or uuid.uuid4()),
                    'area_id': str(z.get('area_id') or ''),
                    'pontos': pontos})
    return out


def carregar_zonas(uid: str, stream_id=None):
    try:
        if stream_id is None:
            linhas = db.consultar('SELECT * FROM zonas WHERE uid=%s', (uid,))
            fora = {}
            for l in linhas:
                fora.setdefault(l['stream_id'], []).append(_linha_zona(l))
            return fora
        return [_linha_zona(l) for l in db.consultar(
            'SELECT * FROM zonas WHERE uid=%s AND stream_id=%s', (uid, stream_id))]
    except Exception:
        return [] if stream_id is not None else {}


def _linha_zona(l) -> dict:
    pts = l.get('pontos')
    if isinstance(pts, str):
        try:
            pts = json.loads(pts or '[]')
        except ValueError:
            pts = []
    return {'id': l['id'], 'area_id': l.get('area_id') or '', 'pontos': pts or []}


def salvar_zonas(uid: str, stream_id: str, zonas) -> list:
    limpas = normalizar_zonas(zonas)
    # so aceita zona que aponte para area existente deste usuario
    validas = set(indice(uid).keys())
    limpas = [z for z in limpas if z['area_id'] in validas]
    with db.pool().connection() as c:
        c.execute('DELETE FROM zonas WHERE uid=%s AND stream_id=%s', (uid, stream_id))
        for z in limpas:
            c.execute('INSERT INTO zonas (id,uid,stream_id,area_id,pontos) VALUES (%s,%s,%s,%s,%s::jsonb)',
                      (z['id'], uid, stream_id, z['area_id'], json.dumps(z['pontos'])))
    return limpas


def remover_area_das_zonas(uid: str, area_id: str):
    """Mantido por compatibilidade: o CASCADE do banco ja faz isso."""
    try:
        db.executar('DELETE FROM zonas WHERE uid=%s AND area_id=%s', (uid, area_id))
    except Exception:
        pass


# ── Geometria ───────────────────────────────────────────────────────

def ponto_em_poligono(x: float, y: float, pontos) -> bool:
    """Ray casting. x, y e pontos em coordenadas normalizadas."""
    dentro = False
    n = len(pontos)
    j = n - 1
    for i in range(n):
        xi, yi = pontos[i]
        xj, yj = pontos[j]
        if (yi > y) != (yj > y):
            corte = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < corte:
                dentro = not dentro
        j = i
    return dentro


def ponto_de_apoio(box, largura: int, altura: int):
    """Onde a pessoa esta pisando, normalizado 0..1."""
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2.0 / max(largura, 1), y2 / max(altura, 1))


class ResolvedorDeArea:
    """Decide os EPIs exigidos para cada pessoa conforme a zona em que ela pisa."""

    def __init__(self, zonas, areas_por_id, padrao, largura, altura):
        self.padrao = list(padrao or [])
        self.largura = largura
        self.altura = altura
        self.zonas = []
        for z in zonas or []:
            area = areas_por_id.get(z.get('area_id'))
            if area is not None:      # zona apontando para area apagada: ignora
                self.zonas.append((z, area))

    @property
    def ativo(self) -> bool:
        return bool(self.zonas)

    def para_box(self, box):
        """(lista_de_epis, area ou None) para a pessoa nessa caixa."""
        if not self.zonas:
            return self.padrao, None
        x, y = ponto_de_apoio(box, self.largura, self.altura)
        for z, area in self.zonas:
            if ponto_em_poligono(x, y, z['pontos']):
                return list(area['epis_obrigatorios']), area
        return self.padrao, None

    def todos_os_itens(self) -> list:
        """Uniao de tudo que pode ser exigido: o detector procura por todos."""
        itens = list(self.padrao)
        for _z, area in self.zonas:
            for i in area['epis_obrigatorios']:
                if i not in itens:
                    itens.append(i)
        return itens
