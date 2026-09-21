"""yolo_runtime.py - Detalhes de versao do Ultralytics compartilhados pelos modulos de IA."""
from functools import lru_cache


@lru_cache(maxsize=1)
def _has_quantize() -> bool:
    try:
        from ultralytics.cfg import DEFAULT_CFG_DICT
        return 'quantize' in DEFAULT_CFG_DICT
    except Exception:
        return False


def precision_kwargs(half: bool) -> dict:
    """Argumento de fp16 na GPU: o Ultralytics 8.4 trocou 'half' por 'quantize'."""
    if not half:
        return {}
    return {'quantize': 16} if _has_quantize() else {'half': True}
