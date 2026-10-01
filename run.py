"""run.py - Launcher backend Argos EPI v20."""
import os
import sys
import threading


BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "scripts"))

os.makedirs(os.path.join(BASE, "dados", "users"), exist_ok=True)
os.makedirs(os.path.join(BASE, "models"), exist_ok=True)

from backend.app import (app, _start_cf, get_local_ip, start_hub_registration,
                         start_auditoria_manutencao, iniciar_banco, start_conta_e_malha, PORTA)
import backend.app as AM
import torch

try:
    from banner import print_banner
except Exception:
    print_banner = None


if __name__ == "__main__":
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

    AM.local_ip = os.environ.get("ARGOS_LOCAL_IP", "").strip() or get_local_ip()
    print(f"  URL local  : http://{AM.local_ip}:{PORTA}")
    print(f"  Vinculo    : http://localhost:{PORTA}/parear (abre sozinho na primeira vez)")
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
    app.run(host="0.0.0.0", port=PORTA, threaded=True, debug=False)
