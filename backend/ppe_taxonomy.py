"""
ppe_taxonomy.py - Taxonomia unificada de EPIs do Argos EPI

Usada em dois lugares:
  - treinamento/: converte as classes de cada dataset publico para as classes do Argos;
  - backend/: interpreta as classes de qualquer modelo (.pt enviado pelo usuario,
    modelo Argos treinado ou COCO) e diz em que regiao do corpo cada EPI fica.
"""
import re
import unicodedata

# Classes do modelo Argos EPI, na ordem dos indices do data.yaml.
# Nao reordenar: modelos ja treinados dependem destes indices.
DATASET_CLASSES = [
    'capacete', 'sem_capacete',
    'colete', 'sem_colete',
    'luvas', 'sem_luvas',
    'botas', 'sem_botas',
    'oculos', 'sem_oculos',
    'mascara', 'sem_mascara',
    'protetor_auricular',
    'protetor_facial',
    'vestimenta',
]

# region: parte do corpo onde o EPI e procurado (ver ppe_analyzer.body_regions)
ITEMS = {
    'capacete': {'label': 'Capacete', 'region': 'head'},
    'colete': {'label': 'Colete refletivo', 'region': 'torso'},
    'luvas': {'label': 'Luvas', 'region': 'hands'},
    'botas': {'label': 'Calçado de segurança', 'region': 'feet'},
    'oculos': {'label': 'Óculos de proteção', 'region': 'head'},
    'mascara': {'label': 'Máscara / respirador', 'region': 'head'},
    'protetor_auricular': {'label': 'Protetor auricular', 'region': 'head'},
    'protetor_facial': {'label': 'Protetor facial', 'region': 'head'},
    'vestimenta': {'label': 'Vestimenta de proteção', 'region': 'torso'},
}

_POSITIVE = {
    'capacete': ['capacete', 'helmet', 'helmets', 'hardhat', 'hard_hat', 'safety_helmet', 'helm'],
    'colete': ['colete', 'vest', 'safety_vest', 'reflective_vest', 'hi_vis', 'hivis', 'high_vis',
               'jacket', 'reflective_jacket'],
    'luvas': ['luvas', 'luva', 'glove', 'gloves', 'safety_gloves'],
    'botas': ['botas', 'bota', 'calcado', 'boots', 'boot', 'shoes', 'shoe', 'safety_shoe',
              'safety_shoes', 'safety_boot', 'safety_boots', 'footwear'],
    'oculos': ['oculos', 'goggles', 'goggle', 'glasses', 'safety_glasses'],
    'mascara': ['mascara', 'mask', 'face_mask', 'face_mask_medical', 'respirator'],
    'protetor_auricular': ['protetor_auricular', 'earmuffs', 'earmuff', 'ear_muffs', 'ear_muff', 'ear_protection',
                           'ear_protector', 'ear_protectors', 'hearing_protection', 'earplugs'],
    'protetor_facial': ['protetor_facial', 'face_guard', 'faceguard', 'face_shield'],
    'vestimenta': ['vestimenta', 'macacao', 'safety_suit', 'medical_suit', 'suit', 'coverall',
                   'protective_suit'],
}
_NEGATIVE = {
    'capacete': ['sem_capacete', 'no_helmet', 'nohelmet', 'no_helm', 'no_hardhat', 'without_helmet'],
    'colete': ['sem_colete', 'no_vest', 'novest', 'no_safety_vest', 'without_vest'],
    'luvas': ['sem_luvas', 'no_glove', 'no_gloves', 'without_gloves'],
    'botas': ['sem_botas', 'no_boots', 'no_boot', 'no_shoes', 'no_shoe', 'no_safety_shoe', 'no_safety_shoes',
              'no_safety_boots'],
    'oculos': ['sem_oculos', 'no_goggles', 'no_goggle', 'no_glasses'],
    'mascara': ['sem_mascara', 'no_mask'],
}
_PERSON = {'person', 'pessoa', 'worker', 'people'}

_LOOKUP = {}
for _item, _names in _POSITIVE.items():
    for _n in _names:
        _LOOKUP[_n] = (_item, True)
for _item, _names in _NEGATIVE.items():
    for _n in _names:
        _LOOKUP[_n] = (_item, False)


def normalize_name(name) -> str:
    s = unicodedata.normalize('NFKD', str(name or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_')


def class_key(item: str, present: bool = True) -> str:
    return item if present else 'sem_' + item


def map_class(name, overrides=None):
    """('person', True), (item, presente) ou None quando a classe nao e EPI.

    overrides: {nome_normalizado: nome_destino ou None} para classes cujo significado
    depende do dataset (ex.: 'head' = cabeca sem capacete em datasets "hard hat")."""
    key = normalize_name(name)
    if overrides and key in overrides:
        target = overrides[key]
        return map_class(target) if target else None
    if key in _PERSON:
        return ('person', True)
    return _LOOKUP.get(key)


def resolve_model_classes(names) -> dict:
    """Indice da classe -> (item, presente, conhecido) para as classes de um modelo qualquer.

    Classes fora da taxonomia (EPIs cadastrados pelo usuario, classes COCO) voltam com
    conhecido=False e item = nome normalizado; quem usa decide se sao EPI."""
    pairs = [(int(k), v) for k, v in names.items()] if isinstance(names, dict) else list(enumerate(names or []))
    keys = {normalize_name(v) for _, v in pairs}
    overrides = {}
    # Datasets "hard hat" usam 'head' para cabeca sem capacete; no SH17 'head' e qualquer
    # cabeca e vem acompanhada de outras partes do corpo ('face', 'ear', 'hands').
    if 'head' in keys and keys & {'helmet', 'hardhat', 'capacete'} and not keys & {'face', 'ear', 'hands', 'foot'}:
        overrides['head'] = 'sem_capacete'
    resolved = {}
    for idx, name in pairs:
        m = map_class(name, overrides)
        if m is None:
            resolved[idx] = (normalize_name(name), True, False)
        else:
            resolved[idx] = (m[0], m[1], True)
    return resolved


def normalize_required(required) -> list:
    """Converte a lista de EPIs exigidos para itens da taxonomia.

    Configuracoes antigas guardam nomes de classe ('helmet', 'no_helmet', 'Footwear');
    todos viram o item correspondente. Nomes desconhecidos continuam como estao."""
    out = []
    for raw in required or []:
        m = map_class(raw)
        key = normalize_name(raw)
        if m is None:
            if key in ITEMS:
                m = (key, True)
            elif key.startswith('sem_') and key[4:] in ITEMS:
                m = (key[4:], False)
        item = m[0] if m else key
        if item and item != 'person' and item not in out:
            out.append(item)
    return out


def default_required(model_names) -> list:
    """EPIs que o modelo sabe detectar (positivos ou negativos), sem pessoa."""
    out = []
    for item, _present, known in resolve_model_classes(model_names).values():
        if known and item != 'person' and item not in out:
            out.append(item)
    return out


def item_label(item: str) -> str:
    meta = ITEMS.get(item)
    return meta['label'] if meta else str(item).replace('_', ' ').capitalize()


def item_region(item: str) -> str:
    meta = ITEMS.get(item)
    return meta['region'] if meta else 'body'
