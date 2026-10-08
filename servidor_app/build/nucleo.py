"""Argos EPI Servidor - executavel do motor no Windows (motor/ArgosMotor.exe).

So liga o Python e as bibliotecas compiladas; o codigo do Argos (supervisor e backend) fica
fora dele, ja compilado, em <instalacao>/codigo.pak. Assim uma versao nova do Argos troca um
arquivo pequeno e este executavel (dezenas de MB de bibliotecas) so e baixado de novo quando
uma biblioteca muda. Mexer neste arquivo troca o id do pacote do nucleo: deixe-o minimo.
"""
import os
import sys


def main():
    pasta = os.path.dirname(os.path.abspath(sys.executable))
    sys.path.insert(0, os.path.join(os.path.dirname(pasta), 'codigo.pak'))
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        import motor
    except ModuleNotFoundError as e:
        if e.name != 'motor':
            raise
        print('Argos EPI Servidor: falta o arquivo codigo.pak na pasta do programa. Instale de novo pelo site.')
        sys.exit(3)
    motor.main()


if __name__ == '__main__':
    main()
