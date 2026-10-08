"""run.py - Launcher backend Argos EPI v20."""
import os
import sys
import threading


if getattr(sys, "frozen", False):
    # programa compilado: <instalacao>/motor/ArgosMotor.exe (ver backend/pastas.py)
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, BASE)
    sys.path.insert(0, os.path.join(BASE, "scripts"))

os.makedirs(os.path.join(BASE, "dados", "users"), exist_ok=True)
os.makedirs(os.path.join(BASE, "models"), exist_ok=True)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
except Exception:
    pass


def _porta_livre(p):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        if s.connect_ex(("127.0.0.1", p)) == 0:      # outro programa ja responde nela
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", p))
        except OSError:
            return False
    return True


def _escolher_porta():
    """8088 ocupada por outro programa? Usa a proxima livre ate 8097 (o site
    procura o servidor nessa faixa na hora de vincular). Com ARGOS_PORTA
    definido, ou no Docker, usa exatamente a porta configurada."""
    if os.environ.get("ARGOS_PORTA", "").strip() or os.path.exists("/.dockerenv"):
        return
    for p in range(8088, 8098):
        if _porta_livre(p):
            os.environ["ARGOS_PORTA"] = str(p)
            if p != 8088:
                print(f"  AVISO porta 8088 ocupada por outro programa - usando a porta {p}")
            return


_escolher_porta()

from backend.app import (app, _start_cf, get_local_ip, start_hub_registration,
                         start_auditoria_manutencao, iniciar_banco, start_conta_e_malha, PORTA)
import backend.app as AM
import torch

try:
    from banner import print_banner
except Exception:
    print_banner = None


def main():
    if print_banner:
        print_banner("Servidor backend v20 - IA, cameras, hub e TensorRT", compact=True)
    else:
        print("=" * 58)
        print("  Argos EPI v20 - AndrosoftStudio")
        print("=" * 58)

    if torch.cuda.is_available():
        AM._device_global = "0"
        print(f"  OK GPU NVIDIA: {torch.cuda.get_device_name(0)}")
    else:
        try:
            import torch_directml  # noqa: F401

            AM._device_global = "dml"
            print("  OK DirectML (AMD/Intel)")
        except Exception:
            AM._device_global = "cpu"
            print("  AVISO GPU nao detectada - usando CPU")
    if AM._device_global != "cpu" and AM.preferencia_dispositivo() == "cpu":
        AM._device_global = "cpu"
        print("  Escolhido nos Ajustes: processar so na CPU")

    # ARGOS_HOST=127.0.0.1 (padrao do programa instalado): so este computador e o tunel
    # acessam, e o Windows nao pede permissao de firewall (que exige administrador).
    host = os.environ.get("ARGOS_HOST", "").strip() or "0.0.0.0"
    if host in ("127.0.0.1", "localhost"):
        # nao anuncia endereco de rede que ninguem alcanca (ARGOS_LOCAL_IP: testes com dois
        # servidores no mesmo computador)
        AM.local_ip = os.environ.get("ARGOS_LOCAL_IP", "").strip()
        print("  URL local  : so este computador (rede local desligada nos Ajustes)")
    else:
        AM.local_ip = os.environ.get("ARGOS_LOCAL_IP", "").strip() or get_local_ip()
        print(f"  URL local  : http://{AM.local_ip}:{PORTA}")
    print(f"  Painel     : http://localhost:{PORTA}")
    print("  Vinculo    : https://argosepi.vercel.app/parear.html (abre sozinho na primeira vez)")
    print("  CORS       : " + ", ".join(sorted(AM.CORS_ORIGINS)))
    print(f"  Dados      : {os.path.join(BASE, 'dados', 'users')}")
    print(f"  Modelos    : {os.path.join(BASE, 'models')}")
    print("  Rotas frame: /stream_frame  +  /stream_frame2 (paralelo)")
    print("  Hub        : " + (AM.HUB_URL or "nao configurado"))
    print("  Iniciando Cloudflare Tunnel...")
    print("=" * 58)

    threading.Thread(target=_start_cf, args=(PORTA,), daemon=True).start()
    iniciar_banco()
    start_hub_registration()
    start_auditoria_manutencao()
    # No Docker nao ha navegador: o instalar_docker.bat abre a pagina de vinculo.
    # Pelo iniciar.bat / iniciar.sh o proprio servidor abre o navegador.
    abrir = (not os.path.exists("/.dockerenv")
             and os.environ.get("ARGOS_ABRIR_NAVEGADOR", "1").strip().lower() not in ("0", "false", "nao", "no"))
    start_conta_e_malha(abrir_navegador=abrir)
    app.run(host=host, port=PORTA, threaded=True, debug=False)


if __name__ == "__main__":
    main()
