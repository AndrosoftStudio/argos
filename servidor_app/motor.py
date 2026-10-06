"""Argos EPI Servidor - ponto de entrada do programa compilado (motor/ArgosMotor).

O empacotamento (build/empacotar_*.) compila este arquivo, o supervisor e o backend
num executavel so; o Python, o PyTorch, o OpenCV e as outras bibliotecas ficam em
.dll/.pyd (.so no Linux) na mesma pasta. Nenhum .py vai para a instalacao.

    ArgosMotor supervisor --pai <pid> --exe <janela>   aberto pela janela do programa
    ArgosMotor servidor                                aberto pelo supervisor: o backend
"""
import multiprocessing
import os
import subprocess
import sys


def main():
    multiprocessing.freeze_support()
    # sem pip no programa compilado: o Ultralytics nao tenta instalar nada sozinho
    os.environ.setdefault('YOLO_AUTOINSTALL', 'false')
    papel = sys.argv[1] if len(sys.argv) > 1 else ''
    del sys.argv[1:2]
    if papel == 'supervisor':
        import supervisor
        supervisor.main()
    elif papel == 'servidor':
        import run
        run.main()
    elif papel == 'conferir':
        # usado pelo empacotamento: abre todas as bibliotecas (PyTorch, OpenCV, backend inteiro) e sai
        os.environ['ARGOS_PORTA'] = '8088'
        import cv2
        import onnxruntime
        import torch
        import ultralytics
        import run  # noqa: F401
        import dependencias
        import supervisor  # noqa: F401
        print('ok', 'torch', torch.__version__, '| ultralytics', ultralytics.__version__, '| opencv', cv2.__version__,
              '| onnxruntime', onnxruntime.get_available_providers())
        variante = ''
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'argos-variante.txt')) as f:
                variante = f.read().strip()
        except OSError:
            pass
        if variante == 'dml':
            import torch_directml
            print('ok directml:', torch_directml.device_count(), 'placa(s)')
        if variante == 'nvidia':
            import onnx
            print('ok cuda:', torch.cuda.is_available(), '| onnx', onnx.__version__)
            if not dependencias.faltando():     # pacote opcional do TensorRT instalado
                import tensorrt
                print('ok tensorrt', tensorrt.__version__)
            else:
                print('tensorrt: pacote opcional nao instalado')
        # autoteste do empacotamento: conferir --rosto --detectar <modelo.pt>
        # pedidos a mao (demoram): --instalar-trt (baixa o pacote opcional) e --trt <modelo.pt> (converte)
        if '--instalar-trt' in sys.argv:
            dependencias._instalar()
            print('ok pacote do TensorRT instalado:', dependencias.faltando() == [])
        if '--rosto' in sys.argv:
            import time

            import numpy as np

            import face_id
            quadro = np.zeros((480, 640, 3), dtype=np.uint8)
            dispositivo = '0' if torch.cuda.is_available() else ('dml' if variante == 'dml' else 'cpu')
            face_id.detectar(quadro, dispositivo)
            t = time.time()
            for _ in range(10):
                face_id.detectar(quadro, dispositivo)
            print(f'ok rosto em {dispositivo}: {(time.time() - t) * 100:.1f} ms por quadro | sessoes', list(face_id._sessoes))
        for opcao in ('--detectar', '--trt'):
            if opcao in sys.argv:
                import numpy as np
                modelo = ultralytics.YOLO(sys.argv[sys.argv.index(opcao) + 1])
                if opcao == '--trt':
                    print('ok tensorrt:', modelo.export(format='engine', device=0, half=True, workspace=4,
                                                        simplify=False, verbose=False))
                else:
                    r = modelo.predict(np.zeros((480, 640, 3), dtype=np.uint8), verbose=False,
                                       device=0 if torch.cuda.is_available() else 'cpu')
                    print('ok deteccao:', r[0].boxes.shape, '| velocidade', r[0].speed)
    else:
        # aberto direto (duplo clique): quem liga tudo e a janela do programa
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
        janela = os.path.join(raiz, 'ArgosEPI.exe' if os.name == 'nt' else 'argos-epi-servidor')
        if getattr(sys, 'frozen', False) and os.path.exists(janela):
            subprocess.Popen([janela], cwd=raiz, close_fds=True)
        else:
            print('Uso: ArgosMotor supervisor|servidor (abra o Argos EPI Servidor pelo atalho do programa)')
            sys.exit(2)


if __name__ == '__main__':
    main()
