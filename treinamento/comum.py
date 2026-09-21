"""comum.py - Caminhos e utilitarios compartilhados pelos scripts de treinamento."""
import json
import os
import sys

RAIZ_PROJETO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ppe_taxonomy.py mora no backend e e a mesma taxonomia usada na inferencia
sys.path.insert(0, os.path.join(RAIZ_PROJETO, 'backend'))


def base_padrao() -> str:
    """Pasta dos dados de treino (datasets somam dezenas de GB; fica fora do OneDrive)."""
    env = os.environ.get('ARGOS_TREINO_DIR', '').strip()
    if env:
        return env
    if os.name == 'nt' and os.path.isdir('D:\\'):
        return r'D:\ArgosEPI'
    return os.path.join(os.path.expanduser('~'), 'ArgosEPI-treino')


def pastas(base: str) -> dict:
    return {
        'raw': os.path.join(base, 'datasets', 'raw'),
        'extraido': os.path.join(base, 'datasets', 'extraido'),
        'datasets': os.path.join(base, 'datasets'),
        'runs': os.path.join(base, 'runs'),
        'pesos': os.path.join(base, 'pesos'),
    }


def pasta_fonte(base: str, fonte: dict) -> str:
    """Onde ficam as anotacoes prontas para leitura de uma fonte."""
    raiz = pastas(base)['extraido' if fonte['tipo'] in ('url_zip', 'roboflow') else 'raw']
    return os.path.join(raiz, fonte['id'])


def carregar_json(caminho: str, padrao=None):
    try:
        with open(caminho, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return padrao


def salvar_json(caminho: str, dados):
    os.makedirs(os.path.dirname(caminho) or '.', exist_ok=True)
    with open(caminho, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


def iou_xywh(a, b) -> float:
    """IoU entre caixas (classe, cx, cy, w, h) normalizadas."""
    ax1, ay1, ax2, ay2 = a[1] - a[3] / 2, a[2] - a[4] / 2, a[1] + a[3] / 2, a[2] + a[4] / 2
    bx1, by1, bx2, by2 = b[1] - b[3] / 2, b[2] - b[4] / 2, b[1] + b[3] / 2, b[2] + b[4] / 2
    inter = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    uniao = a[3] * a[4] + b[3] * b[4] - inter
    return inter / uniao if uniao > 0 else 0.0
