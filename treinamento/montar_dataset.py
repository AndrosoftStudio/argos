"""
montar_dataset.py - Monta o dataset unificado do Argos EPI a partir das fontes publicas.

Etapas:
  1. le as anotacoes de cada fonte (YOLO ou COCO);
  2. converte as classes para a taxonomia Argos (backend/ppe_taxonomy.py);
  3. descarta caixas invalidas e imagens corrompidas;
  4. remove imagens repetidas (dHash, inclusive espelhadas), sem encadear parecidas;
  5. separa treino/validacao/teste por grupo de imagens parecidas, equilibrando as classes raras;
  6. grava imagens, rotulos, data.yaml, relatorio.md e metadados para o pseudo-rotulo.

Uso:
  python treinamento/montar_dataset.py --nome argos_epi_v1
"""
import argparse
import csv
import json
import os
import random
import shutil
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor

import yaml
from PIL import Image, ImageOps

import comum  # antes do ppe_taxonomy: coloca backend/ no sys.path
import ppe_taxonomy as tax
from fontes import selecionar_fontes

CLASSES = tax.DATASET_CLASSES
IDX = {c: i for i, c in enumerate(CLASSES)}
SPLITS = ('train', 'val', 'test')
IMG_EXTS = ('.jpg', '.jpeg', '.png', '.bmp', '.webp', '.JPG', '.JPEG', '.PNG')
# blocos do hash de 64 bits para busca de vizinhos (pigeonhole: distancia <= 4 => 1 bloco igual)
BLOCOS = [(0, 13), (13, 13), (26, 13), (39, 13), (52, 12)]


# ── Leitura das fontes ──────────────────────────────────────────────
def _nomes_de_yaml(pasta):
    for raiz, _, arquivos in os.walk(pasta):
        for a in sorted(arquivos):
            if not a.lower().endswith(('.yaml', '.yml')):
                continue
            try:
                with open(os.path.join(raiz, a), encoding='utf-8') as f:
                    d = yaml.safe_load(f)
            except Exception:
                continue
            nomes = d.get('names') if isinstance(d, dict) else None
            if isinstance(nomes, dict):
                return {int(k): str(v) for k, v in nomes.items()}
            if isinstance(nomes, list):
                return {i: str(v) for i, v in enumerate(nomes)}
    return None


def _imagem_do_label(caminho_label):
    pasta, arq = os.path.split(caminho_label)
    stem = os.path.splitext(arq)[0]
    partes = pasta.split(os.sep)
    candidatas = []
    for i in range(len(partes) - 1, -1, -1):
        if partes[i].lower() == 'labels':
            candidatas.append(os.sep.join(partes[:i] + ['images'] + partes[i + 1:]))
            break
    candidatas.append(pasta)
    for c in candidatas:
        for ext in IMG_EXTS:
            p = os.path.join(c, stem + ext)
            if os.path.exists(p):
                return p
    return None


def ler_yolo(fonte, pasta):
    nomes = {i: n for i, n in enumerate(fonte['nomes'])} if fonte.get('nomes') else _nomes_de_yaml(pasta)
    if not nomes:
        raise RuntimeError(f"{fonte['id']}: nomes das classes nao encontrados (sem data.yaml)")
    registros = []
    for raiz, _, arquivos in os.walk(pasta):
        if 'labels' not in [p.lower() for p in raiz.split(os.sep)]:
            continue
        for a in arquivos:
            if not a.endswith('.txt'):
                continue
            rotulo = os.path.join(raiz, a)
            img = _imagem_do_label(rotulo)
            if not img:
                continue
            caixas = []
            with open(rotulo, encoding='utf-8', errors='ignore') as f:
                for linha in f:
                    v = linha.split()
                    if len(v) < 5:
                        continue
                    try:
                        c, nums = int(float(v[0])), [float(x) for x in v[1:]]
                    except ValueError:
                        continue
                    if len(nums) == 4:
                        cx, cy, w, h = nums
                    elif len(nums) % 2 == 0:  # poligono de segmentacao -> caixa envolvente
                        xs, ys = nums[0::2], nums[1::2]
                        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
                        w, h = max(xs) - min(xs), max(ys) - min(ys)
                    else:
                        continue
                    caixas.append((nomes.get(c, str(c)), cx, cy, w, h))
            registros.append({'img': img, 'caixas': caixas})
    return registros, set(nomes.values())


def ler_coco(fonte, pasta):
    registros, nomes = [], set()
    for raiz, _, arquivos in os.walk(pasta):
        for a in arquivos:
            # Roboflow: <split>/_annotations.coco.json; CPPE-5: annotations/<split>.json + images/
            if not a.lower().endswith('.json') or (
                    'annotation' not in a.lower() and os.path.basename(raiz).lower() != 'annotations'):
                continue
            with open(os.path.join(raiz, a), encoding='utf-8') as f:
                d = json.load(f)
            categorias = {c['id']: c['name'] for c in d.get('categories', [])}
            nomes |= set(categorias.values())
            por_imagem = defaultdict(list)
            for an in d.get('annotations', []):
                por_imagem[an['image_id']].append(an)
            for im in d.get('images', []):
                W, H = float(im.get('width') or 0), float(im.get('height') or 0)
                img = os.path.join(raiz, im['file_name'])
                if not os.path.exists(img):
                    img = os.path.join(os.path.dirname(raiz), 'images', im['file_name'])
                if not W or not H or not os.path.exists(img):
                    continue
                caixas = []
                for an in por_imagem.get(im['id'], []):
                    x, y, w, h = an['bbox']
                    nome = categorias.get(an['category_id'], str(an['category_id']))
                    caixas.append((nome, (x + w / 2) / W, (y + h / 2) / H, w / W, h / H))
                registros.append({'img': img, 'caixas': caixas})
    return registros, nomes


def mapear_classes(fonte, registros, nomes_fonte):
    """Troca nomes de classe pelos indices Argos. Retorna (classes que a fonte anota, ignoradas)."""
    extra = {tax.normalize_name(k): v for k, v in (fonte.get('mapa_extra') or {}).items()}
    cache = {}

    def indice(nome):
        if nome not in cache:
            m = tax.map_class(nome, extra)
            cache[nome] = IDX.get(tax.class_key(*m)) if m and m[0] != 'person' else None
        return cache[nome]

    conhecidas = sorted({indice(n) for n in nomes_fonte if indice(n) is not None})
    ignoradas = Counter()
    for r in registros:
        r['vazio_original'] = not r['caixas']
        mapeadas = []
        for nome, cx, cy, w, h in r['caixas']:
            i = indice(nome)
            if i is None:
                ignoradas[nome] += 1
            else:
                mapeadas.append((i, cx, cy, w, h))
        r['caixas'] = mapeadas
    return conhecidas, ignoradas


# ── Limpeza, duplicatas e divisao ───────────────────────────────────
def _dhash(g):
    px = list(g.getdata())
    bits = 0
    for y in range(8):
        base = y * 9
        for x in range(8):
            bits = (bits << 1) | (px[base + x] < px[base + x + 1])
    return bits


def analisar_imagem(caminho):
    """(largura, altura) ja com rotacao EXIF + dHash normal e espelhado. None se corrompida."""
    try:
        im = Image.open(caminho)
        largura, altura = im.size
        if im.getexif().get(0x0112, 1) in (5, 6, 7, 8):
            largura, altura = altura, largura
        im.draft('L', (max(64, largura // 8), max(64, altura // 8)))
        pequena = ImageOps.exif_transpose(im).convert('L').resize((9, 8), Image.BILINEAR)
        return largura, altura, _dhash(pequena), _dhash(pequena.transpose(Image.FLIP_LEFT_RIGHT))
    except Exception:
        return None


def limpar_caixas(caixas, largura, altura, min_px=4):
    saida = []
    for c, cx, cy, w, h in caixas:
        x1, y1 = max(0.0, cx - w / 2), max(0.0, cy - h / 2)
        x2, y2 = min(1.0, cx + w / 2), min(1.0, cy + h / 2)
        if (x2 - x1) * largura < min_px or (y2 - y1) * altura < min_px:
            continue
        nova = (c, (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1)
        if any(o[0] == c and comum.iou_xywh(o, nova) > 0.9 for o in saida):
            continue
        saida.append(nova)
    return saida


def distancia(a, b) -> int:
    return min((a['hash'] ^ b['hash']).bit_count(), (a['hash_espelho'] ^ b['hash']).bit_count())


def grupos_duplicados(hashes, espelhados, max_dist):
    """Union-find de imagens com dHash a <= max_dist bits (ou espelhadas).

    E transitivo: quadros de uma camera fixa formam cadeias de milhares de imagens diferentes.
    Serve para manter parecidas no mesmo split, nao para decidir o que e duplicata."""
    pai = list(range(len(hashes)))

    def raiz(i):
        while pai[i] != i:
            pai[i] = pai[pai[i]]
            i = pai[i]
        return i

    def chaves(h):
        for b, (ini, tam) in enumerate(BLOCOS):
            v = (h >> ini) & ((1 << tam) - 1)
            # blocos quase lisos aparecem em muitas fotos (ceu, parede) e explodem a busca
            if 1 < v.bit_count() < tam - 1:
                yield (b, v)

    tabela = defaultdict(list)
    for i, h in enumerate(hashes):
        candidatos = set()
        for q in (h, espelhados[i]):
            for k in chaves(q):
                balde = tabela.get(k, ())
                if len(balde) <= 400:
                    candidatos.update(balde)
        for j in candidatos:
            if (h ^ hashes[j]).bit_count() <= max_dist or (espelhados[i] ^ hashes[j]).bit_count() <= max_dist:
                pai[raiz(i)] = raiz(j)
        for k in chaves(h):
            tabela[k].append(i)
    return [raiz(i) for i in range(len(hashes))]


def nome_base(caminho):
    """Exportacoes Roboflow repetem a foto como '<nome>_jpg.rf.<hash>' (aumentacoes)."""
    return os.path.splitext(os.path.basename(caminho))[0].split('.rf.')[0]


def separar(registros, proporcao, rng, grupo_treino=0.01):
    """Divide por grupo, priorizando classes raras. A mesma foto base (aumentacoes Roboflow) e
    imagens parecidas no dHash nunca caem em splits diferentes, para o teste nao vazar."""
    pai = {}

    def raiz(x):
        pai.setdefault(x, x)
        while pai[x] != x:
            pai[x] = pai[pai[x]]
            x = pai[x]
        return x

    for r in registros:
        pai[raiz(('base', r['fonte'], nome_base(r['img'])))] = raiz(('hash', r['grupo']))
    grupos = defaultdict(list)
    for i, r in enumerate(registros):
        grupos[raiz(('hash', r['grupo']))].append(i)
    freq = Counter()
    for r in registros:
        freq.update({c for c, *_ in r['caixas']})

    def mais_rara(ids):
        cls = {c for i in ids for c, *_ in registros[i]['caixas']}
        return min(cls, key=lambda c: freq[c]) if cls else None

    # grupo grande = uma cena (camera fixa); em validacao/teste dominaria a metrica de algumas classes
    grande = max(50, int(len(registros) * grupo_treino))
    lista = [ids for ids in grupos.values() if len(ids) <= grande]
    rng.shuffle(lista)
    ordenados = sorted(((mais_rara(ids), ids) for ids in lista),
                       key=lambda t: freq[t[0]] if t[0] is not None else float('inf'))
    alvo = dict(zip(SPLITS, proporcao))
    por_classe = {s: Counter() for s in SPLITS}
    por_split = Counter()
    atribuicao = {}

    def passa_da_cota(ids):
        """O grupo sozinho passaria da cota de validacao/teste de alguma de suas classes."""
        cont = Counter(k for i in ids for k in {k for k, *_ in registros[i]['caixas']})
        return any(n > min(alvo['val'], alvo['test']) * freq[k] for k, n in cont.items())

    def atribuir(ids, escolha):
        for i in ids:
            atribuicao[i] = escolha
            por_split[escolha] += 1
            por_classe[escolha].update({k for k, *_ in registros[i]['caixas']})

    for ids in grupos.values():
        if len(ids) > grande:
            atribuir(ids, 'train')
    for c, ids in ordenados:
        if c is None:
            total = sum(por_split.values()) or 1
            escolha = max(SPLITS, key=lambda s: alvo[s] - por_split[s] / total)
        elif passa_da_cota(ids):
            escolha = 'train'
        else:
            total = sum(por_classe[s][c] for s in SPLITS) or 1
            escolha = max(SPLITS, key=lambda s: alvo[s] - por_classe[s][c] / total)
        atribuir(ids, escolha)
    return atribuicao


def gravar(tarefa):
    origem, destino_img, destino_rotulo, caixas, max_lado = tarefa
    try:
        im = ImageOps.exif_transpose(Image.open(origem)).convert('RGB')
        if max(im.size) > max_lado:
            im.thumbnail((max_lado, max_lado), Image.LANCZOS)
        im.save(destino_img, 'JPEG', quality=92)
        with open(destino_rotulo, 'w') as f:
            f.writelines(f"{c} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n" for c, cx, cy, w, h in caixas)
        return True
    except Exception:
        # imagem sem rotulo seria lida como "fundo" pelo YOLO: nao deixar sobra
        for p in (destino_img, destino_rotulo):
            if os.path.exists(p):
                os.remove(p)
        return False


# ── Relatorio ───────────────────────────────────────────────────────
def escrever_relatorio(saida, nome, estat, info, por_fonte_split):
    total = sum(estat[s]['imagens'] for s in SPLITS)
    L = [f"# Dataset {nome}", '',
         f"Gerado em {time.strftime('%d/%m/%Y %H:%M')} · {total} imagens "
         f"(treino {estat['train']['imagens']}, validação {estat['val']['imagens']}, teste {estat['test']['imagens']})", '',
         '## Caixas por classe', '',
         '| Classe | Treino | Validação | Teste | Imagens com a classe |',
         '|---|---:|---:|---:|---:|']
    poucas = []
    for c in CLASSES:
        n = [estat[s]['caixas'][c] for s in SPLITS]
        L.append(f"| {c} | {n[0]} | {n[1]} | {n[2]} | {sum(estat[s]['imagens_com'][c] for s in SPLITS)} |")
        if n[0] < 300:
            poucas.append(c)
    if poucas:
        L += ['', f"Poucos exemplos de treino (< 300 caixas): {', '.join(poucas)}. Espere desempenho menor nessas classes."]
    L += ['', '## Fontes', '',
          '| Fonte | Licença | Lidas | Treino / Val / Teste | Duplicadas | Outros descartes | Classes anotadas |',
          '|---|---|---:|---|---:|---:|---|']
    for fid, d in info.items():
        pfs = por_fonte_split[fid]
        outros = sum(v for k, v in d['descartes'].items() if k != 'duplicada')
        L.append(f"| {d['nome']} | {d['licenca']} | {d['lidas']} | {pfs['train']} / {pfs['val']} / {pfs['test']} | "
                 f"{d['descartes']['duplicada']} | {outros} | {', '.join(d['classes_conhecidas']) or '-'} |")
    L += ['', '## Classes ignoradas (fora da taxonomia)', '']
    for fid, d in info.items():
        if d['nao_mapeadas']:
            L.append(f"- **{fid}**: " + ', '.join(f"{k} ({v})" for k, v in d['nao_mapeadas'].items()))
    if any(d['nao_comercial'] for d in info.values()):
        L += ['', '## Licença', '',
              'Este dataset inclui fontes **CC BY-NC-SA 4.0** (uso não comercial). Modelos treinados com ele '
              'servem para pesquisa e para o TCC; para uso comercial, monte o dataset com `--sem-nao-comercial`.']
    with open(os.path.join(saida, 'relatorio.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')


def main():
    ap = argparse.ArgumentParser(description='Monta o dataset unificado de EPIs')
    ap.add_argument('--base', default=comum.base_padrao())
    ap.add_argument('--nome', default='argos_epi_v1')
    ap.add_argument('--fontes', nargs='*', help='ids das fontes (padrao: todas)')
    ap.add_argument('--sem-nao-comercial', action='store_true', help='ignora fontes CC BY-NC (ex.: SH17)')
    ap.add_argument('--max-lado', type=int, default=1280)
    ap.add_argument('--dist-duplicata', type=int, default=2,
                    help='distancia de Hamming no dHash para duplicata entre fontes diferentes')
    ap.add_argument('--dist-duplicata-mesma-fonte', type=int, default=1,
                    help='idem dentro da mesma fonte (acima disso sao quadros com pessoas/EPIs diferentes)')
    ap.add_argument('--dist-grupo', type=int, default=4, help='imagens ate essa distancia ficam no mesmo split')
    ap.add_argument('--grupo-treino', type=float, default=0.01,
                    help='grupos de parecidas maiores que essa fracao do dataset vao inteiros para treino')
    ap.add_argument('--fundo-max', type=float, default=0.05, help='fracao maxima de imagens sem EPI')
    ap.add_argument('--proporcao', type=float, nargs=3, default=(0.8, 0.1, 0.1))
    ap.add_argument('--limite-por-fonte', type=int, default=0, help='amostra N imagens por fonte (teste rapido)')
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--sobrescrever', action='store_true')
    ap.add_argument('--saida', default='', help='pasta do dataset montado (padrao: <base>/datasets/<nome>)')
    args = ap.parse_args()

    rng = random.Random(args.seed)
    saida = args.saida or os.path.join(comum.pastas(args.base)['datasets'], args.nome)
    if os.path.exists(saida):
        if not args.sobrescrever:
            sys.exit(f"{saida} ja existe (use --sobrescrever)")
        shutil.rmtree(saida)

    registros, info = [], {}
    for ordem, fonte in enumerate(selecionar_fontes(args.fontes, args.sem_nao_comercial)):
        pasta = os.path.normpath(comum.pasta_fonte(args.base, fonte))
        if not os.path.isdir(pasta):
            print(f"[{fonte['id']}] AVISO: nao baixada ({pasta}), ignorando")
            continue
        leitor = ler_yolo if fonte['formato'] == 'yolo' else ler_coco
        regs, nomes = leitor(fonte, pasta)
        conhecidas, ignoradas = mapear_classes(fonte, regs, nomes)
        if args.limite_por_fonte and len(regs) > args.limite_por_fonte:
            regs = rng.sample(regs, args.limite_por_fonte)
        for r in regs:
            r['fonte'], r['ordem'] = fonte['id'], ordem
        info[fonte['id']] = {
            'nome': fonte['nome'], 'licenca': fonte['licenca'], 'info': fonte['info'],
            'nao_comercial': bool(fonte.get('nao_comercial')),
            'classes_conhecidas': [CLASSES[i] for i in conhecidas],
            'nao_mapeadas': dict(ignoradas.most_common()),
            'lidas': len(regs), 'descartes': Counter(),
        }
        print(f"[{fonte['id']}] {len(regs)} imagens | classes: {', '.join(info[fonte['id']]['classes_conhecidas']) or '-'}")
        if not conhecidas:
            print(f"[{fonte['id']}] AVISO: nenhuma classe reconhecida; nomes lidos: {sorted(nomes)}")
        registros.extend(regs)
    if not registros:
        sys.exit('Nenhuma fonte disponivel. Rode baixar_datasets.py primeiro.')

    candidatos = []
    for r in registros:
        if r['caixas'] or r['vazio_original']:
            candidatos.append(r)
        else:
            info[r['fonte']]['descartes']['so_classes_fora_da_taxonomia'] += 1

    print(f"Analisando {len(candidatos)} imagens (tamanho e hash)...")
    validos = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        resultados = ex.map(analisar_imagem, [r['img'] for r in candidatos], chunksize=64)
        for n, (r, res) in enumerate(zip(candidatos, resultados), 1):
            if n % 5000 == 0:
                print(f"  {n}/{len(candidatos)}")
            if res is None:
                info[r['fonte']]['descartes']['imagem_invalida'] += 1
                continue
            r['largura'], r['altura'], r['hash'], r['hash_espelho'] = res
            tinha_caixas = bool(r['caixas'])
            r['caixas'] = limpar_caixas(r['caixas'], r['largura'], r['altura'])
            if tinha_caixas and not r['caixas']:
                info[r['fonte']]['descartes']['caixas_invalidas'] += 1
                continue
            validos.append(r)

    print('Removendo duplicatas...')
    raizes = grupos_duplicados([r['hash'] for r in validos], [r['hash_espelho'] for r in validos], args.dist_grupo)
    por_grupo = defaultdict(list)
    for r, g in zip(validos, raizes):
        r['grupo'] = g
        por_grupo[g].append(r)
    # Entre copias fica a que tem a classe mais rara (a mesma foto pode vir com so luvas numa fonte e com
    # macacao em outra); depois a mais completa; em empate, a de licenca livre.
    freq = Counter(c for r in validos for c, *_ in r['caixas'])

    def prioridade(r):
        rara = min((freq[c] for c, *_ in r['caixas']), default=float('inf'))
        return -rara, len(r['caixas']), not info[r['fonte']]['nao_comercial'], -r['ordem']

    mantidos = []
    for membros in por_grupo.values():
        # cada descartada precisa estar perto de uma que ficou (sem encadear A~B~C)
        ordem = sorted(membros, key=prioridade, reverse=True)
        ficam = []
        for r in ordem:
            if any(distancia(r, k) <= (args.dist_duplicata_mesma_fonte if k['fonte'] == r['fonte'] else args.dist_duplicata)
                   for k in ficam):
                info[r['fonte']]['descartes']['duplicada'] += 1
            else:
                ficam.append(r)
        mantidos.extend(ficam)

    com_epi = [r for r in mantidos if r['caixas']]
    fundos = [r for r in mantidos if not r['caixas']]
    limite_fundos = int(args.fundo_max * len(com_epi))
    rng.shuffle(fundos)
    for r in fundos[limite_fundos:]:
        info[r['fonte']]['descartes']['fundo_excedente'] += 1
    finais = com_epi + fundos[:limite_fundos]

    split_de = separar(finais, args.proporcao, rng, args.grupo_treino)
    for s in SPLITS:
        os.makedirs(os.path.join(saida, 'images', s), exist_ok=True)
        os.makedirs(os.path.join(saida, 'labels', s), exist_ok=True)
    tarefas, nomes_arquivo, contador = [], [], Counter()
    for i, r in enumerate(finais):
        contador[r['fonte']] += 1
        nome = f"{r['fonte']}_{contador[r['fonte']]:06d}"
        s = split_de[i]
        tarefas.append((r['img'], os.path.join(saida, 'images', s, nome + '.jpg'),
                        os.path.join(saida, 'labels', s, nome + '.txt'), r['caixas'], args.max_lado))
        nomes_arquivo.append(nome)

    print(f"Gravando {len(tarefas)} imagens em {saida}...")
    falhas = set()
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for n, ok in enumerate(ex.map(gravar, tarefas, chunksize=32)):
            if not ok:
                falhas.add(n)
            if (n + 1) % 5000 == 0:
                print(f"  {n + 1}/{len(tarefas)}")

    estat = {s: {'imagens': 0, 'caixas': Counter(), 'imagens_com': Counter()} for s in SPLITS}
    por_fonte_split = defaultdict(Counter)
    with open(os.path.join(saida, 'origem.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['arquivo', 'fonte', 'split', 'origem'])
        for i, r in enumerate(finais):
            if i in falhas:
                info[r['fonte']]['descartes']['falha_ao_gravar'] += 1
                continue
            s = split_de[i]
            estat[s]['imagens'] += 1
            estat[s]['caixas'].update(CLASSES[c] for c, *_ in r['caixas'])
            estat[s]['imagens_com'].update({CLASSES[c] for c, *_ in r['caixas']})
            por_fonte_split[r['fonte']][s] += 1
            w.writerow([nomes_arquivo[i], r['fonte'], s, r['img']])

    with open(os.path.join(saida, 'data.yaml'), 'w', encoding='utf-8') as f:
        yaml.safe_dump({'path': saida.replace('\\', '/'), 'train': 'images/train', 'val': 'images/val',
                        'test': 'images/test', 'names': dict(enumerate(CLASSES))},
                       f, allow_unicode=True, sort_keys=False)
    comum.salvar_json(os.path.join(saida, 'fontes_classes.json'),
                      {fid: d['classes_conhecidas'] for fid, d in info.items()})
    comum.salvar_json(os.path.join(saida, 'relatorio.json'), {
        'nome': args.nome,
        'splits': {s: {'imagens': e['imagens'], 'caixas': dict(e['caixas'])} for s, e in estat.items()},
        'fontes': {fid: {**d, 'descartes': dict(d['descartes']), 'usadas': dict(por_fonte_split[fid])}
                   for fid, d in info.items()},
    })
    escrever_relatorio(saida, args.nome, estat, info, por_fonte_split)
    print(f"Pronto: {sum(e['imagens'] for e in estat.values())} imagens. Relatorio: {os.path.join(saida, 'relatorio.md')}")


if __name__ == '__main__':
    main()
