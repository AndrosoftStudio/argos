"""
fontes.py - Datasets publicos usados para montar o dataset Argos EPI.

Cada fonte anota so parte dos EPIs; 'classes conhecidas' de cada uma sao calculadas
na montagem e usadas pelo pseudo_rotular.py para completar o que falta.
"""

_HF = 'https://huggingface.co/datasets/'

FONTES = [
    {
        'id': 'construction_ppe',
        'nome': 'Ultralytics Construction-PPE',
        'licenca': 'AGPL-3.0',
        'info': 'https://docs.ultralytics.com/datasets/detect/construction-ppe/',
        'tipo': 'url_zip',
        'arquivos': ['https://github.com/ultralytics/assets/releases/download/v0.0.0/construction-ppe.zip'],
        'formato': 'yolo',
        # o zip nao traz data.yaml; nomes do construction-ppe.yaml oficial
        'nomes': ['helmet', 'gloves', 'vest', 'boots', 'goggles', 'none', 'Person',
                  'no_helmet', 'no_goggle', 'no_gloves', 'no_boots'],
    },
    {
        'id': '51ddhesh_ppe',
        'nome': 'PPE Detection (51ddhesh)',
        'licenca': 'CC BY 4.0',
        'info': _HF + '51ddhesh/PPE_Detection',
        'tipo': 'url_zip',
        'arquivos': [_HF + '51ddhesh/PPE_Detection/resolve/main/PPE.zip'],
        'formato': 'yolo',
    },
    {
        'id': 'jhboyo_ppe',
        'nome': 'PPE Dataset 3-Class (jhboyo)',
        'licenca': 'MIT',
        'info': _HF + 'jhboyo/ppe-dataset',
        'tipo': 'hf_snapshot',
        'repo': 'jhboyo/ppe-dataset',
        'formato': 'yolo',
        'nomes': ['helmet', 'head', 'vest'],
        # neste dataset 'head' marca cabeca sem capacete
        'mapa_extra': {'head': 'sem_capacete'},
    },
    {
        'id': 'vincent_ppe',
        'nome': 'PPE Detection 1 (VincentGOURBIN / Roboflow)',
        'licenca': 'CC BY 4.0',
        'info': _HF + 'VincentGOURBIN/ppe-detection',
        'tipo': 'hf_snapshot',
        'repo': 'VincentGOURBIN/ppe-detection',
        'formato': 'yolo',
    },
    {
        'id': 'keremberke_protective',
        'nome': 'Protective Equipment Detection (keremberke / Roboflow)',
        'licenca': 'CC BY 4.0',
        'info': _HF + 'keremberke/protective-equipment-detection',
        'tipo': 'url_zip',
        'arquivos': [_HF + 'keremberke/protective-equipment-detection/resolve/main/data/' + s + '.zip'
                     for s in ('train', 'valid', 'test')],
        'formato': 'coco',
    },
    {
        'id': 'keremberke_construction',
        'nome': 'Construction Safety (keremberke / Roboflow)',
        'licenca': 'CC BY 4.0',
        'info': _HF + 'keremberke/construction-safety-object-detection',
        'tipo': 'url_zip',
        'arquivos': [_HF + 'keremberke/construction-safety-object-detection/resolve/main/data/' + s + '.zip'
                     for s in ('train', 'valid', 'test')],
        'formato': 'coco',
    },
    {
        'id': 'sh17',
        'nome': 'SH17 - Human Safety and PPE (espelho Roboflow)',
        'licenca': 'CC BY-NC-SA 4.0',
        'nao_comercial': True,
        'info': 'https://github.com/ahmadmughees/SH17dataset',
        'tipo': 'hf_snapshot',
        'repo': 'fathansanum/SH-17-Dataset',
        'formato': 'coco',
        # O espelho Roboflow troca os nomes por numeros, fora da ordem oficial do SH17. Conferido
        # recortando caixas de cada categoria: 0 pessoa, 1 orelha, 2 capacete, 3 maos, 4 cabeca,
        # 5 roupa hospitalar, 6 calcado, 7 macacao/roupa de seguranca, 8 colete, 9 abafador,
        # 10 rosto, 11 protetor facial/mascara de solda, 12 mascara cirurgica, 13 pe,
        # 14 ferramentas, 15 oculos, 16 luvas.
        # Calcado (6) e oculos (15) ficam de fora: no SH17 sao quase sempre tenis, sapatos e oculos
        # de grau, nao EPI. Partes do corpo tambem nao entram (nao indicam falta de EPI).
        'mapa_extra': {'2': 'helmet', '5': 'medical_suit', '7': 'safety_suit', '8': 'safety_vest',
                       '9': 'ear_muffs', '11': 'face_guard', '12': 'face_mask_medical', '16': 'gloves'},
    },
    # Fontes extras para as classes raras (botas/sem_botas, vestimenta, protetor facial)
    {
        'id': 'ppes_14classes',
        'nome': 'PPEs 14 classes (Roboflow object-detection / espelho BIDJOE)',
        'licenca': 'CC BY 4.0',
        'info': 'https://universe.roboflow.com/object-detection/ppes-kaxsi',
        'tipo': 'url_zip',
        'arquivos': [_HF + 'BIDJOE/ppe-14classes/resolve/main/dataset_bidjoe_14classes.zip'],
        'formato': 'yolo',
        # Cameras de fabrica com anotacao parcial: cada imagem marca so alguns trabalhadores/EPIs.
        # Aproveita so 'suit' (macacao, uma caixa por imagem). shoes/no_shoes sao caixas minusculas e ambiguas.
        'mapa_extra': {n: None for n in ('glove', 'goggles', 'helmet', 'mask', 'no_suit', 'no_glove', 'no_goggles',
                                         'no_helmet', 'no_mask', 'no_shoes', 'shoes', 'no_safety_vest', 'safety_vest')},
    },
    {
        'id': 'ppe_9p0pu',
        'nome': 'PPE Detection 9p0pu (Roboflow / espelho Chapian)',
        'licenca': 'CC BY 4.0',
        'info': 'https://universe.roboflow.com/developing-automatic-surveillance-system-for-personal-protective-'
                'equipment-compliance-in-real-construction-sites-using-deep-neural-network-models/ppe-detection-9p0pu',
        'tipo': 'url_zip',
        'arquivos': [_HF + 'Chapian/ppev3/resolve/main/PPE%20DETECTION.v3i.coco.zip'],
        'formato': 'coco',
    },
    {
        'id': 'cppe5',
        'nome': 'CPPE-5 (EPI hospitalar)',
        'licenca': 'Apache-2.0',
        'info': 'https://github.com/Rishit-dagli/CPPE-Dataset',
        'tipo': 'url_zip',  # .tar.gz: baixar_datasets.py extrai zip e tar
        'arquivos': ['https://github.com/Rishit-dagli/CPPE-Dataset/releases/download/v0.1.0/dataset.tar.gz'],
        'formato': 'coco',
    },
    # Roboflow Universe: precisam de ROBOFLOW_API_KEY no ambiente (ver baixar_datasets.py)
    {
        'id': 'rf_amalia_ppe',
        'nome': 'PPE (Amalia123 / Roboflow)',
        'licenca': 'Public Domain',
        'info': 'https://universe.roboflow.com/amalia123/ppe.',
        'tipo': 'roboflow', 'workspace': 'amalia123', 'projeto': 'ppe.', 'versao': 4,
        'formato': 'coco',
        'mapa_extra': {'rompi': 'vest', 'masker': 'mask'},  # nomes em indonesio
    },
    {
        'id': 'rf_boots',
        'nome': 'Boots (Construction Resources / Roboflow)',
        'licenca': 'CC BY 4.0',
        'info': 'https://universe.roboflow.com/construction-resources/boots-uzihq',
        'tipo': 'roboflow', 'workspace': 'construction-resources', 'projeto': 'boots-uzihq', 'versao': 3,
        'formato': 'coco',
    },
    {
        'id': 'rf_safety_shoe',
        'nome': 'Safety shoe (Roboflow)',
        'licenca': 'CC BY 4.0',
        'info': 'https://universe.roboflow.com/new-workspace-xszud/safety-shoe',
        'tipo': 'roboflow', 'workspace': 'new-workspace-xszud', 'projeto': 'safety-shoe', 'versao': 1,
        'formato': 'coco',
        'mapa_extra': {'helmet': None},  # capacete so marcado em parte das fotos; o foco sao os calcados
    },
    {
        'id': 'rf_workplace_ppe',
        'nome': 'PPE Dataset for Workplace Safety (RussellWenner / Roboflow)',
        'licenca': 'CC BY 4.0',
        'info': 'https://universe.roboflow.com/russellwenner/ppe-dataset-for-workplace-safety-62mmn-a016k-lsqf6',
        'tipo': 'roboflow', 'workspace': 'russellwenner', 'projeto': 'ppe-dataset-for-workplace-safety-62mmn-a016k-lsqf6',
        'versao': 1,
        'formato': 'coco',
        # luvas, oculos, mascara e protetor auricular tem de 4 a 32 caixas em 1604 imagens: a fonte nao anota essas classes
        'mapa_extra': {'glove': None, 'glass': None, 'mask': None, 'ear_protection': None},
    },
    {
        'id': 'rf_earplug',
        'nome': 'Earplug use (Lia Casco / Roboflow)',
        'licenca': 'CC BY 4.0',
        'info': 'https://universe.roboflow.com/lia-casco/combine-ypw05',
        'tipo': 'roboflow', 'workspace': 'lia-casco', 'projeto': 'combine-ypw05', 'versao': 2,
        'formato': 'coco',
        'mapa_extra': {'earplug_use': 'earplugs', 'no_earplug_use': None},  # nao existe classe "sem protetor auricular"
    },
]


def selecionar_fontes(ids=None, sem_nao_comercial=False) -> list:
    fontes = [f for f in FONTES if not ids or f['id'] in ids]
    if sem_nao_comercial:
        fontes = [f for f in fontes if not f.get('nao_comercial')]
    desconhecidas = set(ids or []) - {f['id'] for f in FONTES}
    if desconhecidas:
        raise SystemExit('fontes desconhecidas: ' + ', '.join(sorted(desconhecidas)))
    return fontes
