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
import areas as areas_mod
import auditoria
import face_id
import ppe_taxonomy as tax
from epi_detector import (EpiDetector, info_do_modelo, is_cuda, merge_detections, pose_model_path,
                          resolve_device, run_employee_model)
from live_pipeline import CONFIG_PADRAO, MODOS, LivePipeline, config_publica, normalizar_config
from ppe_analyzer import PPEAnalyzer, draw_analysis
from yolo_runtime import precision_kwargs


class VideoProcessor:
    def __init__(self, client_id: str = "local"):
        self.client_id = client_id
        self.detector = None
        self.analyzers = {}  # (nivel da pose, imgsz) -> PPEAnalyzer
        self.analyzer_lock = threading.RLock()
        self.running = False
        self.required_items = []
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
        self._annot = (-1, None)
        self._annot_lock = threading.Lock()

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
            if not confirmados and 'Possível queda' not in (p.get('alertas') or []):
                continue
            func_id, func_nome = self._identificar(p)
            chave = func_id or f"track:{p.get('track_id')}"
            comum = dict(stream_id=self.client_id, chave_pessoa=chave, func_id=func_id,
                         func_nome=func_nome, track_id=p.get('track_id'),
                         area_id=p.get('area_id'), area_nome=p.get('area'),
                         img=img, box=p.get('bbox'), t=t)
            for item in confirmados:
                try:
                    auditoria.episodio(self.dados_dir, self.owner_uid, tipo='violacao',
                                       epi=item, epi_label=tax.item_label(item), **comum)
                except Exception as e:
                    self._log(f"auditoria (violacao) falhou: {e}")
            if 'Possível queda' in (p.get('alertas') or []):
                try:
                    auditoria.episodio(self.dados_dir, self.owner_uid, tipo='queda',
                                       detalhe='Possível queda detectada', **comum)
                except Exception as e:
                    self._log(f"auditoria (queda) falhou: {e}")

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
        self.pipeline.configurar(novo['modo'])
        if self.running and self.pose_ok:
            threading.Thread(target=self._analyzer_for, args=(MODOS[novo['modo']],), daemon=True).start()
        return self.public_config()

    def public_config(self):
        return config_publica(self.config)

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
            self.model_path = model_path
            self.inference_model_path = self.detector.runtime_path
            self.runtime_backend = self.detector.backend
            self.model_name = os.path.basename(self.detector.runtime_path)
            self.model_classes = self.detector.class_names
            self.required_items = self.detector.clean_required(
                tax.normalize_required(required_items) if required_items else tax.default_required(self.detector.names))
            self.pose_ok = True
            self._analyzer_for(MODOS[self.mode])
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
        return self.pipeline.receber(seq, t, jpeg, sessao=session)

    def set_external_frame(self, img, seq=None):
        """Rotas antigas (JSON base64): sem horario de captura, usa a hora de chegada."""
        if self.running and self.use_external:
            self.pipeline.receber_imagem(img, largura_max=MODOS[self.mode]['largura_envio'])

    set_external_frame_b = set_external_frame  # rota paralela antiga (/stream_frame2)

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
                h, w = img.shape[:2]
                alvo = self.config['resolucao']
                if h > alvo:
                    s = alvo / h
                    img = cv2.resize(img, (int(w * s) // 2 * 2, alvo), interpolation=cv2.INTER_AREA)
                self.pipeline.receber_imagem(img, t=t0, largura_max=MODOS[self.mode]['largura_envio'])
            if is_file:  # arquivo toca na velocidade original
                time.sleep(max(0.0, file_delay - (time.perf_counter() - t0)))
        cap.release()
        self._log("Captura encerrada.")

    def _open_camera(self, source):
        if isinstance(source, str) and source.startswith('rtsp://'):
            cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        elif isinstance(source, str) and source.startswith(('http://', 'https://')):
            cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
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

    def _reconhecer_rostos(self, img):
        """Acha os rostos do quadro e compara cada um com a galeria de funcionarios.

        Mesmo caminho do projeto DEEPFAKE: o embedding do rosto vira um vetor
        unitario e a identidade sai da menor distancia. Quem nao bate com
        ninguem volta sem nome -- a caixa ainda aparece, so nao e atribuida."""
        if self.galeria is None or self.galeria.vazia:
            return []
        try:
            rostos = face_id.detectar(img, self.device)
        except Exception as e:
            self._log(f'reconhecimento de rosto falhou: {e}')
            return []
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

    def _analyze_key(self, img, t, perfil):
        det = self.detector
        h, w = img.shape[:2]
        resolvedor = self._resolvedor(w, h)
        # com zonas, o detector precisa procurar tudo que qualquer area possa exigir
        required = resolvedor.todos_os_itens() if resolvedor is not None else list(self.required_items)
        analyzer = self._analyzer_for(perfil) if self.pose_ok else None
        run_det = det.useful_for(required)
        # detector e pose rodam juntos na GPU (modelos diferentes, threads diferentes)
        fut = self._pool.submit(det.detect, img, perfil['imgsz'], perfil['conf']) if run_det else None
        people = analyzer.detect_people(img) if analyzer is not None else None
        dets = fut.result() if fut is not None else []
        if perfil['recortes'] and people and run_det:
            dets = merge_detections(dets, det.detect_crops(img, [p['box'] for p in people], imgsz=640,
                                                            conf=perfil['conf']))
        employees = self._run_extra_models(img, dets)
        employees += self._reconhecer_rostos(img)
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
        with self.pipeline.cond:
            count = self.pipeline.exibidos
            result, jpeg = self.pipeline.ultimo_resultado, self.pipeline.ultimo_jpeg
        if jpeg is None:
            return None
        with self._annot_lock:
            if self._annot[0] == count:
                return self._annot[1]
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
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
            'runtime_backend': self.runtime_backend,
            'inference_model_path': self.inference_model_path,
            'classes': list(self.model_classes),
            'required_items': list(self.required_items),
            'required_labels': [tax.item_label(i) for i in self.required_items],
            'zonas': list(self.zonas),
            'areas': [self._areas_por_id().get(z['area_id']) for z in self.zonas
                      if self._areas_por_id().get(z['area_id'])],
            'detections': list(r.get('detections', [])),
            'employees': list(r.get('employees', [])),
            'activity': r.get('activity', 'sem leitura'),
            'pose': self.pose_ok,
            'config': self.public_config(),
            'pipeline': stats,
            'frames_recebidos': len(self.pipeline.taxa_entrada.ts),
            'frames_descartados': stats['pulados'],
        }

    def stop(self):
        self.running = False
        if self.owner_uid:
            # camera parou: fecha os episodios abertos para nao ficarem com duracao aberta
            try:
                auditoria.encerrar_stream(self.owner_uid, self.client_id)
            except Exception:
                pass
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
