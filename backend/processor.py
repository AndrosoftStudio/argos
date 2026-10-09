"""
processor.py — Motor de IA por stream
v20: os tres modos (tempo real, detalhado, super) sao ao vivo pelo LivePipeline: buffer com
     atraso controlado, quadros-chave analisados pela GPU e resultado interpolado em todos os
     quadros. O modo, o fps e a resolucao sao escolhidos no painel (config do stream).
"""
import base64
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import ajuda
import areas as areas_mod
import rtc
import auditoria
import avisos
import face_id
import ppe_taxonomy as tax
from epi_detector import (MODELS_DIR, EpiDetector, info_do_modelo, is_cuda, merge_detections, modelo_de_reforco,
                          pose_model_path, resolve_device, run_employee_model, trava_gpu)
from reforco import Reforco
from live_pipeline import CONFIG_PADRAO, MODOS, LivePipeline, _Taxa, config_publica, normalizar_config
from ppe_analyzer import PPEAnalyzer, draw_analysis
from yolo_runtime import precision_kwargs


# O decodificador de video (FFmpeg) usa por padrao um thread por nucleo e cada thread segura um
# quadro: num servidor de 16 nucleos a imagem da camera IP chegava meio segundo atrasada. Com um
# thread o quadro sai assim que chega (medido: 540 ms -> 43 ms). ARGOS_DECODIFICAR_THREADS muda.
THREADS_VIDEO = max(1, int(os.environ.get('ARGOS_DECODIFICAR_THREADS', '1') or 1))


def _rtsp_por_tcp():
    """Camera de rede (RTSP) sempre por TCP.

    Por UDP o leitor de video (FFmpeg, dentro do OpenCV) abre portas de escuta neste programa e o
    Windows mostra o aviso do Firewall, que pede administrador: em computador de escola ou empresa
    ninguem consegue aceitar. Camera que so fala UDP: OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;udp
    no .env."""
    valor = os.environ.setdefault('OPENCV_FFMPEG_CAPTURE_OPTIONS', 'rtsp_transport;tcp')
    if os.name == 'nt':
        # no Windows o FFmpeg do OpenCV le as variaveis pelo msvcrt.dll, que guarda uma copia propria
        # feita quando o programa abriu: o os.environ nao chega la (medido: sem isto ele tenta UDP)
        try:
            import ctypes
            ctypes.cdll.msvcrt._putenv_s(b'OPENCV_FFMPEG_CAPTURE_OPTIONS', valor.encode('ascii', 'ignore'))
        except Exception:
            pass


_rtsp_por_tcp()


def _sem_modo_de_eficiencia():
    """O Windows 11 poe programa sem janela no "modo de eficiencia": nucleos lentos e relogio baixo.
    O servidor roda assim, em segundo plano, e a analise ficava com metade da velocidade (medido
    numa RTX 4060 Ti com processador de nucleos mistos: 25 analises por segundo, 50 fora do modo).
    Aqui o processo pede para ficar de fora. Vale so para ele e nao precisa de administrador.
    ARGOS_ECONOMIA=1 deixa como o Windows quiser."""
    if os.name != 'nt' or os.environ.get('ARGOS_ECONOMIA', '').strip().lower() in ('1', 'true', 'sim', 'on'):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class _Estado(ctypes.Structure):
            _fields_ = [('Version', wintypes.ULONG), ('ControlMask', wintypes.ULONG), ('StateMask', wintypes.ULONG)]

        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        # ProcessPowerThrottling (4): ControlMask = velocidade de execucao, StateMask = 0 (sem freio)
        estado = _Estado(1, 0x1, 0)
        return bool(k.SetProcessInformation(k.GetCurrentProcess(), 4, ctypes.byref(estado), ctypes.sizeof(estado)))
    except Exception:
        return False


SEM_ECONOMIA = _sem_modo_de_eficiencia()


def abrir_rede(fonte):
    """Abre uma camera de rede (RTSP/HTTP) sem fila no decodificador."""
    try:
        return cv2.VideoCapture(fonte, cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, THREADS_VIDEO])
    except Exception:      # OpenCV antigo, sem este parametro
        return cv2.VideoCapture(fonte, cv2.CAP_FFMPEG)


class VideoProcessor:
    def __init__(self, client_id: str = "local"):
        self.client_id = client_id
        self.detector = None
        self.reforco = None      # segunda olhada (ROI e o outro detector) so para quem precisa
        self.analyzers = {}  # (nivel da pose, imgsz) -> PPEAnalyzer
        self.analyzer_lock = threading.RLock()
        self.running = False
        self.required_items = []     # EPIs cobrados nesta camera (o que vale na analise)
        self.required_modelo = []    # os que o modelo traz como padrao
        self.device = 'cpu'
        self.thread = None
        self.camera_source = 0
        self.use_external = False
        self.extra_models = []
        self.extra_lock = threading.Lock()
        self.model_name = None
        self.model_path = None
        self.inference_model_path = None
        self.runtime_backend = 'pytorch'
        self.model_classes = []
        self.config = dict(CONFIG_PADRAO)
        self.config_version = 0
        self.pose_ok = True
        # auditoria e areas: quem e o dono do stream, onde gravar e o mapa desenhado
        self.owner_uid = None
        self.dados_dir = None
        self.zonas = []
        self.funcionarios = {}   # nome normalizado -> {'id', 'nome'} (modelo antigo)
        self.galeria = None      # embeddings de rosto dos funcionarios
        self._areas_cache = (0.0, {})
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f'det-{client_id}')
        self.pipeline = LivePipeline(self._analyze_key, self._reset_tracking, nome=client_id, log=self._log)
        # o JPEG do quadro inteiro so e feito quando outro servidor da conta vai ajudar na analise
        self.pipeline.quer_jpeg_inteiro = ajuda.ativa
        self._annot = (-1, None)
        self._annot_lock = threading.Lock()
        # tempos medidos aqui (ms, media movel) e o que outro servidor da conta adiantou (ver ajuda.py)
        self._ms = {}
        self._n_epi = 0
        self._ajudou = {'rosto': _Taxa(), 'epi': _Taxa()}

    def _log(self, msg):
        print(f"[Processor:{self.client_id}] {msg}")

    # ── Auditoria e areas ───────────────────────────────────────────
    def set_auditoria(self, uid, dados_dir, funcionarios=None):
        """Liga o stream ao usuario dono: sem isso nada e gravado no historico."""
        self.owner_uid = uid
        self.dados_dir = dados_dir
        if funcionarios is not None:
            self.set_funcionarios(funcionarios)

    def set_funcionarios(self, funcs):
        """Mapa para converter o nome que o modelo de rosto devolve em func_id."""
        self.funcionarios = {
            str(f.get('nome', '')).replace('_', ' ').strip().lower(): {'id': f.get('id'), 'nome': f.get('nome')}
            for f in (funcs or []) if f.get('nome')
        }

    def set_galeria(self, galeria):
        """Embeddings cadastrados. Trocar a galeria vale no proximo quadro-chave."""
        self.galeria = galeria

    def set_zonas(self, zonas):
        self.zonas = areas_mod.normalizar_zonas(zonas)

    def _areas_por_id(self):
        """Cache curto: as areas mudam pelo painel, nao a 10 quadros por segundo."""
        if not (self.owner_uid and self.dados_dir):
            return {}
        agora = time.time()
        if agora - self._areas_cache[0] < 5.0:
            return self._areas_cache[1]
        try:
            idx = areas_mod.indice(self.owner_uid)
        except Exception:
            idx = self._areas_cache[1]
        self._areas_cache = (agora, idx)
        return idx

    def _resolvedor(self, largura, altura):
        if not self.zonas:
            return None
        return areas_mod.ResolvedorDeArea(self.zonas, self._areas_por_id(),
                                          self.required_items, largura, altura)

    def _identificar(self, pessoa):
        """(func_id, nome). O reconhecimento por embedding ja traz o id; o mapa
        por nome so atende quem ainda usa o modelo antigo funcionarios.pt."""
        fid = pessoa.get('func_id')
        if fid:
            return fid, pessoa.get('nome')
        nome = (pessoa.get('nome') or '').strip().lower()
        rec = self.funcionarios.get(nome)
        if rec:
            return rec['id'], rec['nome']
        return None, (pessoa.get('nome') or None)

    def _auditar(self, analysis, img, t):
        """Grava um episodio por violacao confirmada, com foto do funcionario inteiro."""
        if not (self.owner_uid and self.dados_dir):
            return
        for p in analysis.get('persons') or []:
            epis = p.get('epis') or {}
            # so o que ja passou do tempo de alerta vira registro: evita falso positivo
            confirmados = [i for i in p.get('faltando') or [] if (epis.get(i) or {}).get('alerta')]
            # o episodio segue a pessoa rastreada (track), nao o nome: o rosto costuma
            # ser reconhecido so depois do alerta, e trocar a chave no meio abria
            # outro episodio e deixava o primeiro sem dono
            tid = p.get('track_id')
            func_id, func_nome = self._identificar(p)
            chave = f"track:{tid}" if tid is not None else (func_id or 'sem_track')
            # EPI visto de novo logo depois de uma falta curta: o historico marca "provavel engano"
            vistos = [i for i, s in epis.items() if s.get('estado') == 'ok' and s.get('fonte') == 'detectado']
            if vistos:
                auditoria.viu_com_epi(self.owner_uid, self.client_id, chave, vistos)
            if not confirmados and 'Possível queda' not in (p.get('alertas') or []):
                continue
            # o t do pipeline e relogio de desempenho (perf_counter), nao data: gravado
            # assim, o episodio saia em 1970, a faxina apagava e cada quadro abria outro
            agora = time.time()
            # exigidos: o que estava sendo cobrado desta pessoa (os EPIs da camera ou os da area dela)
            comum = dict(stream_id=self.client_id, chave_pessoa=chave, func_id=func_id,
                         func_nome=func_nome, track_id=p.get('track_id'),
                         area_id=p.get('area_id'), area_nome=p.get('area'),
                         img=img, box=p.get('bbox'), t=agora, exigidos=list(epis.keys()))
            linhas = []
            for item in confirmados:
                try:
                    est = auditoria.episodio(self.dados_dir, self.owner_uid, tipo='violacao',
                                             epi=item, epi_label=tax.item_label(item),
                                             forte=(epis.get(item) or {}).get('fonte') == 'ausencia_detectada', **comum)
                    linhas.append(est.get('rowid'))
                except Exception as e:
                    self._log(f"auditoria (violacao) falhou: {e}")
            queda = 'Possível queda' in (p.get('alertas') or [])
            if queda:
                try:
                    est = auditoria.episodio(self.dados_dir, self.owner_uid, tipo='queda',
                                             detalhe='Possível queda detectada', **comum)
                    linhas.append(est.get('rowid'))
                except Exception as e:
                    self._log(f"auditoria (queda) falhou: {e}")
            # TV: sirene e voz ("Fulano, por favor, coloque o capacete"). Area com o alarme desligado nao avisa.
            area = self._areas_por_id().get(p.get('area_id')) if p.get('area_id') else None
            if area is None or area.get('alarme', True):
                try:
                    avisos.violacao(self.owner_uid, self.client_id, chave, func_id=func_id, func_nome=func_nome,
                                    itens=confirmados, area=p.get('area') or '', rowids=linhas, queda=queda, t=agora)
                except Exception as e:
                    self._log(f"aviso da TV falhou: {e}")

    @classmethod
    def read_model_info(cls, model_path: str) -> dict:
        """{'classes': [...], 'arquitetura': 'yolo' | 'detr'}"""
        return info_do_modelo(model_path)

    # ── Configuracao ────────────────────────────────────────────────
    @property
    def mode(self):
        return self.config['modo']

    def set_config(self, cfg):
        """Modo, fps e resolucao escolhidos no painel. Vale na hora, sem reiniciar o stream."""
        novo = normalizar_config(cfg, self.config)
        if novo != self.config:
            self.config = novo
            self.config_version += 1
            self._aplicar_epis()
        self.pipeline.configurar(novo['modo'])
        if self.running and self.pose_ok:
            threading.Thread(target=self._analyzer_for, args=(MODOS[novo['modo']],), daemon=True).start()
        return self.public_config()

    def public_config(self):
        return config_publica(self.config)

    def _aplicar_epis(self):
        """EPIs cobrados nesta camera: os escolhidos nos botoes do painel; sem escolha, os do modelo.
        Vale no proximo quadro-chave, sem reiniciar a camera."""
        epis = self.config.get('epis')
        if epis is None or self.detector is None:
            self.required_items = list(self.required_modelo)
        else:
            self.required_items = self.detector.clean_required(list(epis))

    def epis_disponiveis(self):
        """EPIs que este modelo sabe procurar (os botoes que o painel mostra na camera)."""
        if self.detector is None:
            return list(self.required_modelo)
        itens = tax.default_required(self.detector.names)
        return itens + [i for i in self.required_modelo if i not in itens]

    def _analyzer_for(self, perfil):
        chave = (perfil['pose'], perfil['imgsz'])
        with self.analyzer_lock:
            a = self.analyzers.get(chave)
            if a is None and self.pose_ok:
                try:
                    a = PPEAnalyzer(pose_model_path(perfil['pose']), device=self.device, half=is_cuda(self.device),
                                    imgsz=perfil['imgsz'])
                    self.analyzers[chave] = a
                except Exception as ex:
                    self._log(f"Pose {perfil['pose']} indisponivel ({ex})")
                    if perfil['pose'] == 'tempo_real':
                        self.pose_ok = False
                        return None
                    return self._analyzer_for(MODOS['tempo_real'])  # usa a pose leve no lugar
            return a

    def _reset_tracking(self):
        with self.analyzer_lock:
            for a in self.analyzers.values():
                a.reset()

    def update_settings(self, model_path: str, device: str, camera_source=None, required_items=None, config=None):
        self._log(f"Carregando: {model_path} | device: {device}")
        self.stop()
        if config:
            self.set_config(config)
        try:
            device = resolve_device(device)
            if device != self.device:
                with self.analyzer_lock:
                    self.analyzers = {}
            self.device = device
            self.detector = EpiDetector(model_path, self.device, use_tensorrt=True, log=self._log)
            self._tam = None
            perfil = MODOS[self.mode]
            # tamanhos que esta camera vai usar (ver _tamanho_analise), ja aquecidos antes de ela ligar
            tam_pose = perfil['imgsz']
            if perfil.get('imediato') and is_cuda(self.device):
                tam_pose = max(tam_pose, self._teto_do_lado(self.config['resolucao'] * 16 // 9))
            self.detector.aquecer(self._tamanho_detector(tam_pose, perfil))
            self.model_path = model_path
            self.inference_model_path = self.detector.runtime_path
            self.runtime_backend = self.detector.backend
            self.model_name = os.path.basename(self.detector.runtime_path)
            self.model_classes = self.detector.class_names
            self.required_modelo = self.detector.clean_required(
                tax.normalize_required(required_items) if required_items else tax.default_required(self.detector.names))
            self._aplicar_epis()
            self.reforco = Reforco(self.detector, self._detector_extra(model_path))
            self.pose_ok = True
            pose = self._analyzer_for(perfil)
            if pose is not None:
                pose.aquecer(tam_pose)
            if camera_source is not None:
                if camera_source == "webcam":
                    self.use_external = True
                    self.camera_source = "webcam"
                else:
                    self.use_external = False
                    self.camera_source = int(camera_source) if isinstance(camera_source, str) and camera_source.isdigit() else camera_source
            self.pipeline.limpar()
            self.running = True
            self.pipeline.iniciar()
            if not self.use_external:
                self.thread = threading.Thread(target=self._run_capture, daemon=True, name=f"cap-{self.client_id}")
                self.thread.start()
            self._log(f"Iniciado: {self.camera_source} | modelo={self.model_name} ({self.detector.arquitetura}) | "
                      f"runtime={self.runtime_backend} | "
                      f"modo={self.mode} | pose={'sim' if self.pose_ok else 'nao'} | EPIs={self.required_items}")
        except Exception as e:
            self._log(f"ERRO: {e}")
            self.running = False

    def _detector_extra(self, model_path):
        """O modelo Argos da outra arquitetura (DETR x YOLO), para a segunda opiniao em quem esta
        encoberto. Sem um treinado em models/, o reforco usa so o recorte (ROI)."""
        caminho = modelo_de_reforco(MODELS_DIR, model_path, self.detector.arquitetura)
        if not caminho or os.path.abspath(caminho) == os.path.abspath(model_path):
            return None
        try:
            extra = EpiDetector(caminho, self.device, use_tensorrt=True, log=self._log)
            self._log(f"Segunda opiniao para pessoas encobertas: {os.path.basename(caminho)} ({extra.arquitetura})")
            return extra
        except Exception as e:
            self._log(f"Segunda opiniao indisponivel ({os.path.basename(caminho)}): {e}")
            return None

    def load_extra_models(self, model_paths: list):
        from ultralytics import YOLO
        with self.extra_lock:
            self.extra_models = []
            for mp in model_paths:
                if os.path.exists(mp):
                    try:
                        model = YOLO(mp)
                        self.extra_models.append({'model': model, 'path': mp,
                                                  'resolved': tax.resolve_model_classes(model.names)})
                        self._log(f"Modelo extra: {mp}")
                    except Exception as e:
                        self._log(f"Erro modelo extra {mp}: {e}")

    # ── Entrada ─────────────────────────────────────────────────────
    def receive_jpeg(self, seq, t, jpeg, session=None):
        """Quadro de camera externa (celular, navegador). t = horario de captura em segundos."""
        if not self.running or not self.use_external:
            return False
        if self.direto_ativo():       # o video direto ja esta trazendo os quadros desta camera
            return False
        return self.pipeline.receber(seq, t, jpeg, sessao=session)

    def set_external_frame(self, img, seq=None):
        """Rotas antigas (JSON base64): sem horario de captura, usa a hora de chegada."""
        if self.running and self.use_external:
            self.pipeline.receber_imagem(img, largura_max=MODOS[self.mode]['largura_envio'])

    set_external_frame_b = set_external_frame  # rota paralela antiga (/stream_frame2)

    # ── Video direto (WebRTC) ───────────────────────────────────────
    # O celular/navegador manda video de verdade (H.264/VP8) por WebRTC para o MediaMTX, que roda
    # ao lado do servidor; aqui o quadro e lido do endereco local dele e entra no mesmo pipeline.
    def ligar_direto(self, url):
        """Comeca (ou mantem) a leitura do video direto desta camera."""
        self._direto_url = url
        th = getattr(self, '_direto_thread', None)
        if th is not None and th.is_alive():
            return
        self._direto_parar = threading.Event()
        self._direto_thread = threading.Thread(target=self._ler_direto, args=(self._direto_parar,), daemon=True,
                                               name=f'direto-{self.client_id}')
        self._direto_thread.start()

    def desligar_direto(self):
        ev = getattr(self, '_direto_parar', None)
        if ev is not None:
            ev.set()
        self._direto_em = 0.0

    # O video direto nao espera nada: pacote que se perde no caminho vira imagem quebrada e a analise
    # falha junto. Com perda seguida, a camera volta para o envio normal (que nao perde) por um tempo.
    # 2% dos pacotes, ja contadas as retransmissoes (ARGOS_RTC_PERDA_LIMITE, em %, muda)
    PERDA_LIMITE = float(os.environ.get('ARGOS_RTC_PERDA_LIMITE', '2') or 2) / 100
    PERDA_JANELAS = 3         # em 3 medidas seguidas (6 s)

    def direto_espera(self) -> float:
        """Segundos que faltam para o video direto poder ser tentado de novo (0 = pode)."""
        return max(0.0, getattr(self, '_direto_volta_em', 0.0) - time.monotonic())

    def direto_aviso(self):
        """Uma vez so: (motivo, espera em segundos) quando o video direto acabou de ser dispensado."""
        aviso, self._direto_aviso = getattr(self, '_direto_aviso', None), None
        return aviso

    def _conferir_perda(self, estado) -> bool:
        """True quando a perda passou do limite por tempo demais (chamado a cada ~2 s)."""
        agora = rtc.chegada(self.client_id)
        if agora is None:
            return False
        antes, estado['ultimo'] = estado.get('ultimo'), agora
        if antes is None or agora[0] < antes[0]:
            return False
        rec, per = agora[0] - antes[0], agora[1] - antes[1]
        taxa = per / max(rec + per, 1)
        self._direto_perda = round(taxa * 100, 1)
        estado['ruins'] = estado.get('ruins', 0) + 1 if (rec + per >= 30 and taxa > self.PERDA_LIMITE) else 0
        if estado['ruins'] < self.PERDA_JANELAS:
            return False
        # cada vez que acontece, espera mais antes de tentar de novo: 2, 5, 10 min
        self._direto_quedas = getattr(self, '_direto_quedas', 0) + 1
        espera = (120, 300, 600)[min(self._direto_quedas, 3) - 1]
        self._direto_volta_em = time.monotonic() + espera
        self._direto_aviso = ('perda', espera)
        self._log(f'Video direto com {self._direto_perda}% de perda: voltando ao envio normal por {espera // 60} min.')
        return True

    def direto_ativo(self) -> bool:
        """True enquanto os quadros estao chegando pelo video direto."""
        return time.perf_counter() - getattr(self, '_direto_em', 0.0) < 1.5

    def _limitar(self, img):
        """Reduz ate a resolucao escolhida no painel, contada no lado menor: 1080 e Full HD com o
        celular em pe (1080x1920) ou deitado (1920x1080). Antes contava so a altura, e o celular em
        pe chegava a analise com 608x1080."""
        h, w = img.shape[:2]
        alvo = self.config['resolucao']
        if min(h, w) > alvo:
            s = alvo / min(h, w)
            img = cv2.resize(img, (int(w * s) // 2 * 2, int(h * s) // 2 * 2), interpolation=cv2.INTER_AREA)
        return img

    def _largura_max(self):
        # tempo real: a analise recebe a imagem inteira (nada fica guardado, entao nao pesa);
        # nos outros modos cada quadro vira JPEG no buffer e o limite continua
        perfil = MODOS[self.mode]
        return None if perfil.get('imediato') else perfil['largura_envio']

    def direto_chegando(self):
        """O que o video direto esta entregando de verdade: {'w', 'h', 'fps'} ou None."""
        info = getattr(self, '_direto_info', None)
        if not info or not self.direto_ativo():
            return None
        return {'w': info['w'], 'h': info['h'], 'fps': info['taxa'].valor(time.perf_counter())}

    def _ler_direto(self, parar):
        cap, vazio_desde = None, time.perf_counter()
        proximo = 0.0
        perda, conferir_em = {}, time.perf_counter() + 2.0
        self._direto_perda = 0.0
        self._direto_info = info = {'w': 0, 'h': 0, 'taxa': _Taxa()}
        base_rtp, relogio_rtp, confirmado = None, None, False
        while not parar.is_set() and self.running and self.use_external:
            if time.perf_counter() >= conferir_em:
                conferir_em = time.perf_counter() + 2.0
                if self._conferir_perda(perda):
                    break
            if cap is None:
                cap = abrir_rede(self._direto_url)
                if not cap.isOpened():
                    cap.release()
                    cap = None
                    if time.perf_counter() - vazio_desde > 12:   # ninguem publicando: desiste
                        break
                    parar.wait(0.4)
                    continue
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                # cada abertura recomeca a contagem do tempo do video: quem assiste precisa saber
                self._direto_epoca = getattr(self, '_direto_epoca', 0) + 1
                if relogio_rtp is not None:
                    relogio_rtp.parar()
                base_rtp, confirmado = None, False
                relogio_rtp = rtc.RelogioRtp(self._direto_url)
                relogio_rtp.start()
            ok, img = cap.read()
            agora = time.perf_counter()
            relogio = time.time()
            if not ok or img is None:
                cap.release()
                cap = None
                if agora - vazio_desde > 12:
                    break
                parar.wait(0.2)
                continue
            vazio_desde = agora
            self._direto_em = agora
            info['h'], info['w'] = img.shape[:2]
            info['taxa'].marcar(agora)
            tv = cap.get(cv2.CAP_PROP_POS_MSEC)
            if relogio_rtp is not None:
                # nos quadros-chave (um a cada ~2 s) acerta o tempo deste leitor com o tempo RTP do video
                if tv and tv > 0 and int(cap.get(cv2.CAP_PROP_FRAME_TYPE)) == 73:      # 73 = 'I'
                    nova = relogio_rtp.base_para(round(tv * 90), agora)
                    if nova is not None:
                        confirmado = base_rtp == nova
                        base_rtp = nova
                if confirmado or not relogio_rtp.is_alive():
                    relogio_rtp.parar()
                    relogio_rtp = None
            if agora < proximo - 0.004:     # respeita o fps escolhido no painel (4 ms de folga: a 60 q/s
                continue                    # o quadro que chega um pouco adiantado nao e jogado fora)
            proximo = max(proximo + 1.0 / self.config['fps'], agora - 0.05)
            # Hora deste quadro, para o painel e o celular porem as caixas no quadro certo:
            # rtp = tempo RTP do quadro (o mesmo numero que o navegador de quem assiste ve em cada
            #       quadro): com ele o acerto e exato. Aparece depois do primeiro quadro-chave;
            # tv = tempo do video contado por este leitor (ms); ts = relogio deste computador quando
            #      o quadro chegou; ep = muda quando a leitura reabre. Servem enquanto nao ha o rtp.
            extra = {'tv': round(tv, 3) if tv and tv > 0 else None, 'ts': round(relogio * 1000, 1),
                     'ep': self._direto_epoca}
            if base_rtp is not None and extra['tv'] is not None:
                extra['rtp'] = (base_rtp + round(tv * 90)) & 0xFFFFFFFF
            self.pipeline.receber_imagem(self._limitar(img), t=agora, largura_max=self._largura_max(),
                                         sessao='direto', extra=extra)
        if cap is not None:
            cap.release()
        if relogio_rtp is not None:
            relogio_rtp.parar()
        self._direto_em = 0.0

    def _run_capture(self):
        cap = self._open_camera(self.camera_source)
        if not cap or not cap.isOpened():
            self._log(f"ERRO: câmera indisponível: {self.camera_source}")
            self.running = False
            return
        is_file = isinstance(self.camera_source, str) and os.path.isfile(self.camera_source)
        file_fps = cap.get(cv2.CAP_PROP_FPS) if is_file else 0
        file_delay = 1.0 / file_fps if 1 <= file_fps <= 120 else 1 / 30
        next_t = 0.0
        while self.running:
            t0 = time.perf_counter()
            ok, img = cap.read()
            if not ok:
                if is_file:  # recomeca o arquivo; o horario continua avancando, entao o buffer segue valendo
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                time.sleep(0.5)
                cap.release()
                cap = self._open_camera(self.camera_source)
                continue
            # respeita o fps escolhido no painel e limita a resolucao enviada ao pipeline
            if t0 >= next_t:
                next_t = max(next_t + 1.0 / self.config['fps'], t0 - 0.05)
                self.pipeline.receber_imagem(self._limitar(img), t=t0, largura_max=self._largura_max())
            if is_file:  # arquivo toca na velocidade original
                time.sleep(max(0.0, file_delay - (time.perf_counter() - t0)))
        cap.release()
        self._log("Captura encerrada.")

    def _open_camera(self, source):
        if isinstance(source, str) and source.startswith(('rtsp://', 'rtsps://')):
            cap = abrir_rede(source)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        elif isinstance(source, str) and source.startswith(('http://', 'https://')):
            cap = abrir_rede(source)
        elif isinstance(source, str) and os.path.isfile(source):
            cap = cv2.VideoCapture(source)
        else:
            cap = cv2.VideoCapture(source)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    # ── Processamento de um quadro-chave ────────────────────────────
    def _run_extra_models(self, img, detections):
        with self.extra_lock:
            extras = list(self.extra_models)
        employees = []
        for extra in extras:
            try:
                if os.path.basename(extra['path']) == 'funcionarios.pt':
                    if not self.config.get('rosto', True):   # camera so confere EPIs: nao identifica ninguem
                        continue
                    employees.extend(run_employee_model(extra['model'], img, self.device, is_cuda(self.device)))
                    continue
                r = extra['model'].predict(img, device=self.device, verbose=False, conf=0.4, iou=0.45,
                                           **precision_kwargs(is_cuda(self.device)))[0]
                if r.boxes is None:
                    continue
                for xyxy, conf, cls in zip(r.boxes.xyxy.cpu().tolist(), r.boxes.conf.cpu().tolist(), r.boxes.cls.int().cpu().tolist()):
                    item, present, known = extra['resolved'].get(cls, (str(cls), True, False))
                    detections.append({'item': item, 'present': present, 'known': known, 'conf': round(conf, 4),
                                       'box': tuple(xyxy), 'label': str(extra['model'].names[cls]),
                                       'cls': f"extra:{cls}", 'source': 'extra'})
            except Exception:
                pass
        return employees

    def _medir(self, chave, t0):
        ms = (time.perf_counter() - t0) * 1000
        antes = self._ms.get(chave)
        self._ms[chave] = ms if antes is None else antes * 0.8 + ms * 0.2

    def _detectar(self, img, imgsz, conf):
        """Detector de EPIs neste servidor, cronometrado (o par so ajuda se for mais rapido que isto)."""
        t0 = time.perf_counter()
        dets = self.detector.detect(img, imgsz, conf)
        self._medir(('det', imgsz), t0)
        return dets

    def _reconhecer_rostos(self, img, pedido=None):
        """Acha os rostos do quadro e compara cada um com a galeria de funcionarios.

        Mesmo caminho do projeto DEEPFAKE: o embedding do rosto vira um vetor
        unitario e a identidade sai da menor distancia. Quem nao bate com
        ninguem volta sem nome -- a caixa ainda aparece, so nao e atribuida.

        pedido: os rostos deste quadro ja foram pedidos a outro servidor da conta. Se a resposta
        chegou (ou chega em menos tempo do que achar os rostos aqui), usa; senao faz aqui."""
        if self.galeria is None or self.galeria.vazia or not self.config.get('rosto', True):
            return []
        rostos = None
        if pedido is not None and pedido.contar:
            d = pedido.resultado(esperar_s=0.8 * self._ms.get('rosto', 0.0) / 1000)
            if d is not None:
                rostos = ajuda.rostos_da_resposta(d)
                self._ajudou['rosto'].marcar(time.perf_counter())
        if rostos is None:
            t0 = time.perf_counter()
            try:
                with trava_gpu():
                    rostos = face_id.detectar(img, self.device)
            except Exception as e:
                self._log(f'reconhecimento de rosto falhou: {e}')
                return []
            self._medir('rosto', t0)
        saida = []
        for r in rostos:
            fid, nome, dist = self.galeria.identificar(r['embedding'])
            if not fid:
                continue
            saida.append({'nome': nome, 'func_id': fid, 'bbox': r['bbox'],
                          'confidence': round(max(0.0, 1.0 - dist / 2.0), 4),
                          'distancia': round(float(dist), 4)})
        return saida

    def _frame_analysis(self, detections):
        """Sem modelo de pose: EPI exigido ausente em qualquer lugar do frame."""
        found = {(d['item'], d['present']) for d in detections}
        person = any(d['item'] == 'person' for d in detections) or 'person' not in self.detector.items
        missing = [i for i in self.required_items if (i, True) not in found] if person else []
        return {'persons': [], 'status': 'perigo' if missing else ('seguro' if person else 'sem_pessoa'),
                'missing_items': missing, 'missing': [tax.item_label(i) for i in missing], 'alerts': [],
                '_sem_pose': True}

    # Tempo real: quem esta longe ocupa poucos pontos da imagem e some quando ela e encolhida para
    # 640. Com a placa de video a rede roda quase no mesmo tempo com a imagem inteira (medido numa
    # RTX 4060 Ti: pose 12 ms em 640 e 13 ms em 1920), entao o tamanho sobe ate o da propria imagem
    # enquanto a analise couber no tempo; se ficar lenta, desce de novo.
    TAMANHOS = (640, 960, 1280, 1600, 1920)
    ALVO_ANALISE_S = 0.05           # 20 analises por segundo
    DETECTOR_MAX = 1280             # o detector de EPIs foi treinado em 640: acima disto nao ganha

    def _teto_do_lado(self, lado):
        return next((s for s in self.TAMANHOS if s >= lado), self.TAMANHOS[-1])    # nunca amplia a imagem

    def _tamanho_analise(self, img, perfil):
        base = perfil['imgsz']
        if not perfil.get('imediato') or str(self.device) == 'cpu':
            return base
        teto = self._teto_do_lado(max(img.shape[:2]))
        agora = time.perf_counter()
        if agora < getattr(self, '_tam_teto_ate', 0.0):
            teto = min(teto, self._tam_teto)
        teto = max(teto, base)
        tam = getattr(self, '_tam', None)
        if tam is None:
            # placa NVIDIA: ja comeca no tamanho da imagem; as outras sobem aos poucos, medindo
            tam = teto if is_cuda(self.device) else base
            self._tam_em = agora + 5.0      # as primeiras analises num tamanho novo sao lentas: nao contam
            self._tam_prova = None
        tam = max(base, min(tam, teto))
        if agora - self._tam_em >= 1.5:
            self._tam_em = agora
            proc = self.pipeline.proc_s
            i = self.TAMANHOS.index(tam)
            prova = getattr(self, '_tam_prova', None)
            if prova is not None:
                # acabou de diminuir: so vale se a analise ficou mesmo mais rapida. Em muita placa o
                # tempo quase nao depende do tamanho (o peso e do processador), e ai encolher a
                # imagem so tiraria a visao de longe: volta ao tamanho de antes e nao mexe mais.
                self._tam_prova = None
                if proc > 0.85 * prova[1]:
                    tam = prova[0]
                    self._tam_em = agora + 300.0
                else:
                    self._tam_teto, self._tam_teto_ate = tam, agora + 60     # nao sobe de novo por 1 min
            elif proc > self.ALVO_ANALISE_S * 1.3 and tam > base:
                self._tam_prova = (tam, proc)
                tam = self.TAMANHOS[i - 1]
                self._tam_em = agora + 3.0
            elif proc < self.ALVO_ANALISE_S * 0.7 and tam < teto:
                tam = self.TAMANHOS[i + 1]
                self._tam_em = agora + 3.0
        self._tam = tam
        return tam

    def _tamanho_detector(self, tam_pose, perfil):
        """Tamanho da imagem para o detector de EPIs. No tempo real com o modelo convertido em
        TensorRT ele fica no tamanho da conversao (5 ms por quadro) e quem esta longe e conferido
        pelo recorte ampliado (segunda olhada); sem TensorRT o modelo original roda quase no mesmo
        tempo em qualquer tamanho, entao acompanha a pose ate 1280."""
        if not perfil.get('imediato'):
            return perfil['imgsz']
        if self.detector is not None and self.detector.backend == 'tensorrt':
            return perfil['imgsz']
        return min(tam_pose, max(perfil['imgsz'], self.DETECTOR_MAX))

    def _analyze_key(self, img, t, perfil, jpeg=None):
        det = self.detector
        h, w = img.shape[:2]
        inicio = time.perf_counter()
        tam_pose = self._tamanho_analise(img, perfil)
        imgsz = self._tamanho_detector(tam_pose, perfil)
        resolvedor = self._resolvedor(w, h)
        # com zonas, o detector precisa procurar tudo que qualquer area possa exigir
        required = resolvedor.todos_os_itens() if resolvedor is not None else list(self.required_items)
        analyzer = self._analyzer_for(perfil) if self.pose_ok else None
        run_det = det.useful_for(required)
        # Outro servidor da conta, na mesma rede, adianta o que nao tem memoria: os rostos (em paralelo
        # com a analise daqui) e o detector de EPIs (so quando ele tem respondido mais rapido que o daqui).
        # O rastreio das pessoas e a votacao ficam sempre neste servidor.
        p_rosto = p_epi = None
        ms_det = self._ms.get(('det', imgsz))
        if jpeg is not None and ajuda.ativa():
            if (self.galeria is not None and not self.galeria.vazia and 'rosto' in self._ms
                    and self.config.get('rosto', True)):
                p_rosto = ajuda.pedir_rostos(jpeg, self._ms.get('pre', 0.0) + 0.8 * self._ms['rosto'])
            if run_det and ms_det is not None:
                self._n_epi += 1
                if self._n_epi % 120:   # de vez em quando roda so aqui: mantem a medida do detector local em dia
                    p_epi = ajuda.pedir_epis(jpeg, self.model_path, imgsz, perfil['conf'], 0.75 * ms_det)
        contar_epi = p_epi is not None and p_epi.contar
        # detector e pose rodam juntos na GPU (modelos diferentes, threads diferentes)
        fut = self._pool.submit(self._detectar, img, imgsz, perfil['conf']) if run_det and not contar_epi else None
        t_pose = time.perf_counter()
        people = analyzer.detect_people(img, tam_pose) if analyzer is not None else None
        self._medir('pose', t_pose)
        if contar_epi:
            # espera o par ate o tempo que o detector daqui levaria; passou disso, roda aqui
            d = p_epi.resultado(esperar_s=ms_det * 1.1 / 1000 - (time.perf_counter() - inicio))
            if d is not None:
                dets = ajuda.dets_da_resposta(d)
                self._ajudou['epi'].marcar(time.perf_counter())
            else:
                dets = self._detectar(img, imgsz, perfil['conf'])
        else:
            dets = fut.result() if fut is not None else []
        if perfil['recortes'] and people and run_det:
            dets = merge_detections(dets, det.detect_crops(img, [p['box'] for p in people], imgsz=640,
                                                            conf=perfil['conf']))
        if self.reforco is not None and people and run_det:
            # so quem esta encoberto ou sem evidencia de algum EPI ganha a segunda olhada
            t_ref = time.perf_counter()
            dets = self.reforco.aplicar(img, people, dets, required, t, ja_recortou=perfil['recortes'],
                                        conf=perfil['conf'])
            self._medir('reforco', t_ref)
        employees = self._run_extra_models(img, dets)
        self._medir('pre', inicio)
        # tempo real: o rosto e conferido 4 vezes por segundo (o nome fica guardado na pessoa
        # rastreada); em toda analise ele custava mais que a pose e segurava as caixas
        if not perfil.get('imediato') or p_rosto is not None or t - getattr(self, '_rosto_em', -1e9) >= 0.25:
            self._rosto_em = t
            employees += self._reconhecer_rostos(img, p_rosto)
        if analyzer is not None:
            analysis = analyzer.analyze(img, dets, required, det.supported, t=t, employees=employees,
                                        people=people, resolvedor=resolvedor)
        else:
            analysis = self._frame_analysis(dets)
        self._auditar(analysis, img, t)
        return analysis, dets, employees

    # ── Saida ───────────────────────────────────────────────────────
    @property
    def processed(self):
        return self.pipeline.exibidos

    def wait_result(self, last_processed, timeout=1.0):
        count, result, _ = self.pipeline.esperar_saida(last_processed, timeout)
        return count, result

    def get_result(self):
        return self.pipeline.ultimo_resultado

    def _annotated_jpeg(self):
        count, result, img = self.pipeline.imagem_atual()
        if img is None:
            return None
        with self._annot_lock:
            if self._annot[0] == count:
                return self._annot[1]
        if isinstance(img, bytes):
            img = cv2.imdecode(np.frombuffer(img, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return None
        else:
            img = img.copy()      # o desenho nao pode cair na imagem que a analise ainda usa
        dets = [{'item': d['item'], 'present': d['present'], 'conf': d['confidence'], 'box': d['bbox'],
                 'label': d['label']} for d in (result or {}).get('detections', [])]
        draw_analysis(img, result or {}, dets, zonas=self.zonas, areas_por_id=self._areas_por_id())
        ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            return None
        with self._annot_lock:
            self._annot = (count, buf.tobytes())
            return self._annot[1]

    def get_frame_b64(self):
        jpeg = self._annotated_jpeg()
        return base64.b64encode(jpeg).decode() if jpeg else None

    def generate_frames(self):
        last = -1
        while True:
            count, _, _ = self.pipeline.esperar_saida(last, 0.5)
            if count == last:
                if not self.running:
                    return
                continue
            last = count
            frame = self._annotated_jpeg()
            if frame:
                yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'

    def get_status(self) -> dict:
        r = self.pipeline.ultimo_resultado or {}
        stats = self.pipeline.estatisticas()
        return {
            'active': self.running,
            'status': 'sem_sinal' if stats['sem_sinal'] and self.use_external else r.get('status', 'aguardando'),
            'missing': list(r.get('missing', [])),
            'missing_items': list(r.get('missing_items', [])),
            'alerts': list(r.get('alerts', [])),
            'persons': list(r.get('persons', [])),
            'fps': stats['fps_analise'],
            'fps_exibido': stats['fps_exibido'],
            'fps_entrada': stats['fps_entrada'],
            'latency_ms': stats['atraso_ms'],
            'atraso_ms': stats['atraso_ms'],
            'camera': str(self.camera_source),
            'client_id': self.client_id,
            'model': self.model_name,
            'arquitetura': self.detector.arquitetura if self.detector else None,
            'runtime_backend': self.detector.backend if self.detector else self.runtime_backend,
            'inference_model_path': self.inference_model_path,
            'tamanho_analise': getattr(self, '_tam', None),
            'classes': list(self.model_classes),
            'required_items': list(self.required_items),
            'required_labels': [tax.item_label(i) for i in self.required_items],
            'epis_disponiveis': self.epis_disponiveis(),
            'ajuda': self.ajuda_status(),
            # quanto cada etapa leva neste servidor (ms): e com isto que se decide se o par compensa
            'tempos_ms': {('detector' if isinstance(k, tuple) else k): round(v) for k, v in self._ms.items()},
            'epis_da_camera': self.config.get('epis') is not None,   # False = seguindo o padrao do modelo
            'zonas': list(self.zonas),
            'areas': [self._areas_por_id().get(z['area_id']) for z in self.zonas
                      if self._areas_por_id().get(z['area_id'])],
            'detections': list(r.get('detections', [])),
            'employees': list(r.get('employees', [])),
            'activity': r.get('activity', 'sem leitura'),
            'pose': self.pose_ok,
            'config': self.public_config(),
            'direto': self.direto_ativo(),
            'direto_chegando': self.direto_chegando(),
            'direto_perda': getattr(self, '_direto_perda', 0.0) if self.direto_ativo() else None,
            'direto_espera_s': round(self.direto_espera()),
            'pipeline': stats,
            'frames_recebidos': len(self.pipeline.taxa_entrada.ts),
            'frames_descartados': stats['pulados'],
        }

    def ajuda_status(self):
        """Quadros por segundo que outro servidor da conta esta adiantando para esta camera (None = nenhum)."""
        agora = time.perf_counter()
        rosto, epi = self._ajudou['rosto'].valor(agora), self._ajudou['epi'].valor(agora)
        if not (rosto or epi):
            return None
        return {'rosto': rosto, 'epi': epi,
                'pares': [p['nome'] for p in ajuda.resumo() if sum(p['usados'].values())]}

    def stop(self):
        self.running = False
        if self.owner_uid:
            # camera parou: fecha os episodios abertos para nao ficarem com duracao aberta
            try:
                auditoria.encerrar_stream(self.owner_uid, self.client_id)
            except Exception:
                pass
        self.desligar_direto()
        self.pipeline.parar()
        if self.thread and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)
        self.thread = None

    def stop_external(self):
        """Camera do navegador desligada: para de processar e limpa a saida, sem remover o stream."""
        self.stop()
        self.pipeline.limpar()
        self.use_external = False

    def close(self):
        """Stream removido: encerra tudo e avisa quem esta assistindo."""
        self.stop()
        self.pipeline.fechar()
