/* argos-stream.js — câmeras ao vivo do Argos EPI. Usado por cam.html (celular) e index.html (painel).
   Sem dependências.

   Os três modos (tempo real, detalhado, super) são ao vivo: a câmera manda quadros com o horário de
   captura, o servidor analisa com um atraso fixo por modo e devolve o resultado de cada quadro no
   ritmo original. O modo, o fps e a resolução são escolhidos no painel; a câmera só obedece.

   LiveSender  captura, codifica e envia (WebSocket, com HTTP de reserva). Guarda os quadros até o
               servidor confirmar: se a conexão cai, reenvia os que ainda cabem no atraso do modo.
               Ajusta qualidade, tamanho e fps à rede.
   LivePlayer  mostra quadro + caixas sincronizados pelo número do quadro, com buffer contra oscilação.
   LiveViewer  painel assistindo um stream do servidor (vídeo + resultado pelo WebSocket /ws/view).
*/
(function () {
  'use strict';

  const COLORS = { ok: '#48bb58', faltando: '#dc3030', verificando: '#f5b000', nao_visivel: '#969696' };
  const LABELS = {
    capacete: 'Capacete', colete: 'Colete', luvas: 'Luvas', botas: 'Calçado', oculos: 'Óculos',
    mascara: 'Máscara', protetor_auricular: 'Protetor auricular', protetor_facial: 'Protetor facial',
    vestimenta: 'Vestimenta',
  };
  const REGION = {
    capacete: 'head', oculos: 'head', mascara: 'head', protetor_auricular: 'head', protetor_facial: 'head',
    colete: 'torso', vestimenta: 'torso', luvas: 'hands', botas: 'feet',
  };
  const POSTURE = { em_pe: 'em pé', sentado: 'sentado', agachado: 'agachado', curvado: 'curvado', caido: 'caído', desconhecida: '' };
  const HEAD = {
    frente: 'de frente', virada_direita: 'virada à direita', virada_esquerda: 'virada à esquerda',
    perfil_direita: 'perfil direito', perfil_esquerda: 'perfil esquerdo', de_costas: 'de costas', desconhecida: '—',
  };
  const MODES = {
    tempo_real: { nome: 'Tempo real', icone: '⚡', descricao: 'Resposta mais rápida (atraso de ~0,4 s).' },
    detalhado: { nome: 'Detalhado', icone: '🎯', descricao: 'Modelos maiores e decisão com passado e futuro (atraso de ~1,5 s).' },
    super: { nome: 'Super detalhado', icone: '🔬', descricao: 'O mais preciso: revisa cada pessoa ampliada (atraso de ~4 s).' },
  };
  const FPS_OPTIONS = [15, 30, 60];
  const RES_OPTIONS = [480, 720, 1080];
  const RANK = { faltando: 3, verificando: 2, ok: 1, nao_visivel: 0 };
  // [ponto a, ponto b, região que colore o segmento] — 17 pontos COCO
  const SKELETON = [
    [0, 1, 'head'], [0, 2, 'head'], [1, 3, 'head'], [2, 4, 'head'],
    [5, 6, 'torso'], [5, 11, 'torso'], [6, 12, 'torso'], [11, 12, 'torso'],
    [5, 7, 'hands'], [7, 9, 'hands'], [6, 8, 'hands'], [8, 10, 'hands'],
    [11, 13, 'feet'], [13, 15, 'feet'], [12, 14, 'feet'], [14, 16, 'feet'],
  ];

  // ── Ícones de EPI desenhados em canvas ────────────────────────────
  // Cada glifo é desenhado num quadrado 0..1 e escalado na hora. Vetorial de
  // propósito: nada de imagem externa, funciona offline e fica nítido em
  // qualquer resolução de câmera.
  const EPI_ICONS = {
    capacete(c) {                       // domo + aba
      c.beginPath(); c.arc(0.5, 0.56, 0.3, Math.PI, 0, false); c.fill();
      c.beginPath(); c.roundRect(0.1, 0.56, 0.8, 0.14, 0.07); c.fill();
      c.beginPath(); c.roundRect(0.46, 0.24, 0.08, 0.2, 0.04); c.fill();
    },
    oculos(c) {                         // duas lentes + ponte + hastes
      c.lineWidth = 0.09; c.lineCap = 'round';
      c.beginPath(); c.arc(0.29, 0.52, 0.19, 0, Math.PI * 2); c.stroke();
      c.beginPath(); c.arc(0.71, 0.52, 0.19, 0, Math.PI * 2); c.stroke();
      c.beginPath(); c.moveTo(0.48, 0.5); c.lineTo(0.52, 0.5); c.stroke();
      c.beginPath(); c.moveTo(0.1, 0.46); c.lineTo(0.02, 0.38); c.stroke();
      c.beginPath(); c.moveTo(0.9, 0.46); c.lineTo(0.98, 0.38); c.stroke();
    },
    luvas(c) {                          // mão com quatro dedos e polegar
      c.beginPath(); c.roundRect(0.3, 0.42, 0.44, 0.44, 0.1); c.fill();
      for (let i = 0; i < 4; i++) {     // dedos
        c.beginPath(); c.roundRect(0.33 + i * 0.11, 0.18 + (i === 0 ? 0.06 : 0), 0.085, 0.3, 0.045); c.fill();
      }
      c.beginPath(); c.roundRect(0.12, 0.5, 0.2, 0.1, 0.05); c.fill();   // polegar
    },
    botas(c) {                          // bota de cano com solado
      c.beginPath(); c.moveTo(0.26, 0.16); c.lineTo(0.52, 0.16); c.lineTo(0.52, 0.56);
      c.lineTo(0.86, 0.62); c.lineTo(0.86, 0.74); c.lineTo(0.26, 0.74); c.closePath(); c.fill();
      c.beginPath(); c.roundRect(0.16, 0.74, 0.74, 0.12, 0.05); c.fill();  // solado
    },
    mascara(c) {                        // respirador: corpo trapezoidal + pregas + tirantes
      c.lineWidth = 0.075; c.lineCap = 'round';
      c.beginPath(); c.moveTo(0.2, 0.36); c.lineTo(0.06, 0.26); c.stroke();   // tirante esq.
      c.beginPath(); c.moveTo(0.8, 0.36); c.lineTo(0.94, 0.26); c.stroke();   // tirante dir.
      c.beginPath();                    // corpo: largo em cima, base arredondada
      c.moveTo(0.2, 0.34); c.lineTo(0.8, 0.34);
      c.quadraticCurveTo(0.79, 0.68, 0.64, 0.78);
      c.quadraticCurveTo(0.5, 0.85, 0.36, 0.78);
      c.quadraticCurveTo(0.21, 0.68, 0.2, 0.34);
      c.closePath(); c.fill();
      c.globalAlpha *= 0.4;             // pregas do respirador
      c.lineWidth = 0.055;
      c.beginPath(); c.moveTo(0.25, 0.48); c.lineTo(0.75, 0.48); c.stroke();
      c.beginPath(); c.moveTo(0.29, 0.6); c.lineTo(0.71, 0.6); c.stroke();
    },
    colete(c) {                         // colete com faixas refletivas
      c.beginPath(); c.moveTo(0.2, 0.18); c.lineTo(0.38, 0.18); c.lineTo(0.5, 0.36);
      c.lineTo(0.62, 0.18); c.lineTo(0.8, 0.18); c.lineTo(0.84, 0.88); c.lineTo(0.16, 0.88);
      c.closePath(); c.fill();
      c.globalAlpha *= 0.45;            // faixas mais claras sobre o colete
      c.beginPath(); c.roundRect(0.2, 0.56, 0.6, 0.08, 0.04); c.fill();
    },
    vestimenta(c) {                     // macacão / vestimenta de corpo
      c.beginPath(); c.moveTo(0.22, 0.16); c.lineTo(0.78, 0.16); c.lineTo(0.84, 0.42);
      c.lineTo(0.68, 0.42); c.lineTo(0.7, 0.88); c.lineTo(0.54, 0.88); c.lineTo(0.5, 0.58);
      c.lineTo(0.46, 0.88); c.lineTo(0.3, 0.88); c.lineTo(0.32, 0.42); c.lineTo(0.16, 0.42);
      c.closePath(); c.fill();
    },
    protetor_auricular(c) {             // abafador: arco + duas conchas
      c.lineWidth = 0.1; c.lineCap = 'round';
      c.beginPath(); c.arc(0.5, 0.5, 0.33, Math.PI * 1.08, Math.PI * 1.92); c.stroke();
      c.beginPath(); c.roundRect(0.06, 0.44, 0.22, 0.38, 0.1); c.fill();
      c.beginPath(); c.roundRect(0.72, 0.44, 0.22, 0.38, 0.1); c.fill();
    },
    protetor_facial(c) {                // faixa de cabeça + viseira larga e reta
      c.beginPath(); c.roundRect(0.12, 0.14, 0.76, 0.15, 0.075); c.fill();   // faixa
      const a = c.globalAlpha;
      c.globalAlpha = a * 0.42;         // a viseira é uma placa transparente
      c.beginPath();
      c.moveTo(0.08, 0.31); c.lineTo(0.92, 0.31); c.lineTo(0.92, 0.62);
      c.quadraticCurveTo(0.92, 0.88, 0.5, 0.92);
      c.quadraticCurveTo(0.08, 0.88, 0.08, 0.62);
      c.closePath(); c.fill();
      c.globalAlpha = a * 0.85;         // brilho vertical: lê como vidro
      c.lineWidth = 0.05; c.lineCap = 'round';
      c.beginPath(); c.moveTo(0.28, 0.4); c.lineTo(0.24, 0.72); c.stroke();
      c.globalAlpha = a;
    },
  };
  // fallback: um escudo, para EPI cadastrado pelo usuário fora da taxonomia
  const EPI_ICON_FALLBACK = (c) => {
    c.beginPath(); c.moveTo(0.5, 0.12); c.lineTo(0.86, 0.26); c.lineTo(0.86, 0.54);
    c.quadraticCurveTo(0.86, 0.8, 0.5, 0.9); c.quadraticCurveTo(0.14, 0.8, 0.14, 0.54);
    c.lineTo(0.14, 0.26); c.closePath(); c.fill();
  };

  /** Desenha o ícone do EPI conforme o estado.
   *  ok          -> verde, opaco ("aceso")
   *  faltando    -> vermelho e bem apagado (quase invisível)
   *  verificando -> âmbar, meio-termo
   *  nao_visivel -> cinza apagado                                          */
  function drawEpiIcon(ctx, item, estado, x, y, size) {
    const cor = COLORS[estado] || COLORS.nao_visivel;
    const alpha = estado === 'ok' ? 1 : estado === 'verificando' ? 0.65 : estado === 'faltando' ? 0.3 : 0.22;
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(size, size);
    // fundo do selo, para o ícone não sumir sobre cena clara
    ctx.globalAlpha = estado === 'ok' ? 0.28 : 0.14;
    ctx.fillStyle = cor;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(-0.12, -0.12, 1.24, 1.24, 0.22); else ctx.rect(-0.12, -0.12, 1.24, 1.24);
    ctx.fill();
    // o glifo
    ctx.globalAlpha = alpha;
    ctx.fillStyle = cor;
    ctx.strokeStyle = cor;
    (EPI_ICONS[item] || EPI_ICON_FALLBACK)(ctx);
    // quem está OK ganha um brilho leve; quem falta fica só apagado
    if (estado === 'ok') {
      ctx.globalAlpha = 0.5;
      ctx.lineWidth = 0.06;
      ctx.strokeStyle = '#eafff0';
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(-0.12, -0.12, 1.24, 1.24, 0.22); else ctx.rect(-0.12, -0.12, 1.24, 1.24);
      ctx.stroke();
    }
    ctx.restore();
  }

  /** Fileira de ícones dos EPIs cobrados desta pessoa. Devolve o y livre acima. */
  function drawEpiRow(ctx, pessoa, xEsq, yBase, largura) {
    const epis = pessoa.epis || {};
    const itens = Object.keys(epis);
    if (!itens.length) return yBase;
    // ordem estável (de cima do corpo para baixo), não a ordem que veio do backend
    const ORDEM = ['capacete', 'protetor_facial', 'oculos', 'mascara', 'protetor_auricular',
                   'colete', 'vestimenta', 'luvas', 'botas'];
    itens.sort((a, b) => {
      const ia = ORDEM.indexOf(a), ib = ORDEM.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    // tamanho pelo espaço disponível: legível mesmo com pessoa pequena no quadro,
    // sem deixar a fileira maior que ~1,3x a largura da pessoa quando há muitos EPIs
    const alvo = Math.max(largura * 1.3, 140);
    const size = Math.max(16, Math.min(30, alvo / itens.length * 0.8));
    const gap = size * 0.22;
    const total = itens.length * size + (itens.length - 1) * gap;
    // não deixa a fileira sair da tela
    const x0 = Math.max(2, Math.min(xEsq, ctx.canvas.clientWidth - total - 2));
    const y0 = Math.max(2, yBase - size);
    itens.forEach((item, i) => {
      drawEpiIcon(ctx, item, (epis[item] || {}).estado, x0 + i * (size + gap), y0, size);
    });
    return y0 - 4;
  }

  const joinUrl = (base, path) => String(base || '').replace(/\/+$/, '') + path;
  const itemLabel = (item) => LABELS[item] || String(item || '').replace(/_/g, ' ');
  const now = () => performance.now();
  const randomId = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  const backoff = (attempt) => Math.min(8000, 400 * 2 ** attempt) * (0.7 + Math.random() * 0.6);

  function wsUrl(backendUrl, path, params) {
    const url = new URL(joinUrl(backendUrl, path), location.href);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
    return url.toString();
  }

  // 'AG' + versão + reservado + seq (uint32) + horário de captura em ms (float64) + JPEG
  function packFrame(seq, tMs, bytes) {
    const buf = new Uint8Array(16 + bytes.byteLength);
    buf[0] = 0x41; buf[1] = 0x47; buf[2] = 1; buf[3] = 0;
    const dv = new DataView(buf.buffer);
    dv.setUint32(4, seq >>> 0);
    dv.setFloat64(8, tMs);
    buf.set(new Uint8Array(bytes), 16);
    return buf;
  }

  function unpackFrame(ab) {
    const u = new Uint8Array(ab);
    if (u.length < 17 || u[0] !== 0x41 || u[1] !== 0x47) return null;
    const dv = new DataView(ab);
    return { seq: dv.getUint32(4), t: dv.getFloat64(8) / 1000, blob: new Blob([u.subarray(16)], { type: 'image/jpeg' }) };
  }

  async function openCamera({ fps = 30, height = 720, facingMode = 'environment', screen = false } = {}) {
    const video = { frameRate: { ideal: fps }, height: { ideal: height } };
    if (screen) return navigator.mediaDevices.getDisplayMedia({ video, audio: false });
    try {
      return await navigator.mediaDevices.getUserMedia({ video: { ...video, facingMode }, audio: false });
    } catch (e) {
      if (e && (e.name === 'NotAllowedError' || e.name === 'SecurityError')) throw e;
      // aparelho recusou fps/resolução pedidos: abre com o padrão dele
      return navigator.mediaDevices.getUserMedia({ video: { facingMode }, audio: false });
    }
  }

  function trackInfo(stream) {
    const track = stream && stream.getVideoTracks()[0];
    const s = (track && track.getSettings && track.getSettings()) || {};
    return { fps: s.frameRate || 0, width: s.width || 0, height: s.height || 0 };
  }

  /* ── Envio ─────────────────────────────────────────────────────── */
  class LiveSender {
    constructor(opts) {
      Object.assign(this, { backendUrl: '' }, opts);
      this.session = randomId();
      this.config = { modo: 'tempo_real', fps: 30, resolucao: 720, largura_envio: 960, janela_s: 1.7 };
      this.active = false;
      this.transport = null;       // 'ws' | 'http' | null (desconectado)
      this.ws = null;
      this.wsAttempt = 0;
      this.wsFailures = 0;
      this.seq = 0;
      this.ackRecv = -1;
      this.lastResultSeq = -1;
      this.lastAckAt = 0;
      this.lastMsgAt = 0;
      this.outbox = [];            // {seq, tMs, data, sent, at} em ordem de seq
      this.outboxBytes = 0;
      this.quality = 0.72;
      this.scale = 1;
      this.fpsFactor = 1;
      this.avgFrame = 60000;
      this.encoding = 0;
      this.lastCapture = 0;
      this.httpInFlight = 0;
      this.rtt = 0;
      this.server = {};
      this.congested = 0;
      this.clear = 0;
      this.c = { captured: 0, sent: 0, bytes: 0, dropped: 0, results: 0 };
    }

    get targetFps() { return Math.max(3, this.config.fps * this.fpsFactor); }

    start() {
      this.active = true;
      this._statsAt = now();
      this._timers = [
        setInterval(() => this._emitStats(), 1000),
        setInterval(() => this._adapt(), 500),
        setInterval(() => this._heartbeat(), 1000),
      ];
      this._connectWs();
      this._captureLoop();
    }

    stop() {
      this.active = false;
      (this._timers || []).forEach(clearInterval);
      clearTimeout(this._retryTimer);
      clearTimeout(this._capTimer);
      const ws = this.ws;
      this.ws = null;
      if (ws) {
        try { if (ws.readyState === 1) ws.send(JSON.stringify({ type: 'parar' })); ws.close(); } catch (e) { /* já fechado */ }
      }
      this.outbox = [];
      this.outboxBytes = 0;
      this.transport = null;
    }

    /* Quadro já enviado, para o player mostrar junto com o resultado. */
    frameBlob(seq) {
      const e = this._find(seq);
      return e ? new Blob([e.data.subarray(16)], { type: 'image/jpeg' }) : null;
    }

    _find(seq) {
      const o = this.outbox;
      let lo = 0, hi = o.length - 1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (o[mid].seq === seq) return o[mid];
        if (o[mid].seq < seq) lo = mid + 1; else hi = mid - 1;
      }
      return null;
    }

    _setTransport(t) {
      if (this.transport === t) return;
      this.transport = t;
      this.onStatus && this.onStatus(t || 'reconectando');
      if (t === 'http') this._httpPump();
    }

    _applyConfig(cfg, janela) {
      if (!cfg) return;
      const old = this.config;
      this.config = { ...old, ...cfg, janela_s: janela || cfg.janela_s || old.janela_s };
      if (old.modo !== this.config.modo || old.fps !== this.config.fps || old.resolucao !== this.config.resolucao) {
        this.fpsFactor = 1;
        this.scale = 1;
        this.onConfig && this.onConfig(this.config, old);
      }
    }

    // ── conexão ──
    _connectWs() {
      if (!this.active || !('WebSocket' in window)) { if (this.active) this._setTransport('http'); return; }
      let ready = false;
      // ultimo_res: o servidor reenvia os resultados exibidos enquanto a conexão estava fora
      const ws = new WebSocket(wsUrl(this.backendUrl, '/ws/stream', {
        token: this.token, stream_id: this.streamId, sessao: this.session, ultimo_res: String(this.lastResultSeq),
      }));
      ws.binaryType = 'arraybuffer';
      const giveUp = setTimeout(() => { if (!ready) try { ws.close(); } catch (e) { /* */ } }, 6000);
      ws.onmessage = (ev) => {
        this.lastMsgAt = now();
        let d;
        try { d = JSON.parse(ev.data); } catch (e) { return; }
        switch (d.type) {
          case 'quadro': this._onResult(d); break;
          case 'ack': this._onAck(d.seq); this.server = d; break;
          case 'pronto':
            ready = true;
            clearTimeout(giveUp);
            this.ws = ws;
            this.wsAttempt = 0;
            this.wsFailures = 0;
            this._applyConfig(d.config, d.janela_s);
            this.onInfo && this.onInfo(d);
            this._resume(d.ultimo_seq);
            this._setTransport('ws');
            break;
          case 'config': this._applyConfig(d.config, d.janela_s); break;
          case 'pong': if (d.t) this.rtt = this.rtt ? this.rtt * 0.7 + (now() - d.t) * 0.3 : now() - d.t; break;
          case 'removido': this.onStatus && this.onStatus('removido'); this.stop(); break;
          case 'erro': this.onStatus && this.onStatus('erro', d.error); break;
          default: break;
        }
      };
      ws.onclose = () => {
        clearTimeout(giveUp);
        if (this.ws === ws) this.ws = null;
        if (!this.active) return;
        if (!ready) this.wsFailures++;
        // sem WebSocket por duas tentativas seguidas: segue por HTTP e continua tentando o WS
        this._setTransport(this.wsFailures >= 2 ? 'http' : (this.transport === 'http' ? 'http' : null));
        this._retryTimer = setTimeout(() => this._connectWs(), this.transport === 'http' ? 15000 : backoff(this.wsAttempt++));
      };
    }

    _heartbeat() {
      const ws = this.ws;
      if (!ws || ws.readyState !== 1) return;
      if (now() - this.lastMsgAt > 7000) { try { ws.close(); } catch (e) { /* */ } return; }  // conexão morta
      try { ws.send(JSON.stringify({ type: 'ping', t: now() })); } catch (e) { /* */ }
    }

    /* Reconectou: o servidor diz até onde recebeu; o resto volta para a fila, se ainda servir. */
    _resume(lastSeq) {
      this._onAck(lastSeq);
      for (const e of this.outbox) e.sent = false;
      this._pump();
    }

    _onAck(seq) {
      if (typeof seq !== 'number' || seq < 0) return;
      this.lastAckAt = now();
      if (seq > this.ackRecv) this.ackRecv = seq;
    }

    // ── captura ──
    _captureLoop() {
      const v = this.video;
      let lastCb = 0;
      const onFrame = (t, meta) => {
        lastCb = now();
        if (!this.active) return;
        this._maybeCapture();
        v.requestVideoFrameCallback(onFrame);
      };
      if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(onFrame);
      const timer = () => {
        if (!this.active) return;
        // sem requestVideoFrameCallback (ou aba em segundo plano): usa o relógio
        if (now() - lastCb > 250) this._maybeCapture();
        this._capTimer = setTimeout(timer, Math.max(4, 500 / this.targetFps));
      };
      timer();
    }

    _maybeCapture() {
      const t = now();
      if (t - this.lastCapture < 1000 / this.targetFps - 4) return;
      if (this.encoding >= 2) { this.c.dropped++; return; }
      const v = this.video;
      if (!v.videoWidth) return;
      this.lastCapture = t;
      const maxW = Math.min(v.videoWidth, this.config.largura_envio || 1280) * this.scale;
      const s = Math.min(1, maxW / v.videoWidth);
      const w = Math.round(v.videoWidth * s / 2) * 2;
      const h = Math.round(v.videoHeight * s / 2) * 2;
      this.canvases = this.canvases || [];
      let canvas = this.canvases.find((c) => !c.busy);
      if (!canvas) { canvas = document.createElement('canvas'); this.canvases.push(canvas); }
      canvas.busy = true;
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; canvas.ctx2d = null; }
      canvas.ctx2d = canvas.ctx2d || canvas.getContext('2d', { alpha: false });
      canvas.ctx2d.drawImage(v, 0, 0, w, h);
      const seq = this.seq++;
      this.encoding++;
      this.c.captured++;
      canvas.toBlob(async (blob) => {
        try {
          if (!blob || !this.active) return;
          const data = packFrame(seq, t, await blob.arrayBuffer());
          this.avgFrame = this.avgFrame * 0.9 + data.byteLength * 0.1;
          this._enqueue({ seq, tMs: t, data, sent: false, at: t });
        } finally {
          this.encoding--;
          canvas.busy = false;
        }
      }, 'image/jpeg', this.quality);
    }

    _enqueue(e) {
      const o = this.outbox;
      let i = o.length;
      while (i > 0 && o[i - 1].seq > e.seq) i--;
      o.splice(i, 0, e);
      this.outboxBytes += e.data.byteLength;
      this._trim();
      this._pump();
    }

    /* Tira da fila o que o servidor já recebeu e o que ficou velho demais para servir. */
    _trim() {
      const t = now();
      const keepMs = (this.config.janela_s || 1.7) * 1000;
      const o = this.outbox;
      // o player ainda precisa do quadro até o resultado voltar (atraso do modo + folga)
      const displayMs = keepMs + 2500;
      let n = 0;
      while (n < o.length && (t - o[n].at > displayMs || (o[n].seq <= this.ackRecv && t - o[n].at > keepMs + 1500))) n++;
      for (const e of o.slice(0, n)) {
        this.outboxBytes -= e.data.byteLength;
        if (!e.sent) this.c.dropped++;
      }
      if (n) o.splice(0, n);
      while (this.outboxBytes > 80e6 && o.length) {
        const e = o.shift();
        this.outboxBytes -= e.data.byteLength;
      }
    }

    _pump() {
      if (this.transport === 'http') { this._httpPump(); return; }
      const ws = this.ws;
      if (!ws || ws.readyState !== 1) return;
      const t = now();
      const keepMs = (this.config.janela_s || 1.7) * 1000;
      const limit = Math.max(400000, this.avgFrame * this.targetFps * 0.5);
      const realtime = this.config.modo === 'tempo_real';
      for (const e of this.outbox) {
        if (e.sent || e.seq <= this.ackRecv) continue;
        if (ws.bufferedAmount > limit) break;
        // tempo real: quadro velho não serve; nos detalhados, serve enquanto couber no atraso
        if (t - e.at > (realtime ? 700 : keepMs)) { e.sent = true; this.c.dropped++; continue; }
        try { ws.send(e.data); } catch (err) { return; }
        e.sent = true;
        this.c.sent++;
        this.c.bytes += e.data.byteLength;
      }
      if (this.outbox.some((e) => !e.sent) && !this._pumpTimer) {
        this._pumpTimer = setTimeout(() => { this._pumpTimer = null; this._pump(); }, 30);
      }
    }

    async _httpPump() {
      if (this.transport !== 'http' || !this.active) return;
      while (this.httpInFlight < 3 && this.transport === 'http' && this.active) {
        const t = now();
        const e = this.outbox.find((x) => !x.sent && x.seq > this.ackRecv && t - x.at <= (this.config.janela_s || 1.7) * 1000);
        if (!e) break;
        e.sent = true;
        this._postHttp(e);
      }
    }

    async _postHttp(e) {
      this.httpInFlight++;
      try {
        const r = await fetch(joinUrl(this.backendUrl, '/stream_frame_bin'), {
          method: 'POST',
          body: e.data.subarray(16),
          headers: {
            Authorization: 'Bearer ' + this.token, 'Content-Type': 'image/jpeg', 'ngrok-skip-browser-warning': 'true',
            'X-Stream-Id': this.streamId, 'X-Frame-Seq': String(e.seq), 'X-Frame-T': String(e.tMs), 'X-Sessao': this.session,
          },
        });
        if (r.status === 410) { this.onStatus && this.onStatus('removido'); this.stop(); return; }
        if (r.status === 401) { this.onStatus && this.onStatus('erro', 'Sessão inválida ou token revogado'); return; }
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const d = await r.json();
        this.lastMsgAt = now();
        this.c.sent++;
        this.c.bytes += e.data.byteLength;
        this._onAck(d.ack);
        this._applyConfig(d.config, d.janela_s);
        for (const res of d.resultados || []) this._onResult(res);
      } catch (err) {
        e.sent = false;  // tenta de novo enquanto ainda servir
        await new Promise((res) => setTimeout(res, 300));
      } finally {
        this.httpInFlight--;
        this._httpPump();
      }
    }

    _onResult(d) {
      if (typeof d.seq === 'number' && d.seq > this.lastResultSeq) this.lastResultSeq = d.seq;
      this.c.results++;
      this.onResult && this.onResult(d);
    }

    /* Rede apertada: baixa qualidade, depois tamanho, depois fps. Folgada: sobe na ordem inversa. */
    _adapt() {
      if (!this.active) return;
      this._trim();
      const queued = this.ws ? this.ws.bufferedAmount : this.httpInFlight * this.avgFrame;
      const unacked = this.outbox.filter((e) => e.sent && e.seq > this.ackRecv).length;
      const budget = this.avgFrame * this.targetFps * 0.35;
      const congested = this.transport && (queued > budget || unacked > this.targetFps * 1.2);
      if (congested) {
        this.clear = 0;
        if (++this.congested >= 2) {
          this.congested = 0;
          if (this.quality > 0.5) this.quality = Math.max(0.5, this.quality - 0.06);
          else if (this.scale > 0.55) this.scale = Math.max(0.55, this.scale - 0.1);
          else if (this.fpsFactor > 0.25) this.fpsFactor = Math.max(0.25, this.fpsFactor - 0.15);
        }
      } else if (this.transport) {
        this.congested = 0;
        if (++this.clear >= 8) {
          this.clear = 0;
          if (this.fpsFactor < 1) this.fpsFactor = Math.min(1, this.fpsFactor + 0.1);
          else if (this.scale < 1) this.scale = Math.min(1, this.scale + 0.1);
          else if (this.quality < 0.72) this.quality = Math.min(0.72, this.quality + 0.04);
        }
      }
    }

    _emitStats() {
      const t = now();
      const dt = Math.max(0.001, (t - this._statsAt) / 1000);
      const s = {
        transport: this.transport || 'reconectando', mode: this.transport,
        captureFps: this.c.captured / dt, sentFps: this.c.sent / dt, resultFps: this.c.results / dt,
        dropped: this.c.dropped, kbps: (this.c.bytes * 8) / 1000 / dt, rtt: this.rtt,
        quality: this.quality, scale: this.scale, targetFps: this.targetFps,
        pending: this.outbox.filter((e) => !e.sent).length,
        serverDelay: this.server.atraso_ms, serverFps: this.server.fps_analise, shownFps: this.server.fps_exibido,
      };
      this.c = { captured: 0, sent: 0, bytes: 0, dropped: 0, results: 0 };
      this._statsAt = t;
      this.onStats && this.onStats(s);
    }
  }

  /* ── Exibição ──────────────────────────────────────────────────── */
  class LivePlayer {
    constructor({ canvas, getFrame = null, onStats = null, showBoxes = true }) {
      this.canvas = canvas;
      this.getFrame = getFrame;
      this.onStats = onStats;
      this.showBoxes = showBoxes;
      this.entries = new Map();  // seq -> {seq, t, result, blob, bitmap, arrival}
      this.offsets = [];         // [chegada, chegada - t]
      this.base = null;
      this.jb = 120;             // buffer contra oscilação da rede (ms)
      this.current = null;
      this.lastArrival = 0;
      this.shown = 0;
      this.annotated = null;     // reserva: imagem já desenhada pelo servidor
      this.closed = false;
      this.message = '';
      this._statsAt = now();
      this._raf = requestAnimationFrame(() => this._render());
    }

    close() {
      this.closed = true;
      cancelAnimationFrame(this._raf);
      this.clear();
    }

    clear() {
      for (const e of this.entries.values()) if (e.bitmap) e.bitmap.close();
      this.entries.clear();
      if (this.current && this.current.bitmap) this.current.bitmap.close();
      this.current = null;
      this.base = null;
      this.offsets = [];
      this.annotated = null;
      this._paint();
    }

    setMessage(text) { this.message = text || ''; }

    get latestResult() { return this.current ? this.current.result : null; }

    _entry(seq) {
      let e = this.entries.get(seq);
      if (!e) { e = { seq }; this.entries.set(seq, e); }
      return e;
    }

    pushResult(r) {
      if (this.closed || r.seq === null || r.seq === undefined) return;
      const e = this._entry(r.seq);
      e.result = r;
      e.t = r.t;
      e.arrival = e.arrival || now();
      if (!e.blob && this.getFrame) e.blob = this.getFrame(r.seq);
      if (!e.blob && this.getFrame) { this.entries.delete(r.seq); return; }  // quadro local já descartado
      this._decode(e);
    }

    pushFrame(seq, t, blob) {
      if (this.closed) return;
      const e = this._entry(seq);
      e.blob = blob;
      e.t = t;
      e.arrival = e.arrival || now();
      this._decode(e);
    }

    /* Reserva sem WebSocket: imagem já anotada pelo servidor, mostrada na hora. */
    pushAnnotated(blob) {
      createImageBitmap(blob).then((b) => {
        if (this.closed) { b.close(); return; }
        if (this.annotated) this.annotated.close();
        this.annotated = b;
        this.lastArrival = now();
        this.shown++;
      }).catch(() => {});
    }

    _decode(e) {
      if (!e.blob || e.bitmap || e.decoding) return;
      e.decoding = true;
      createImageBitmap(e.blob).then((b) => {
        e.decoding = false;
        if (this.closed || !this.entries.has(e.seq)) { b.close(); return; }
        e.bitmap = b;
        e.blob = null;
        this._ready(e);
      }).catch(() => { e.decoding = false; this.entries.delete(e.seq); });
    }

    _ready(e) {
      if (!e.result) return;
      const t = now();
      this.lastArrival = t;
      if (this.annotated) { this.annotated.close(); this.annotated = null; }
      this.offsets.push([t, t - e.t * 1000]);
      while (this.offsets.length && t - this.offsets[0][0] > 4000) this.offsets.shift();
      const offs = this.offsets.map((o) => o[1]);
      const target = Math.min(...offs);
      const sorted = offs.map((o) => o - target).sort((a, b) => a - b);
      const p90 = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.9))] || 0;
      this.jbTarget = Math.max(60, Math.min(600, p90 + 40));
      if (this.base === null || Math.abs(target - this.base) > 800) this.base = target;
    }

    _render() {
      if (this.closed) return;
      this._raf = requestAnimationFrame(() => this._render());
      const t = now();
      if (this.base !== null && this.offsets.length) {
        const target = Math.min(...this.offsets.map((o) => o[1]));
        this.base += Math.max(-0.5, Math.min(0.5, target - this.base));      // ajuste lento do relógio
        this.jb += Math.max(-0.3, Math.min(1.5, (this.jbTarget || this.jb) - this.jb));
      }
      let pick = null;
      const old = [];
      for (const e of this.entries.values()) {
        if (!e.bitmap || !e.result) {
          if (e.arrival && t - e.arrival > 8000) old.push(e);
          continue;
        }
        if (this.base + e.t * 1000 + this.jb <= t) {
          if (!pick || e.t > pick.t) { if (pick) old.push(pick); pick = e; } else old.push(e);
        }
      }
      for (const e of old) {
        if (e.bitmap) e.bitmap.close();
        this.entries.delete(e.seq);
      }
      if (pick) {
        this.entries.delete(pick.seq);
        if (this.current && this.current.bitmap) this.current.bitmap.close();
        this.current = pick;
        this.shown++;
        this._paint();
      } else if (this.annotated || (this.current && t - this.lastArrival > 1500) || !this.current) {
        this._paint();
      }
      if (t - this._statsAt >= 1000) {
        const dt = (t - this._statsAt) / 1000;
        this.onStats && this.onStats({ fps: this.shown / dt, buffer: Math.round(this.jb), pending: this.entries.size,
          stalled: this.lastArrival > 0 && t - this.lastArrival > 1500 });
        this.shown = 0;
        this._statsAt = t;
      }
    }

    _paint() {
      const canvas = this.canvas;
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      const cw = canvas.clientWidth;
      const ch = canvas.clientHeight;
      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== Math.round(cw * dpr) || canvas.height !== Math.round(ch * dpr)) {
        canvas.width = Math.round(cw * dpr);
        canvas.height = Math.round(ch * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cw, ch);
      const img = this.annotated || (this.current && this.current.bitmap);
      if (img) {
        const s = Math.min(cw / img.width, ch / img.height);
        ctx.drawImage(img, (cw - img.width * s) / 2, (ch - img.height * s) / 2, img.width * s, img.height * s);
        if (!this.annotated && this.current) drawOverlay(canvas, this.current.result, { showBoxes: this.showBoxes, clear: false });
      }
      const stalled = this.lastArrival > 0 && now() - this.lastArrival > 1500;
      const text = this.message || (stalled ? 'Sinal interrompido · reconectando...' : '');
      if (text) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        if (img) { ctx.fillStyle = 'rgba(0,1,42,.45)'; ctx.fillRect(0, 0, cw, ch); }
        ctx.font = '600 14px Outfit, system-ui, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const w = ctx.measureText(text).width + 24;
        ctx.fillStyle = 'rgba(0,0,0,.72)';
        ctx.fillRect((cw - w) / 2, ch / 2 - 16, w, 32);
        ctx.fillStyle = '#fff';
        ctx.fillText(text, cw / 2, ch / 2);
        ctx.textAlign = 'start';
      }
    }
  }

  /* ── Painel assistindo um stream do servidor ───────────────────── */
  class LiveViewer {
    constructor(opts) {
      Object.assign(this, { backendUrl: '' }, opts);
      this.active = false;
      this.ws = null;
      this.attempt = 0;
      this.failures = 0;
      this.lastMsgAt = 0;
      this.pending = new Map();  // seq -> resultado esperando o quadro
      this.polling = false;
    }

    start() {
      this.active = true;
      this._connect();
      this._timer = setInterval(() => this._heartbeat(), 2000);
    }

    stop() {
      this.active = false;
      clearInterval(this._timer);
      clearTimeout(this._retry);
      this.polling = false;
      const ws = this.ws;
      this.ws = null;
      if (ws) try { ws.close(); } catch (e) { /* */ }
    }

    _status(st, extra) { this.onStatus && this.onStatus(st, extra); }

    _connect() {
      if (!this.active) return;
      if (!('WebSocket' in window)) { this._startPolling(); return; }
      let ready = false;
      const ws = new WebSocket(wsUrl(this.backendUrl, '/ws/view', { token: this.token, stream_id: this.streamId, video: '1' }));
      ws.binaryType = 'arraybuffer';
      const giveUp = setTimeout(() => { if (!ready) try { ws.close(); } catch (e) { /* */ } }, 6000);
      ws.onmessage = (ev) => {
        this.lastMsgAt = now();
        if (typeof ev.data !== 'string') {
          const f = unpackFrame(ev.data);
          if (!f) return;
          this.player.pushFrame(f.seq, f.t, f.blob);
          const r = this.pending.get(f.seq);
          if (r) { this.pending.delete(f.seq); this.player.pushResult(r); }
          return;
        }
        let d;
        try { d = JSON.parse(ev.data); } catch (e) { return; }
        if (d.type === 'quadro') {
          this.pending.set(d.seq, d);
          if (this.pending.size > 240) this.pending.delete(this.pending.keys().next().value);
          this.onResult && this.onResult(d);
        } else if (d.type === 'pronto' || d.type === 'estado') {
          if (!ready) { ready = true; clearTimeout(giveUp); this.ws = ws; this.attempt = 0; this.failures = 0; this.polling = false; this.player.setMessage(''); this._status('ws'); }
          this.onInfo && this.onInfo(d);
        } else if (d.type === 'aguardando') {
          ready = true; clearTimeout(giveUp); this.ws = ws; this.attempt = 0;
          this.player.setMessage('Aguardando a câmera...');
          this._status('aguardando');
        } else if (d.type === 'removido') {
          this._status('removido');
          this.stop();
        } else if (d.type === 'erro') {
          this._status('erro', d.error);
        }
      };
      ws.onclose = () => {
        clearTimeout(giveUp);
        if (this.ws === ws) this.ws = null;
        if (!this.active) return;
        if (!ready) this.failures++;
        this._status('reconectando');
        if (this.failures >= 2) this._startPolling();
        this._retry = setTimeout(() => this._connect(), this.polling ? 15000 : backoff(this.attempt++));
      };
    }

    _heartbeat() {
      const ws = this.ws;
      if (!ws || ws.readyState !== 1) return;
      if (now() - this.lastMsgAt > 8000) { try { ws.close(); } catch (e) { /* */ } return; }  // servidor manda 'estado' a cada 1 s
      try { ws.send(JSON.stringify({ type: 'ping' })); } catch (e) { /* */ }
    }

    /* Reserva: imagem anotada pelo servidor, ~8 q/s, até o WebSocket voltar. */
    async _startPolling() {
      if (this.polling || !this.active) return;
      this.polling = true;
      this._status('http');
      while (this.active && this.polling && !this.ws) {
        try {
          const r = await fetch(joinUrl(this.backendUrl, '/frame?stream=' + encodeURIComponent(this.streamId)), {
            headers: { Authorization: 'Bearer ' + this.token, 'ngrok-skip-browser-warning': 'true' }, cache: 'no-store',
          });
          if (r.ok && r.status !== 204) {
            const d = await r.json();
            if (d.frame) {
              const bin = atob(d.frame);
              const bytes = new Uint8Array(bin.length);
              for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
              this.player.pushAnnotated(new Blob([bytes], { type: 'image/jpeg' }));
            }
            if (d.status) {
              this.onResult && this.onResult(d.status);
              this.onInfo && this.onInfo({ config: d.status.config, pipeline: d.status.pipeline, ...d.status });
            }
          }
        } catch (e) { /* tenta de novo */ }
        await new Promise((res) => setTimeout(res, 120));
      }
      this.polling = false;
    }
  }

  /* ── Upload de vídeo gravado (aba Análises) ────────────────────── */
  function uploadAnalysisFile({ backendUrl, token, file, mode, name, model, onProgress }) {
    return new Promise((resolve, reject) => {
      const fd = new FormData();
      fd.append('video', file);
      fd.append('modo', mode);
      if (name) fd.append('nome', name);
      if (model) fd.append('modelo', model);
      const xhr = new XMLHttpRequest();
      xhr.open('POST', joinUrl(backendUrl, '/analises/upload'));
      xhr.setRequestHeader('Authorization', 'Bearer ' + token);
      xhr.setRequestHeader('ngrok-skip-browser-warning', 'true');
      xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
      xhr.onload = () => {
        let d = {};
        try { d = JSON.parse(xhr.responseText || '{}'); } catch (e) { /* */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(d.analise);
        else reject(new Error(d.error || 'Falha no envio do vídeo'));
      };
      xhr.onerror = () => reject(new Error('Falha de rede no envio do vídeo'));
      xhr.send(fd);
    });
  }

  /* Desenha pessoas (caixa, esqueleto colorido por região do corpo, etiquetas) e EPIs sobre o canvas,
     com a imagem ocupando a área em modo "contain". */
  function drawOverlay(canvas, result, { showBoxes = true, clear = true } = {}) {
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const cw = canvas.clientWidth;
    const ch = canvas.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    if (clear) {
      if (canvas.width !== Math.round(cw * dpr) || canvas.height !== Math.round(ch * dpr)) {
        canvas.width = Math.round(cw * dpr);
        canvas.height = Math.round(ch * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cw, ch);
    }
    if (!result || !result.frame_w || !cw) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const s = Math.min(cw / result.frame_w, ch / result.frame_h);
    const ox = (cw - result.frame_w * s) / 2;
    const oy = (ch - result.frame_h * s) / 2;
    const X = (x) => ox + x * s;
    const Y = (y) => oy + y * s;
    const font = Math.max(11, Math.min(15, cw / 60));
    ctx.font = `600 ${font}px Outfit, system-ui, sans-serif`;
    ctx.textBaseline = 'bottom';

    const tag = (text, x, yBottom, color) => {
      const w = ctx.measureText(text).width + 10;
      const tx = Math.max(0, Math.min(x, cw - w));
      const ty = Math.max(font + 6, yBottom);
      ctx.fillStyle = color;
      ctx.fillRect(tx, ty - font - 5, w, font + 5);
      ctx.fillStyle = '#fff';
      ctx.fillText(text, tx + 5, ty - 2);
      return ty - font - 7;
    };

    if (showBoxes) {
      ctx.lineWidth = 1.5;
      for (const d of result.detections || []) {
        if (d.item === 'person') continue;
        const [x1, y1, x2, y2] = d.bbox;
        ctx.strokeStyle = d.present ? COLORS.ok : COLORS.faltando;
        ctx.strokeRect(X(x1), Y(y1), (x2 - x1) * s, (y2 - y1) * s);
      }
    }

    for (const p of result.persons || []) {
      const regions = {};
      for (const [item, st] of Object.entries(p.epis || {})) {
        const r = REGION[item] || 'body';
        if ((RANK[st.estado] ?? -1) >= (RANK[regions[r]] ?? -1)) regions[r] = st.estado;
      }
      const danger = (p.faltando || []).length || (p.alertas || []).length;
      const color = danger ? COLORS.faltando : Object.values(regions).includes('verificando') ? COLORS.verificando : COLORS.ok;
      const [x1, y1, x2, y2] = p.bbox;
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.5;
      ctx.strokeRect(X(x1), Y(y1), (x2 - x1) * s, (y2 - y1) * s);
      const k = p.keypoints || [];
      ctx.lineWidth = 3;
      ctx.lineCap = 'round';
      for (const [a, b, region] of SKELETON) {
        if (k[a] && k[b] && k[a][2] >= 0.35 && k[b][2] >= 0.35) {
          ctx.strokeStyle = COLORS[regions[region]] || 'rgba(255,255,255,.85)';
          ctx.beginPath();
          ctx.moveTo(X(k[a][0]), Y(k[a][1]));
          ctx.lineTo(X(k[b][0]), Y(k[b][1]));
          ctx.stroke();
        }
      }
      ctx.fillStyle = '#fff';
      for (const q of k) {
        if (q[2] >= 0.35) {
          ctx.beginPath();
          ctx.arc(X(q[0]), Y(q[1]), 2.5, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      // EPIs viram ícones: aceso e verde quando está usando, apagado e
      // avermelhado quando falta. Sem nomes escritos na caixa.
      let y = drawEpiRow(ctx, p, X(x1), Y(y1) - 2, (x2 - x1) * s);

      const lines = [];
      const who = (p.track_id !== null && p.track_id !== undefined ? 'ID ' + p.track_id : 'Pessoa') + (p.nome ? ' · ' + p.nome : '');
      // encoberto: um objeto ou outra pessoa esconde onde o EPI fica; o sistema segura o alarme
      lines.push([[who, POSTURE[p.postura], p.movimento, (p.encoberto || []).length ? 'encoberto' : '']
        .filter(Boolean).join(' · '), color]);
      if ((p.alertas || []).includes('Possível queda')) lines.push(['⚠ POSSÍVEL QUEDA', '#a00000']);
      for (const [text, c] of lines.reverse()) y = tag(text, X(x1), y, c);
    }
  }

  function formatDuration(sec) {
    const s = Math.max(0, Math.round(Number(sec) || 0));
    const m = Math.floor(s / 60);
    return m ? `${m}min ${String(s % 60).padStart(2, '0')}s` : `${s}s`;
  }

  window.ArgosStream = {
    openCamera, trackInfo, LiveSender, LivePlayer, LiveViewer, uploadAnalysisFile, drawOverlay,
    drawEpiIcon, drawEpiRow, EPI_ICONS,
    packFrame, unpackFrame, itemLabel, formatDuration, LABELS, COLORS, POSTURE, HEAD, MODES, FPS_OPTIONS, RES_OPTIONS,
  };
})();
