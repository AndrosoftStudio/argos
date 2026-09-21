"""
multistream.py — Recepção paralela de frames via múltiplos workers
v16.0: o frontend envia frames por 2 rotas simultâneas (/stream_frame e
       /stream_frame2), cada uma com seu próprio slot de buffer.
       O processor consome o mais recente disponível entre os dois,
       dobrando efetivamente a taxa de entrega sem travar em 1 req/vez.
"""
import threading
import time
import cv2
import numpy as np
import base64


class ParallelFrameBuffer:
    """
    Buffer de 2 slots (A e B). O frontend alterna entre /stream_frame (slot A)
    e /stream_frame2 (slot B). O processor consome sempre o slot mais recente.
    Isso permite que o browser envie um frame enquanto o outro ainda está
    sendo processado pelo backend — sem bloqueio.
    """
    def __init__(self):
        self._slots = [None, None]      # [img_A, img_B]
        self._times = [0.0, 0.0]
        self._counts = [0, 0]
        self._lock = threading.Lock()
        self._last_consumed = 0         # contador total consumido

    def put(self, slot: int, img):
        """Slot 0 = rota principal, slot 1 = rota alternativa."""
        with self._lock:
            self._slots[slot] = img.copy() if img is not None else None
            self._times[slot] = time.time()
            self._counts[slot] += 1

    def get_latest(self):
        """Retorna o frame mais recente entre os dois slots (ou None)."""
        with self._lock:
            t0, t1 = self._times
            s0, s1 = self._slots
            # Preferir o mais recente
            if s0 is None and s1 is None:
                return None, 0
            if s0 is None:
                img = s1.copy() if s1 is not None else None
                count = self._counts[1]
            elif s1 is None:
                img = s0.copy() if s0 is not None else None
                count = self._counts[0]
            elif t1 >= t0:
                img = s1.copy() if s1 is not None else None
                count = self._counts[1]
            else:
                img = s0.copy() if s0 is not None else None
                count = self._counts[0]
            return img, count

    def total_count(self):
        with self._lock:
            return self._counts[0] + self._counts[1]

    def last_time(self):
        with self._lock:
            return max(self._times)

    def clear(self):
        with self._lock:
            self._slots = [None, None]
            self._times = [0.0, 0.0]
