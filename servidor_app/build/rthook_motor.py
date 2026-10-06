# Roda dentro do motor compilado, antes de tudo (runtime hook do PyInstaller, ver motor.spec).
#
# O programa nao leva o texto (.py) das bibliotecas. O PyTorch le o texto de alguns modulos
# de configuracao so para montar a lista de opcoes que o torch.compile ignora, e o Argos nao
# usa torch.compile: quando o texto de um modulo do torch nao existe, devolve vazio em vez de erro.
import inspect
import types
import warnings

# aviso do PyTorch sobre o mesmo assunto (funcoes @overload sem texto): so encheria o Registro
warnings.filterwarnings('ignore', message='Unable to retrieve source for @torch.jit._overload')

_original = inspect.getsource


def _getsource(obj):
    try:
        return _original(obj)
    except OSError:
        if isinstance(obj, types.ModuleType) and (obj.__name__ or '').startswith('torch'):
            return ''
        raise


inspect.getsource = _getsource
