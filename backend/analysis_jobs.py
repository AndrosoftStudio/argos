"""
analysis_jobs.py - Analises de video nos modos detalhado e super detalhado.

O celular/navegador grava em alta taxa de quadros (MediaRecorder) e envia o video em partes;
aqui ele entra numa fila e e processado quadro a quadro, sem pressa de tempo real:

  detalhado        pose maior (yolo26s-pose), detector em 960 px, todos os quadros, rastreio.
  super detalhado  o detalhado e mais uma revisao: detector tambem em recortes ampliados de cada
                   pessoa, votacao temporal centrada (passado e futuro), video final gerado ja com
                   os estados revisados e quadros duvidosos salvos para reforcar o dataset.

Arquivos de cada analise: dados/users/<uid>/analises/<id>/
  job.json, entrada.<ext>, resultado.mp4 (ou .webm), miniaturas/, revisao/
"""
import copy
import json
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
import uuid
from collections import Counter, defaultdict, deque

import cv2

import ppe_taxonomy as tax
from epi_detector import (MODELS_DIR, EpiDetector, is_cuda, merge_detections, modelo_de_reforco, pose_model_path,
                          resolve_device, run_employee_model)
from ppe_analyzer import WEIGHTS, PPEAnalyzer, centered_states, draw_analysis, summarize
from reforco import Reforco

MODES = {
    'detalhado': {'nome': 'Detalhado', 'pose': 'detalhado', 'imgsz': 960, 'conf': 0.3, 'revisao': False},
    'super': {'nome': 'Super detalhado', 'pose': 'super', 'imgsz': 1280, 'conf': 0.25, 'revisao': True},
}
VIDEO_EXTS = ('.webm', '.mp4', '.mov', '.mkv', '.avi', '.m4v', '.3gp')
ACTIVE = ('processando', 'revisando', 'gerando_video')
MAX_OUTPUT_SIDE = 1280
# a gravacao chega em partes de ~1 s; 100 mil partes passam de 24 h de video
MAX_PARTES = 100000
_ID_RE = re.compile(r'^[a-f0-9]{12}$')


class _Cancelled(Exception):
    pass


def valid_job_id(jid):
    return bool(_ID_RE.match(str(jid or '')))


# ── Video ───────────────────────────────────────────────────────────
def _video_info(path, job):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError('Vídeo inválido ou formato não suportado')
    fps = cap.get(cv2.CAP_PROP_FPS)
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()
    # WebM do MediaRecorder costuma vir sem fps/duracao confiaveis: usa o que o cliente informou
    if not 1 <= fps <= 240:
        fps = float(job.get('fps_gravacao') or 30.0)
    duration = float(job.get('duracao_ms') or 0) / 1000 or (total / fps if 0 < total < 1e7 else 0.0)
    return {'fps': round(float(fps), 3), 'duracao': duration}


def _frame_time(cap, index, fps, previous):
    t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
    if t <= 0 and index > 0:
        t = index / fps
    return max(t, previous + 1e-3) if previous is not None else max(t, 0.0)


class _VideoOut:
    """Video anotado. Com ffmpeg no PATH converte para H.264 (toca em qualquer navegador);
    sem ffmpeg grava WebM/VP8 direto pelo OpenCV."""

    def __init__(self, folder, fps):
        self.folder, self.fps = folder, max(1.0, min(float(fps), 120.0))
        self.ffmpeg = shutil.which('ffmpeg')
        self.tmp = os.path.join(folder, 'resultado_tmp.mp4' if self.ffmpeg else 'resultado.webm')
        self.writer = None
        self.size = None

    def write(self, img):
        h, w = img.shape[:2]
        if self.size is None:
            s = min(1.0, MAX_OUTPUT_SIDE / max(h, w))
            self.size = (int(w * s) // 2 * 2, int(h * s) // 2 * 2)
            fourcc = cv2.VideoWriter_fourcc(*('mp4v' if self.ffmpeg else 'VP80'))
            self.writer = cv2.VideoWriter(self.tmp, fourcc, self.fps, self.size)
            if not self.writer.isOpened():
                raise RuntimeError('Não foi possível criar o vídeo de saída')
        if (w, h) != self.size:
            img = cv2.resize(img, self.size, interpolation=cv2.INTER_AREA)
        self.writer.write(img)

    def close(self):
        if self.writer is None:
            return None
        self.writer.release()
        if not self.ffmpeg:
            return os.path.basename(self.tmp)
        attempts = [
            ('resultado.mp4', ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
                               '-movflags', '+faststart']),
            ('resultado.webm', ['-c:v', 'libvpx-vp9', '-b:v', '0', '-crf', '36', '-deadline', 'realtime']),
        ]
        for name, args in attempts:
            dest = os.path.join(self.folder, name)
            r = subprocess.run([self.ffmpeg, '-y', '-loglevel', 'error', '-i', self.tmp, *args, dest],
                               capture_output=True, text=True)
            if r.returncode == 0 and os.path.exists(dest):
                os.remove(self.tmp)
                return name
        return os.path.basename(self.tmp)


# ── Linha do tempo, revisao e coleta para o dataset ────────────────
class _Timeline:
    """Eventos (EPI faltando, possivel queda), tempos por pessoa e miniaturas."""

    def __init__(self, cfg, folder, max_thumbs=60):
        self.cfg = cfg
        self.thumbs_dir = os.path.join(folder, 'miniaturas')
        self.max_thumbs = max_thumbs
        self.thumbs = 0
        self.open = {}
        self.events = []
        self.people = {}
        self.last_t = None
        self.frames = 0
        self.frames_with_people = 0
        self.frames_safe = 0

    def _thumb(self, img):
        if img is None or self.thumbs >= self.max_thumbs:
            return None
        os.makedirs(self.thumbs_dir, exist_ok=True)
        self.thumbs += 1
        name = f'{self.thumbs:03d}.jpg'
        h, w = img.shape[:2]
        s = 480 / max(h, w)
        small = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else img
        cv2.imwrite(os.path.join(self.thumbs_dir, name), small, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return name

    def add(self, t, analysis, annotated):
        dt = 0.0 if self.last_t is None else max(0.0, min(t - self.last_t, 1.0))
        self.last_t = t
        self.frames += 1
        persons = analysis.get('persons', [])
        if persons:
            self.frames_with_people += 1
            self.frames_safe += analysis.get('status') == 'seguro'
        active = {}
        for p in persons:
            tid = p['track_id']
            if tid is None:
                continue
            st = self.people.setdefault(tid, {'track_id': tid, 'nome': None, 'inicio': round(t, 2), 'fim': t,
                                              'faltando_s': Counter(), 'postura_s': Counter(), 'movimento_s': Counter()})
            st['fim'] = round(t, 2)
            st['nome'] = p.get('nome') or st['nome']
            st['postura_s'][p['postura']] += dt
            st['movimento_s'][p['movimento']] += dt
            for item in p['faltando']:
                st['faltando_s'][item] += dt
                if p['epis'][item]['alerta']:
                    active[(tid, 'epi_faltando', item)] = (p, t - p['epis'][item]['faltando_ha'])
            if 'Possível queda' in p['alertas']:
                active[(tid, 'possivel_queda', None)] = (p, t - self.cfg['fall_sec'])
        for key, (p, start) in active.items():
            ev = self.open.get(key)
            if ev is None:
                tid, kind, item = key
                ev = {'tipo': kind, 'track_id': tid, 'nome': p.get('nome'), 'item': item,
                      'rotulo': tax.item_label(item) if item else 'Possível queda',
                      'inicio': round(max(0.0, start), 2), 'miniatura': self._thumb(annotated)}
                self.open[key] = ev
                self.events.append(ev)
            ev['fim'] = round(t, 2)
            ev['duracao'] = round(ev['fim'] - ev['inicio'], 1)
        for key in [k for k in self.open if k not in active]:
            del self.open[key]

    def result(self):
        missing_total = Counter()
        people = []
        for st in self.people.values():
            missing_total.update(st['faltando_s'])
            people.append({**st, **{k: {a: round(b, 1) for a, b in st[k].items()}
                                    for k in ('faltando_s', 'postura_s', 'movimento_s')}})
        return {
            'eventos': self.events,
            'pessoas': people,
            'tempo_sem_epi_s': {k: round(v, 1) for k, v in missing_total.items()},
            'percentual_seguro': round(100.0 * self.frames_safe / self.frames_with_people, 1) if self.frames_with_people else None,
        }


class _Reviewer:
    """Estados revisados com janela centrada, lidos em ordem de quadro."""

    def __init__(self, records_path, cfg):
        self.cfg = cfg
        series = defaultdict(lambda: ([], [], []))  # (track, item) -> (quadros, tempos, pesos)
        with open(records_path, encoding='utf-8') as f:
            for i, line in enumerate(f):
                rec = json.loads(line)
                for p in rec['analise']['persons']:
                    if p['track_id'] is None:
                        continue
                    for item, source in p.get('evidencias', {}).items():
                        s = series[(p['track_id'], item)]
                        s[0].append(i)
                        s[1].append(rec['t'])
                        s[2].append(WEIGHTS[source])
        self.series = {key: (frames, centered_states(times, weights, cfg))
                       for key, (frames, times, weights) in series.items()}
        self.pos = defaultdict(int)
        self.missing_since = {}

    def _state(self, frame, key):
        frames, states = self.series.get(key, ((), ()))
        i = self.pos[key]
        while i < len(frames) and frames[i] < frame:
            i += 1
        self.pos[key] = i
        return states[i] if i < len(frames) and frames[i] == frame else None

    def apply(self, analysis, frame, t):
        for p in analysis['persons']:
            tid = p['track_id']
            if tid is None:
                continue
            missing = []
            for item, s in p['epis'].items():
                key = (tid, item)
                state = self._state(frame, key) or s['estado']
                if state == 'faltando':
                    since = self.missing_since.setdefault(key, t)
                    missing.append(item)
                else:
                    self.missing_since.pop(key, None)
                    since = None
                dur = t - since if since is not None else 0.0
                s.update(estado=state, faltando_ha=round(dur, 1), alerta=state == 'faltando' and dur >= self.cfg['alert_sec'])
            p['faltando'] = missing
            falls = [a for a in p['alertas'] if a == 'Possível queda']
            p['alertas'] = [f"Sem {tax.item_label(i).lower()} há {p['epis'][i]['faltando_ha']:.0f}s"
                            for i in missing if p['epis'][i]['alerta']] + falls
        return summarize(analysis['persons'])


class _ReviewCollector:
    """Guarda quadros duvidosos com pre-rotulos YOLO para corrigir e reforcar o dataset."""

    def __init__(self, folder, detector, max_frames=150, interval_s=1.0):
        self.folder = os.path.join(folder, 'revisao')
        self.max_frames, self.interval_s = max_frames, interval_s
        self.count, self.last_t = 0, -1e9
        self.argos = detector.class_names == tax.DATASET_CLASSES

    def maybe_save(self, frame, t, original, reviewed, detections):
        if self.count >= self.max_frames or t - self.last_t < self.interval_s:
            return
        changed = any(o['epis'].get(i, {}).get('estado') != s['estado']
                      for o, r in zip(original['persons'], reviewed['persons']) for i, s in r['epis'].items())
        doubtful = any(0.25 <= d['conf'] < 0.45 for d in detections)
        if not (changed or doubtful):
            return
        os.makedirs(self.folder, exist_ok=True)
        self.count += 1
        self.last_t = t
        name = f"{self.count:04d}_{int(t * 1000):09d}ms"
        cv2.imwrite(os.path.join(self.folder, name + '.jpg'), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if self.argos:
            h, w = frame.shape[:2]
            with open(os.path.join(self.folder, name + '.txt'), 'w') as f:
                for d in detections:
                    x1, y1, x2, y2 = d['box']
                    f.write(f"{d['cls']} {(x1 + x2) / 2 / w:.6f} {(y1 + y2) / 2 / h:.6f} {(x2 - x1) / w:.6f} {(y2 - y1) / h:.6f}\n")


def _det_json(d):
    return {'item': d['item'], 'present': d['present'], 'known': d['known'], 'conf': d['conf'],
            'box': [round(float(v), 1) for v in d['box']], 'label': d['label'], 'cls': d['cls']}


# ── Fila ────────────────────────────────────────────────────────────
class JobManager:
    def __init__(self, dados_dir, resolve_model, log=print):
        """resolve_model(uid, nome_do_modelo_ou_None) -> (caminho_do_modelo, epis_exigidos)."""
        self.dados_dir = dados_dir
        self.resolve_model = resolve_model
        self.log = log
        self._lock = threading.Condition()
        self._jobs = {}
        self._queue = deque()
        self._cancel = set()
        self._current = None
        self._last_save = {}
        self._load_existing()
        threading.Thread(target=self._worker, daemon=True, name='analises-video').start()

    def folder(self, uid, jid):
        return os.path.join(self.dados_dir, uid, 'analises', jid)

    def _save(self, job):
        folder = self.folder(job['uid'], job['id'])
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, 'job.json.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(job, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(folder, 'job.json'))
        self._last_save[job['id']] = time.time()

    def _update(self, job, force=True, **fields):
        with self._lock:
            job.update(fields, atualizado_em=time.time())
            if force or time.time() - self._last_save.get(job['id'], 0) > 1.0:
                self._save(job)

    def _load_existing(self):
        if not os.path.isdir(self.dados_dir):
            return
        pending = []
        for uid in os.listdir(self.dados_dir):
            base = os.path.join(self.dados_dir, uid, 'analises')
            if not os.path.isdir(base):
                continue
            for jid in os.listdir(base):
                try:
                    with open(os.path.join(base, jid, 'job.json'), encoding='utf-8') as f:
                        job = json.load(f)
                except (OSError, ValueError):
                    continue
                if job.get('status') in ACTIVE + ('na_fila', 'cancelando'):
                    job.update(status='na_fila', etapa='Na fila (servidor reiniciado)', progresso=0.0)
                    self._save(job)
                    pending.append(job)
                self._jobs[jid] = job
        for job in sorted(pending, key=lambda j: j.get('criado_em', 0)):
            self._queue.append(job['id'])

    def _get(self, uid, jid):
        job = self._jobs.get(jid)
        if not job or job['uid'] != uid:
            raise KeyError('Análise não encontrada')
        return job

    def _public(self, job, full=True):
        out = {k: v for k, v in job.items() if full or k != 'resultado'}
        if job['status'] == 'na_fila' and job['id'] in self._queue:
            out['posicao_fila'] = list(self._queue).index(job['id']) + (2 if self._current else 1)
        if not full and job.get('resultado'):
            r = job['resultado']
            out['resumo'] = {'eventos': len(r.get('eventos', [])), 'pessoas': len(r.get('pessoas', [])),
                             'percentual_seguro': r.get('percentual_seguro'), 'duracao_s': r.get('duracao_s')}
        return out

    # ── API usada pelas rotas ──
    def create(self, uid, mode, name='', origin='gravacao', model=None, extra=None):
        if mode not in MODES:
            raise ValueError('Modo inválido (use detalhado ou super)')
        jid = uuid.uuid4().hex[:12]
        now = time.time()
        job = {'id': jid, 'uid': uid, 'modo': mode, 'modo_nome': MODES[mode]['nome'],
               'nome': name or f"Análise {time.strftime('%d/%m %H:%M')}", 'origem': origin, 'modelo': model,
               'status': 'recebendo', 'etapa': 'Recebendo vídeo', 'progresso': 0.0, 'partes': 0, 'bytes': 0,
               'erro': None, 'resultado': None, 'criado_em': now, 'atualizado_em': now, **(extra or {})}
        with self._lock:
            self._jobs[jid] = job
            self._save(job)
            return self._public(job)

    def add_chunk(self, uid, jid, seq, data):
        if int(seq) >= MAX_PARTES:
            raise ValueError('Vídeo longo demais para uma análise só')
        with self._lock:
            job = self._get(uid, jid)
            if job['status'] != 'recebendo':
                raise ValueError('Esta análise não está recebendo vídeo')
        parts = os.path.join(self.folder(uid, jid), 'partes')
        os.makedirs(parts, exist_ok=True)
        dest = os.path.join(parts, f'{int(seq):06d}.part')
        with open(dest + '.tmp', 'wb') as f:
            f.write(data)
        os.replace(dest + '.tmp', dest)
        self._update(job, force=False, partes=max(job['partes'], int(seq) + 1), bytes=job['bytes'] + len(data))
        return {'ok': True, 'seq': int(seq)}

    def finish_upload(self, uid, jid, total_parts, ext='.webm', duration_ms=None, fps=None):
        with self._lock:
            job = self._get(uid, jid)
            if job['status'] != 'recebendo':
                raise ValueError('Esta análise não está recebendo vídeo')
        # o total vem do cliente: sem teto, um numero enorme travava o servidor neste laco
        if not 0 < int(total_parts) <= MAX_PARTES:
            raise ValueError('Número de partes do vídeo inválido')
        folder = self.folder(uid, jid)
        parts = os.path.join(folder, 'partes')
        missing = [i for i in range(int(total_parts)) if not os.path.exists(os.path.join(parts, f'{i:06d}.part'))]
        if missing or not total_parts:
            return {'ok': False, 'faltando': missing[:100]}
        ext = ext if ext in VIDEO_EXTS else '.webm'
        entrada = os.path.join(folder, 'entrada' + ext)
        with open(entrada, 'wb') as out:
            for i in range(int(total_parts)):
                with open(os.path.join(parts, f'{i:06d}.part'), 'rb') as f:
                    shutil.copyfileobj(f, out)
        shutil.rmtree(parts, ignore_errors=True)
        self._enqueue(job, entrada, duration_ms, fps)
        return {'ok': True, 'job': self.get(uid, jid)}

    def create_from_file(self, uid, mode, file_storage, name='', model=None):
        ext = os.path.splitext(file_storage.filename or '')[1].lower()
        job = self.create(uid, mode, name=name or file_storage.filename, origin='upload', model=model)
        entrada = os.path.join(self.folder(uid, job['id']), 'entrada' + (ext if ext in VIDEO_EXTS else '.mp4'))
        file_storage.save(entrada)
        with self._lock:
            internal = self._jobs[job['id']]
        self._enqueue(internal, entrada, None, None)
        return self.get(uid, job['id'])

    def _enqueue(self, job, entrada, duration_ms, fps):
        with self._lock:
            job.update(status='na_fila', etapa='Na fila', progresso=0.0, entrada=os.path.basename(entrada),
                       bytes=os.path.getsize(entrada), atualizado_em=time.time())
            if duration_ms:
                job['duracao_ms'] = float(duration_ms)
            if fps:
                job['fps_gravacao'] = float(fps)
            self._save(job)
            self._queue.append(job['id'])
            self._lock.notify_all()

    def get(self, uid, jid):
        with self._lock:
            try:
                return self._public(self._get(uid, jid))
            except KeyError:
                return None

    def list(self, uid):
        with self._lock:
            jobs = [self._public(j, full=False) for j in self._jobs.values() if j['uid'] == uid]
        return sorted(jobs, key=lambda j: -j.get('criado_em', 0))

    def remove(self, uid, jid):
        with self._lock:
            try:
                job = self._get(uid, jid)
            except KeyError:
                return False
            if self._current == jid:
                self._cancel.add(jid)
                job['status'] = 'cancelando'
                return True
            self._jobs.pop(jid, None)
            if jid in self._queue:
                self._queue.remove(jid)
        shutil.rmtree(self.folder(uid, jid), ignore_errors=True)
        return True

    def file_path(self, uid, jid, relative):
        """Caminho seguro de um arquivo de saida (video, miniatura, quadro de revisao)."""
        with self._lock:
            try:
                self._get(uid, jid)
            except KeyError:
                return None
        parts = str(relative or '').replace('\\', '/').split('/')
        ok = (len(parts) == 1 and parts[0] in ('resultado.mp4', 'resultado.webm')) or \
             (len(parts) == 2 and parts[0] in ('miniaturas', 'revisao') and re.match(r'^[\w.-]+\.(jpg|txt)$', parts[1]))
        if not ok:
            return None
        path = os.path.join(self.folder(uid, jid), *parts)
        return path if os.path.isfile(path) else None

    # ── Processamento ──
    def _worker(self):
        while True:
            with self._lock:
                while not self._queue:
                    self._lock.wait()
                jid = self._queue.popleft()
                job = self._jobs.get(jid)
                if not job or job['status'] != 'na_fila':
                    continue
                self._current = jid
            try:
                self._process(job)
            except _Cancelled:
                with self._lock:
                    self._jobs.pop(jid, None)
                shutil.rmtree(self.folder(job['uid'], jid), ignore_errors=True)
                self.log(f"[Analise {jid}] cancelada")
            except Exception as ex:
                self.log(f"[Analise {jid}] ERRO: {ex}\n{traceback.format_exc()}")
                self._update(job, status='erro', etapa='Erro', erro=str(ex))
            finally:
                with self._lock:
                    self._current = None
                    self._cancel.discard(jid)
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass

    def _check_cancel(self, jid):
        if jid in self._cancel:
            raise _Cancelled()

    def _progress(self, job, frames, t, info, started, phase, phases):
        frac = min(1.0, t / info['duracao']) if info['duracao'] else 0.0
        elapsed = max(time.time() - started, 1e-6)
        fields = {'progresso': round((phase - 1 + frac) / phases, 4), 'quadros_processados': frames,
                  'fps_processamento': round(frames / elapsed, 1)}
        if frac > 0.02:
            fields['restante_s'] = round(elapsed / frac * (1 - frac) + (phases - phase) * elapsed / frac * 0.5)
        self._update(job, force=False, **fields)

    def _process(self, job):
        uid, jid = job['uid'], job['id']
        mode = MODES[job['modo']]
        folder = self.folder(uid, jid)
        entrada = os.path.join(folder, job['entrada'])
        phases = 2 if mode['revisao'] else 1
        self._update(job, status='processando', etapa='Carregando modelos', progresso=0.0, iniciado_em=time.time())

        model_path, required = self.resolve_model(uid, job.get('modelo'))
        device = resolve_device('auto')
        detector = EpiDetector(model_path, device, use_tensorrt=False, log=self.log)
        required = detector.clean_required(
            tax.normalize_required(required) if required else tax.default_required(detector.names))
        analyzer = PPEAnalyzer(pose_model_path(mode['pose']), device=device, half=is_cuda(device), imgsz=mode['imgsz'])
        extra_path = modelo_de_reforco(MODELS_DIR, model_path, detector.arquitetura)
        extra = None
        if extra_path and os.path.abspath(extra_path) != os.path.abspath(model_path):
            try:
                extra = EpiDetector(extra_path, device, use_tensorrt=False, log=self.log)
            except Exception as e:
                self.log(f'Segunda opiniao indisponivel ({os.path.basename(extra_path)}): {e}')
        reforco = Reforco(detector, extra, analyzer.cfg['kp_conf'])
        employees_model = None
        employees_path = os.path.join(self.dados_dir, uid, 'models', 'funcionarios.pt')
        if os.path.exists(employees_path):
            from ultralytics import YOLO
            employees_model = YOLO(employees_path)
        run_detector = detector.useful_for(required)
        info = _video_info(entrada, job)
        records = os.path.join(folder, 'quadros.jsonl')

        out = None if mode['revisao'] else _VideoOut(folder, info['fps'])
        timeline = None if mode['revisao'] else _Timeline(analyzer.cfg, folder)
        self._update(job, etapa='Analisando quadro a quadro', epis_exigidos=required,
                     modelo_usado=os.path.basename(model_path), modelo_pose=os.path.basename(pose_model_path(mode['pose'])))
        started, frames, t = time.time(), 0, None
        cap = cv2.VideoCapture(entrada)
        with open(records, 'w', encoding='utf-8') as rec_file:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                self._check_cancel(jid)
                t = _frame_time(cap, frames, info['fps'], t)
                dets = detector.detect(frame, imgsz=mode['imgsz'], conf=mode['conf']) if run_detector else []
                people = analyzer.detect_people(frame)
                if mode['revisao'] and people and run_detector:
                    crops = detector.detect_crops(frame, [p['box'] for p in people], imgsz=640, conf=mode['conf'])
                    dets = merge_detections(dets, crops)
                if people and run_detector:  # segunda olhada so em quem esta encoberto ou sem evidencia
                    dets = reforco.aplicar(frame, people, dets, required, t, ja_recortou=mode['revisao'],
                                           conf=mode['conf'])
                employees = run_employee_model(employees_model, frame, device, is_cuda(device)) if employees_model else []
                analysis = analyzer.analyze(frame, dets, required, detector.supported, t=t, employees=employees, people=people)
                if out is not None:
                    annotated = draw_analysis(frame.copy(), analysis, dets)
                    out.write(annotated)
                    timeline.add(t, analysis, annotated)
                rec_file.write(json.dumps({'t': round(t, 4), 'analise': analysis,
                                           'deteccoes': [_det_json(d) for d in dets]}, ensure_ascii=False) + '\n')
                frames += 1
                if frames % 10 == 0:
                    self._progress(job, frames, t, info, started, 1, phases)
        cap.release()
        if frames == 0:
            raise RuntimeError('Nenhum quadro pôde ser lido do vídeo (formato não suportado?)')
        if t and not info['duracao']:
            info['duracao'] = t
        speed = round(frames / max(time.time() - started, 1e-6), 1)

        reviewed_frames = None
        if mode['revisao']:
            self._update(job, status='revisando', etapa='Revisando estados (passado e futuro)')
            reviewer = _Reviewer(records, analyzer.cfg)
            collector = _ReviewCollector(folder, detector)
            out, timeline = _VideoOut(folder, info['fps']), _Timeline(analyzer.cfg, folder)
            self._update(job, status='gerando_video', etapa='Gerando vídeo revisado')
            cap = cv2.VideoCapture(entrada)
            review_started = time.time()
            with open(records, encoding='utf-8') as rec_file:
                for i, line in enumerate(rec_file):
                    ok, frame = cap.read()
                    if not ok:
                        break
                    self._check_cancel(jid)
                    rec = json.loads(line)
                    original = rec['analise']
                    reviewed = reviewer.apply(copy.deepcopy(original), i, rec['t'])
                    annotated = draw_analysis(frame.copy(), reviewed, rec['deteccoes'])
                    out.write(annotated)
                    timeline.add(rec['t'], reviewed, annotated)
                    collector.maybe_save(frame, rec['t'], original, reviewed, rec['deteccoes'])
                    if i % 10 == 0:
                        self._progress(job, i + 1, rec['t'], info, review_started, 2, phases)
            cap.release()
            reviewed_frames = collector.count

        self._update(job, etapa='Finalizando vídeo', progresso=0.99)
        video = out.close()
        result = timeline.result()
        result.update({'duracao_s': round(info['duracao'], 2), 'fps_video': info['fps'], 'quadros': frames,
                       'fps_processamento': speed, 'epis_exigidos': required,
                       'rotulos': {i: tax.item_label(i) for i in required}, 'video': video,
                       'quadros_para_revisao': reviewed_frames})
        try:
            os.remove(records)
        except OSError:
            pass
        self._update(job, status='concluido', etapa='Concluído', progresso=1.0, resultado=result,
                     concluido_em=time.time(), restante_s=0)
        self.log(f"[Analise {jid}] concluida: {frames} quadros a {speed} q/s, {len(result['eventos'])} eventos")
