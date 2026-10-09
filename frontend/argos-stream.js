/* argos-stream.js — câmeras ao vivo do Argos EPI. Usado por cam.html (celular) e index.html (painel).
   Sem dependências.

   Tempo real é imediato: nada é guardado. A câmera manda vídeo de verdade (H.264/VP8) por WebRTC,
   direto para o servidor (UDP; o aperto de mãos passa pelo endereço de sempre). A tela mostra o
   vídeo e, por cima, as caixas do MESMO quadro: cada resultado traz a hora do quadro analisado e
   a tela segura a imagem alguns centésimos de segundo até a caixa dela chegar (Alinhador). Se a
   conexão direta não fecha, o envio volta sozinho para quadros JPEG pelo WebSocket.

   Detalhado e super continuam com atraso fixo: a câmera manda quadros com o horário de captura,
   o servidor analisa e devolve o resultado de cada quadro no ritmo original.
   O modo, o fps e a resolução são escolhidos no painel; a câmera só obedece.

   LiveSender  captura e envia. Tempo real: WebRTC (DirectLink), com JPEG por WebSocket de reserva.
               Nos outros modos guarda os quadros até o servidor confirmar e reenvia se a conexão cai.
   LivePlayer  mostra quadro + caixas. Imediato: o mais novo, na hora (ou só as caixas sobre o vídeo
               ao vivo). Com atraso: sincronizados pelo número do quadro, com buffer contra oscilação.
   LiveViewer  painel assistindo um stream do servidor: resultado pelo WebSocket /ws/view e vídeo
               direto (WebRTC) quando a câmera está mandando assim; senão, os quadros pelo WebSocket.
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
    tempo_real: { nome: 'Tempo real', icone: '⚡', descricao: 'Imediato: nada é guardado. O atraso é só o da rede e o da análise.' },
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
      const s = await navigator.mediaDevices.getUserMedia({ video: { ...video, facingMode }, audio: false });
      // Muita câmera de celular só dá 60 q/s em resolução menor, e o navegador, entre os dois pedidos,
      // pode ficar com o ritmo e largar a resolução. A resolução vale mais (é ela que mostra quem está
      // longe): se veio menor que a pedida, tenta de novo a 30 q/s e fica com o que for maior.
      const track = s.getVideoTracks()[0];
      const lado = () => { const c = track.getSettings ? track.getSettings() : {}; return Math.min(c.width || 0, c.height || 0); };
      const antes = lado();
      if (fps > 30 && antes && antes < height * 0.9 && track.applyConstraints) {
        try {
          await track.applyConstraints({ ...video, frameRate: { ideal: 30 } });
          if (lado() <= antes) await track.applyConstraints(video);
        } catch (e) { /* fica como abriu */ }
      }
      return s;
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

  /* ── Conexão direta (WebRTC) ───────────────────────────────────── */
  const esperar = (ms) => new Promise((ok) => setTimeout(ok, ms));
  const temRtc = () => typeof RTCPeerConnection !== 'undefined';

  async function pedirJson(backendUrl, path, token, opts = {}) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), opts.timeout || 12000);
    try {
      const r = await fetch(joinUrl(backendUrl, path), {
        method: opts.method || 'GET', signal: ctl.signal, cache: 'no-store',
        headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': 'true' },
        body: opts.body ? JSON.stringify(opts.body) : undefined,
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { const e = new Error(d.error || ('HTTP ' + r.status)); e.status = r.status; e.codigo = d.codigo; e.espera_s = d.espera_s; throw e; }
      return d;
    } finally { clearTimeout(t); }
  }

  /* Uma conexão WebRTC com o servidor: 'enviar' (câmera) ou 'assistir' (painel).
     O aperto de mãos vai pelas rotas /rtc/... do servidor; o vídeo vai por UDP, direto. */
  class DirectLink {
    constructor({ backendUrl, token, streamId, modo, track = null, fps = 30, altura = 720, onState = null, onTrack = null }) {
      Object.assign(this, { backendUrl, token, streamId, modo, track, fps, altura, onState, onTrack });
      this.pc = null;
      this.sessao = '';
      this.estado = 'novo';     // novo | conectando | ligado | caiu | fechado
      this.rota = '';           // direta | retransmitida
      this._stats = { t: 0, bytes: 0, quadros: 0 };
    }

    _set(estado, motivo) {
      if (this.estado === estado || this.estado === 'fechado') return;
      this.estado = estado;
      this.onState && this.onState(estado, motivo);
    }

    async abrir() {
      if (!temRtc()) throw new Error('Este navegador não tem WebRTC.');
      this._set('conectando');
      const ice = await pedirJson(this.backendUrl, '/rtc/ice', this.token, { timeout: 8000 });
      if (ice.ativo === false) { const e = new Error('Vídeo direto desligado neste servidor.'); e.codigo = 'desligado'; throw e; }
      const pc = this.pc = new RTCPeerConnection({ iceServers: ice.iceServers || [], bundlePolicy: 'max-bundle' });
      pc.onconnectionstatechange = () => {
        const st = pc.connectionState;
        if (st === 'connected') { this._set('ligado'); this._lerRota(); }
        else if (st === 'failed' || st === 'closed') this._set('caiu', st);
        else if (st === 'disconnected') {
          // oscilação curta volta sozinha; se passar de 4 s, considera caída
          clearTimeout(this._queda);
          this._queda = setTimeout(() => { if (pc.connectionState !== 'connected') this._set('caiu', 'disconnected'); }, 4000);
        }
      };
      if (this.modo === 'enviar') {
        const tr = pc.addTransceiver(this.track, { direction: 'sendonly' });
        this.sender = tr.sender;
        try { this.track.contentHint = 'motion'; } catch (e) { /* */ }
        // H.264 primeiro: o celular codifica por hardware e o servidor decodifica mais barato
        try {
          const caps = RTCRtpSender.getCapabilities('video').codecs;
          const h264 = caps.filter((c) => /h264/i.test(c.mimeType));
          tr.setCodecPreferences([...h264, ...caps.filter((c) => !/h264/i.test(c.mimeType))]);
        } catch (e) { /* navegador sem setCodecPreferences: usa o padrão dele */ }
      } else {
        pc.addTransceiver('video', { direction: 'recvonly' });
        pc.ontrack = (ev) => {
          // sem fila no navegador: mostra o quadro assim que chega
          this.receiver = ev.receiver;
          this.atrasar(0);
          this.onTrack && this.onTrack(ev.streams[0] || new MediaStream([ev.track]));
        };
      }
      await pc.setLocalDescription(await pc.createOffer());
      // espera juntar os endereços (até 1,5 s) e manda a oferta completa: sem troca aos poucos
      await new Promise((ok) => {
        if (pc.iceGatheringState === 'complete') return ok();
        const fim = setTimeout(ok, 1500);
        pc.addEventListener('icegatheringstatechange', () => { if (pc.iceGatheringState === 'complete') { clearTimeout(fim); ok(); } });
      });
      const rota = this.modo === 'enviar' ? '/rtc/publicar/' : '/rtc/assistir/';
      const d = await pedirJson(this.backendUrl, rota + encodeURIComponent(this.streamId), this.token,
        { method: 'POST', body: { sdp: pc.localDescription.sdp }, timeout: 20000 });
      if (this.estado === 'fechado') return;
      this.sessao = d.sessao || '';
      if (this.modo === 'enviar') {
        // começa já perto da taxa certa: sem isto o navegador parte de 0,3 Mb/s e os primeiros
        // segundos saem borrados
        try { await pc.setRemoteDescription({ type: 'answer', sdp: this._comTaxaInicial(d.sdp) }); }
        catch (e) { await pc.setRemoteDescription({ type: 'answer', sdp: d.sdp }); }
        this._limites();
      } else {
        await pc.setRemoteDescription({ type: 'answer', sdp: d.sdp });
      }
      // não fechou em 10 s: a rede não deixou (sem caminho direto e sem TURN)
      this._prazo = setTimeout(() => { if (this.estado === 'conectando') this._set('caiu', 'prazo'); }, 10000);
    }

    /* Teto da taxa do vídeo (bits por segundo) para a resolução e o ritmo pedidos no painel.
       Full HD a 60 q/s precisa de uns 12 Mb/s para sair nítido; com o teto baixo (2,8 Mb/s na
       20.4.1) o navegador encolhia a imagem para caber e quem estava longe virava um borrão. */
    _taxaMaxima() {
      const base = this.altura <= 480 ? 1500000 : this.altura <= 720 ? 4000000 : 8000000;   // a 30 q/s
      return Math.round(base * (this.fps > 40 ? 1.5 : this.fps < 20 ? 0.7 : 1));
    }

    _comTaxaInicial(sdp) {
      const kbps = Math.round(Math.min(this._taxaMaxima() / 2, 4000000) / 1000);
      const extra = 'x-google-start-bitrate=' + kbps;
      const linhas = sdp.split('\r\n');
      const video = linhas.findIndex((l) => l.startsWith('m=video'));
      if (video < 0) return sdp;
      let fim = linhas.findIndex((l, i) => i > video && l.startsWith('m='));
      if (fim < 0) fim = linhas.length;
      for (let i = video; i < fim; i++) {
        if (linhas[i].startsWith('a=fmtp:') && !linhas[i].includes('x-google-start-bitrate')) linhas[i] += ';' + extra;
      }
      return linhas.join('\r\n');
    }

    async _limites() {
      try {
        const p = this.sender.getParameters();
        if (!p.encodings || !p.encodings.length) p.encodings = [{}];
        p.encodings[0].maxBitrate = this._taxaMaxima();
        p.encodings[0].maxFramerate = this.fps;
        p.encodings[0].scaleResolutionDownBy = 1;
        // rede apertada: cai o ritmo, não o tamanho da imagem (é o tamanho que deixa ver quem está longe)
        p.degradationPreference = 'maintain-resolution';
        await this.sender.setParameters(p);
      } catch (e) { /* o navegador escolhe */ }
    }

    /* Quem assiste: segura o vídeo por ms milissegundos (fila do próprio navegador), para o quadro
       só aparecer quando a caixa dele já chegou. false = este navegador não sabe fazer isso. */
    atrasar(ms) {
      const r = this.receiver;
      if (!r) return false;
      let ok = false;
      try { if ('jitterBufferTarget' in r) { r.jitterBufferTarget = ms; ok = true; } } catch (e) { /* */ }
      try { if ('playoutDelayHint' in r) { r.playoutDelayHint = ms / 1000; ok = true; } } catch (e) { /* */ }
      return ok;
    }

    async trocarTrack(track, fps, altura) {
      this.track = track;
      if (fps) this.fps = fps;
      if (altura) this.altura = altura;
      if (this.sender && track) { await this.sender.replaceTrack(track); try { track.contentHint = 'motion'; } catch (e) { /* */ } this._limites(); }
    }

    async _lerRota() {
      try {
        const st = await this.pc.getStats();
        let par = null;
        st.forEach((v) => { if (v.type === 'transport' && v.selectedCandidatePairId) par = st.get(v.selectedCandidatePairId); });
        if (!par) st.forEach((v) => { if (v.type === 'candidate-pair' && v.nominated && v.state === 'succeeded') par = v; });
        const local = par && st.get(par.localCandidateId);
        const remoto = par && st.get(par.remoteCandidateId);
        this.rota = ((local && local.candidateType === 'relay') || (remoto && remoto.candidateType === 'relay')) ? 'retransmitida' : 'direta';
      } catch (e) { /* */ }
    }

    /* {fps, kbps, largura, altura} do vídeo que está passando. */
    async medir() {
      if (!this.pc || this.estado !== 'ligado') return null;
      try {
        const st = await this.pc.getStats();
        let r = null, par = null, volta = null;
        const tipo = this.modo === 'enviar' ? 'outbound-rtp' : 'inbound-rtp';
        st.forEach((v) => {
          if (v.type === tipo && (v.kind === 'video' || v.mediaType === 'video')) r = v;
          else if (v.type === 'candidate-pair' && v.nominated && v.state === 'succeeded') par = v;
          else if (v.type === 'remote-inbound-rtp' && v.kind === 'video') volta = v;
        });
        if (!r) return null;
        const bytes = this.modo === 'enviar' ? r.bytesSent : r.bytesReceived;
        const quadros = this.modo === 'enviar' ? r.framesEncoded : r.framesDecoded;
        const t = now();
        const ant = this._stats;
        const codif = r.totalEncodeTime || 0, espera = r.totalPacketSendDelay || 0, pacotes = r.packetsSent || 0;
        this._stats = { t, bytes, quadros, codif, espera, pacotes };
        const dt = ant.t ? (t - ant.t) / 1000 : 0;
        const nq = quadros - (ant.quadros || 0), np = pacotes - (ant.pacotes || 0);
        // ida e volta na rede (ms): a do par de endereços escolhido; a do relatório do outro lado serve de reserva
        const rtt = ((par && par.currentRoundTripTime) || (volta && volta.roundTripTime) || 0) * 1000;
        return {
          fps: dt ? Math.max(0, nq / dt) : (r.framesPerSecond || 0),
          kbps: dt ? Math.max(0, (bytes - ant.bytes) * 8 / 1000 / dt) : 0,
          largura: r.frameWidth || 0, altura: r.frameHeight || 0, rota: this.rota, rtt,
          limite: r.qualityLimitationReason || '',
          // envio: tempo médio para codificar um quadro e para o pacote sair (ms)
          codificar: ant.t && nq > 0 ? Math.max(0, (codif - ant.codif) * 1000 / nq) : 0,
          sair: ant.t && np > 0 ? Math.max(0, (espera - ant.espera) * 1000 / np) : 0,
        };
      } catch (e) { return null; }
    }

    fechar() {
      if (this.estado === 'fechado') return;
      clearTimeout(this._prazo);
      clearTimeout(this._queda);
      this.estado = 'fechado';
      const pc = this.pc;
      this.pc = null;
      if (pc) { try { pc.ontrack = null; pc.onconnectionstatechange = null; pc.close(); } catch (e) { /* */ } }
      if (this.sessao) {
        const tipo = this.modo === 'enviar' ? 'whip' : 'whep';
        pedirJson(this.backendUrl, `/rtc/sessao/${encodeURIComponent(this.streamId)}/${tipo}/${encodeURIComponent(this.sessao)}`,
          this.token, { method: 'DELETE', timeout: 4000 }).catch(() => {});
      }
    }
  }

  /* ── Caixas no quadro certo (vídeo direto) ─────────────────────────
     No vídeo direto a imagem e o resultado andam por caminhos diferentes: a imagem aparece na hora
     e a caixa só depois da análise e da volta pela rede. Desenhar "o resultado mais novo" deixava a
     caixa para trás de quem anda ou corre. Agora cada resultado diz de que quadro ele é, e a tela
     desenha, em cada quadro mostrado, a caixa daquele quadro (entre os dois resultados vizinhos).

     rtp = tempo RTP do quadro analisado: o número que a câmera carimbou ao filmar.
       Quem assiste lê o mesmo número em cada quadro que o navegador mostra: o acerto é exato.
       Na câmera, o tempo de cada quadro (mediaTime) é esse mesmo relógio a menos de uma constante;
       ela é estimada pelos relógios (abaixo) e depois encaixada nos quadros de verdade, que nunca
       saem em intervalos perfeitamente iguais: só um encaixe faz os resultados caírem em cima deles.
     tv, ts, ep = reserva enquanto o servidor não tem o rtp (antes do primeiro quadro-chave): tv é
       o tempo do vídeo contado pelo leitor do servidor, ts o relógio do servidor quando o quadro
       chegou lá, ep muda quando a leitura reabre. A constante sai da diferença entre os relógios
       (pelos pings, como o NTP) e do menor atraso visto de cada lado. Erra por um ou dois quadros. */
  const entre = (a, b, f) => a + (b - a) * f;

  function iouCaixa(a, b) {
    const ix = Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0]));
    const iy = Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
    const inter = ix * iy;
    const uniao = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter;
    return uniao > 0 ? inter / uniao : 0;
  }

  /* Resultado no ponto f (0..1) entre dois resultados seguidos: pessoas pelo ID, EPIs pela sobreposição. */
  function entreResultados(a, b, f) {
    if (!a || f >= 1) return b;
    if (!b || f <= 0) return a;
    const perto = f < 0.5 ? a : b;
    const porId = new Map();
    for (const p of b.persons || []) if (p.track_id !== null && p.track_id !== undefined) porId.set(p.track_id, p);
    const persons = [];
    const usados = new Set();
    for (const pa of a.persons || []) {
      const pb = pa.track_id !== null && pa.track_id !== undefined ? porId.get(pa.track_id) : null;
      if (!pb) { if (f < 0.5) persons.push(pa); continue; }
      usados.add(pa.track_id);
      const q = { ...(f < 0.5 ? pa : pb), bbox: pa.bbox.map((v, i) => entre(v, pb.bbox[i], f)) };
      const ka = pa.keypoints || [], kb = pb.keypoints || [];
      if (ka.length && ka.length === kb.length) {
        q.keypoints = ka.map((k, i) => [entre(k[0], kb[i][0], f), entre(k[1], kb[i][1], f), Math.min(k[2], kb[i][2])]);
      }
      persons.push(q);
    }
    if (f >= 0.5) for (const pb of b.persons || []) if (pb.track_id === null || pb.track_id === undefined || !usados.has(pb.track_id)) persons.push(pb);
    const outros = (f < 0.5 ? b : a).detections || [];
    const detections = (perto.detections || []).map((d) => {
      let par = null, melhor = 0.15;
      for (const o of outros) {
        if (o.item !== d.item || o.present !== d.present) continue;
        const v = iouCaixa(o.bbox, d.bbox);
        if (v > melhor) { melhor = v; par = o; }
      }
      if (!par) return d;
      const [x, y] = f < 0.5 ? [d, par] : [par, d];
      return { ...d, bbox: x.bbox.map((v, i) => entre(v, y.bbox[i], f)) };
    });
    return { ...perto, persons, detections };
  }

  const percentil = (lista, p) => {
    if (!lista.length) return 0;
    const o = lista.slice().sort((a, b) => a - b);
    return o[Math.min(o.length - 1, Math.floor(o.length * p))];
  };

  class Alinhador {
    /* local: true na câmera (o vídeo na tela é o da própria câmera); false em quem assiste. */
    constructor({ local = false } = {}) {
      this.local = local;
      this.dif = null;            // relógio do servidor − relógio daqui (ms)
      this.rttMelhor = Infinity;
      this.subida = Alinhador.LEITURA_MS + 10;   // câmera: menor tempo da captura até o servidor ler o quadro (ms)
      this.dv = 0;                // quem assiste: tempo do vídeo na rede (ms)
      this.zerar();
    }

    zerar() {
      this.ep = null;
      this.res = [];              // [{tv, r}] em ordem; tv na linha do tempo RTP (ms) quando há rtp
      this.comRtp = false;
      this.dRtp = 0;              // tv do servidor − tempo RTP (ms): constante
      this.mins = [];             // [quando, ts − tv]
      this.ys = [];               // quem assiste: [quando, chegada − tempo do vídeo]
      this.k = null;              // tempo do vídeo daqui − tempo do resultado
      this.kEst = null;           // o mesmo, estimado pelos relógios
      this.kExato = null;         // câmera: o mesmo, encaixado nos quadros
      this.exato = false;
      this.atrasos = [];          // [quando, quanto o resultado chegou depois do quadro dele]
      this.intervalos = [];
      this.cRec = null;           // quem assiste: hora de chegada do quadro − tempo do vídeo (a menor)
      this.off = null;            // câmera: hora da captura − tempo do vídeo
      this.taus = [];             // câmera: tempo dos últimos quadros
      this._ref = null;
      this._encaixeEm = 0;
    }

    /* Contador RTP (32 bits, 90 kHz, dá a volta a cada 13 h) → ms numa linha contínua. */
    _rtpMs(v) {
      if (this._ref === null) this._ref = v;
      let d = (v - this._ref) % 4294967296;
      if (d >= 2147483648) d -= 4294967296; else if (d < -2147483648) d += 4294967296;
      const u = this._ref + d;
      if (u > this._ref) this._ref = u;
      return u / 90;
    }

    /* Resposta de um ping: t0 = quando saiu daqui, s = relógio do servidor ao responder. */
    pong(t0, s) {
      if (typeof t0 !== 'number' || typeof s !== 'number') return;
      const t1 = now();
      const rtt = t1 - t0;
      if (!(rtt >= 0) || rtt > 3000) return;
      // vale a ida e volta mais curta (a menos torta); ela envelhece para o relógio não ficar parado numa medida antiga
      if (rtt <= this.rttMelhor) { this.rttMelhor = rtt; this.dif = s - (t0 + t1) / 2; this._calcular(); }
      else this.rttMelhor += Math.max(0.5, this.rttMelhor * 0.03);
    }

    pronto() { return this.k !== null; }

    _calcular() {
      if (!this.local && this.comRtp) { this.k = 0; this.exato = true; return; }      // mesmo relógio: nada a estimar
      let est = null;
      if (this.dif !== null && this.mins.length) {
        let m = Infinity;
        for (const x of this.mins) if (x[1] < m) m = x[1];
        if (this.local) {
          if (this.off !== null) est = m - this.dif - this.subida - this.off + (this.comRtp ? this.dRtp : 0);
        } else if (this.cRec !== null) {
          est = (m - this.dif) - this.cRec - Alinhador.LEITURA_MS + this.dv;
        }
      }
      this.kEst = est;
      this.exato = this.kExato !== null;
      this.k = this.exato ? this.kExato : est;
    }

    /* Um resultado chegou. true = ele tem a hora do quadro (dá para alinhar). */
    resultado(r) {
      if (typeof r.tv !== 'number' || typeof r.ts !== 'number') return false;
      const t = now();
      if (r.ep !== this.ep) { this.zerar(); this.ep = r.ep; }
      const comRtp = typeof r.rtp === 'number';
      if (comRtp !== this.comRtp) {         // o servidor passou a mandar o rtp: a linha do tempo dos resultados muda
        this.comRtp = comRtp;
        this.res = []; this.atrasos = []; this.intervalos = []; this.kExato = null;
      }
      const tr = comRtp ? this._rtpMs(r.rtp) : r.tv;
      if (comRtp) this.dRtp = r.tv - tr;
      const ult = this.res[this.res.length - 1];
      if (ult && tr <= ult.tv) return true;
      if (ult) { this.intervalos.push(tr - ult.tv); if (this.intervalos.length > 40) this.intervalos.shift(); }
      this.res.push({ tv: tr, r });
      if (this.res.length > 120) this.res.shift();
      this.mins.push([t, r.ts - r.tv]);
      while (t - this.mins[0][0] > 15000) this.mins.shift();
      this._calcular();
      if (this.local && comRtp && t - this._encaixeEm > 400) { this._encaixeEm = t; this._encaixar(); }
      // quanto este resultado chegou depois do quadro dele (da captura, na câmera; da chegada, em quem assiste)
      const base = this.local ? this.off : this.cRec;
      if (this.k !== null && base !== null) {
        this.atrasos.push([t, t - (tr + this.k + base)]);
        while (t - this.atrasos[0][0] > 4000) this.atrasos.shift();
      }
      return true;
    }

    /* Um quadro do vídeo foi apresentado (dados do requestVideoFrameCallback). Devolve o tempo dele (ms). */
    quadro(meta) {
      const t = now();
      if (this.local) {
        const tau = typeof meta.mediaTime === 'number' ? meta.mediaTime * 1000 : (meta.presentationTime || t);
        const captura = typeof meta.captureTime === 'number' ? meta.captureTime : (meta.presentationTime || t) - 12;
        const o = captura - tau;
        this.off = this.off === null || Math.abs(o - this.off) > 60 ? o : this.off * 0.9 + o * 0.1;
        const n = this.taus.length;
        if (!n || tau > this.taus[n - 1]) { this.taus.push(tau); if (n > 300) this.taus.shift(); }
        return tau;
      }
      if (typeof meta.rtpTimestamp !== 'number') return null;
      const tau = this._rtpMs(meta.rtpTimestamp);
      const chegada = typeof meta.receiveTime === 'number' ? meta.receiveTime : (meta.presentationTime || t);
      this.ys.push([t, chegada - tau]);
      while (t - this.ys[0][0] > 15000) this.ys.shift();
      let y = Infinity;
      for (const x of this.ys) if (x[1] < y) y = x[1];
      this.cRec = y;
      return tau;
    }

    /* Câmera: acha a constante que põe os resultados exatamente em cima dos quadros filmados.
       Os quadros nunca saem em intervalos iguais (variam alguns milissegundos), então só o
       encaixe certo faz quase todos os resultados caírem a menos de 1 ms de um quadro. */
    _encaixar() {
      const taus = this.taus;
      const ult = this.res.slice(-40);
      if (ult.length < 12 || taus.length < 30) return;
      const centroK = this.kExato !== null ? this.kExato : this.kEst;
      if (centroK === null) return;
      const perto = (x) => {
        let lo = 0, hi = taus.length - 1;
        while (lo < hi) { const mid = (lo + hi) >> 1; if (taus[mid] < x) lo = mid + 1; else hi = mid; }
        const a = taus[lo] - x, b = lo > 0 ? taus[lo - 1] - x : Infinity;
        return Math.abs(a) < Math.abs(b) ? a : b;
      };
      const nota = (k) => {
        let certos = 0, n = 0, soma = 0;
        for (const e of ult) {
          const x = e.tv + k;
          if (x < taus[0] - 1 || x > taus[taus.length - 1] + 1) continue;
          n++;
          const d = perto(x);
          if (Math.abs(d) < 0.9) { certos++; soma += d; }
        }
        return n >= 10 ? { taxa: certos / n, ajuste: certos ? soma / certos : 0 } : null;
      };
      const r0 = ult[ult.length - 1];
      let melhor = null, segunda = 0;
      for (const tau of taus) {
        if (Math.abs(tau - (r0.tv + centroK)) > 80) continue;
        const k = tau - r0.tv;
        const v = nota(k);
        if (!v) continue;
        if (!melhor || v.taxa > melhor.taxa) { if (melhor) segunda = Math.max(segunda, melhor.taxa); melhor = { k: k + v.ajuste, taxa: v.taxa }; }
        else segunda = Math.max(segunda, v.taxa);
      }
      if (melhor && melhor.taxa >= 0.6 && segunda <= melhor.taxa * 0.6) this.kExato = melhor.k;
      else if (this.kExato !== null) { const v = nota(this.kExato); if (v && v.taxa < 0.3) this.kExato = null; }
      this._calcular();
    }

    /* Resultado para o quadro de tempo tau: entre os dois vizinhos. null = sem como alinhar agora. */
    paraQuadro(tau) {
      const res = this.res;
      if (this.k === null || !res.length) return null;
      const tv = tau - this.k;
      let i = res.length - 1;
      if (tv >= res[i].tv) return tv - res[i].tv > 600 ? null : res[i].r;    // o deste quadro ainda não chegou: vale o mais novo
      while (i > 0 && res[i - 1].tv > tv) i--;
      if (i === 0) return res[0].r;
      const a = res[i - 1], b = res[i];
      // buraco grande entre duas análises: não desliza a caixa por meio segundo, fica com a mais perto
      if (b.tv - a.tv > 400) return tv - a.tv < b.tv - tv ? a.r : b.r;
      return entreResultados(a.r, b.r, (tv - a.tv) / (b.tv - a.tv));
    }

    /* Quanto segurar a imagem (ms) para o quadro sair já com a caixa dele: na câmera, contado da
       captura; em quem assiste, da chegada do quadro. */
    espera() {
      if (!this.atrasos.length) return this.local ? 120 : 0;
      const intervalo = Math.min(200, percentil(this.intervalos, 0.5) || 40);
      return percentil(this.atrasos.map((x) => x[1]), 0.95) + intervalo + 8;
    }
  }
  // reserva (sem rtp): tempo que o servidor leva para receber e abrir um quadro depois que o vídeo chega nele (ms)
  Alinhador.LEITURA_MS = 20;

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
      // vídeo direto (WebRTC) no tempo real; this.direto === false desliga (só JPEG)
      this.link = null;
      this.diretoLigado = false;
      this.diretoFalhas = 0;
      this.diretoMotivo = '';
      this.alinhador = new Alinhador({ local: true });
    }

    get targetFps() { return Math.max(3, this.config.fps * this.fpsFactor); }

    // ── vídeo direto ──
    _querDireto() {
      return this.active && this.direto !== false && temRtc() && Boolean(this.config.imediato) && this.transport === 'ws';
    }

    _trackDaCamera() {
      const s = this.video && this.video.srcObject;
      return (s && s.getVideoTracks && s.getVideoTracks()[0]) || null;
    }

    /* Liga, mantém ou desliga a conexão direta conforme o modo e a rede. Chamado a cada segundo. */
    async _cuidarDoDireto() {
      if (!this._querDireto()) { this._fecharDireto(); return; }
      const track = this._trackDaCamera();
      if (!track || track.readyState !== 'live') return;
      if (this.link) {
        // a câmera foi reaberta (o painel mudou fps/resolução): troca a faixa sem derrubar a conexão
        if (this.link.track !== track) this.link.trocarTrack(track, this.config.fps, this.config.resolucao).catch(() => {});
        return;
      }
      if (now() < (this._diretoDepois || 0)) return;
      const link = this.link = new DirectLink({
        backendUrl: this.backendUrl, token: this.token, streamId: this.streamId, modo: 'enviar', track, fps: this.config.fps,
        altura: this.config.resolucao,
        onState: (st, motivo) => {
          if (this.link !== link) return;
          if (st === 'ligado') { this.diretoLigado = true; this.diretoFalhas = 0; this.diretoMotivo = ''; this._semFila(); }
          else if (st === 'caiu') this._diretoCaiu(motivo === 'prazo' ? 'a rede não deixou fechar a conexão direta' : 'a conexão direta caiu');
        },
      });
      try { await link.abrir(); } catch (e) {
        if (this.link === link) this._diretoCaiu(e.codigo === 'desligado' || e.codigo === 'indisponivel' ? 'vídeo direto indisponível no servidor' : (e.message || 'falhou'), e.codigo, e.espera_s);
      }
    }

    _diretoCaiu(motivo, codigo, esperaS) {
      this._fecharDireto();
      this.diretoFalhas++;
      this.diretoMotivo = motivo || '';
      // tenta de novo com calma: 5 s, 15 s, 45 s... até 5 min (servidor sem o recurso: 5 min direto).
      // Rede perdendo pacotes: o servidor diz quanto esperar (o envio normal segue valendo).
      const espera = esperaS ? esperaS * 1000 + 2000
        : codigo === 'desligado' ? 300000 : Math.min(300000, 5000 * 3 ** Math.min(this.diretoFalhas - 1, 4));
      this._diretoDepois = now() + espera;
    }

    _fecharDireto() {
      const l = this.link;
      this.link = null;
      this.diretoLigado = false;
      this.alinhador.zerar();
      if (l) l.fechar();
    }

    _semFila() {
      for (const e of this.outbox) e.sent = true;     // o que estava na fila em JPEG não serve mais
    }

    start() {
      this.active = true;
      this._statsAt = now();
      this._timers = [
        setInterval(() => this._emitStats(), 1000),
        setInterval(() => this._adapt(), 500),
        setInterval(() => this._heartbeat(), 1000),
        setInterval(() => this._cuidarDoDireto().catch(() => {}), 1000),
      ];
      this._connectWs();
      this._captureLoop();
    }

    stop() {
      this.active = false;
      (this._timers || []).forEach(clearInterval);
      clearTimeout(this._retryTimer);
      clearTimeout(this._capTimer);
      this._fecharDireto();
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
      if (old.modo !== this.config.modo || old.fps !== this.config.fps || old.resolucao !== this.config.resolucao
          || Boolean(old.imediato) !== Boolean(this.config.imediato)) {
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
          case 'mover': this.onMove && this.onMove(d.url); break;
          case 'direto': if (d.usar === false) this._diretoCaiu('a rede estava perdendo pacotes: voltou ao envio normal', 'instavel', d.espera_s); break;
          case 'pong':
            if (d.t) this.rtt = this.rtt ? this.rtt * 0.7 + (now() - d.t) * 0.3 : now() - d.t;
            this.alinhador.pong(d.t, d.s);
            break;
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
      if (this.diretoLigado) return;          // o vídeo está indo por WebRTC: nada de JPEG
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
        if (t - e.at > (realtime ? 250 : keepMs)) { e.sent = true; this.c.dropped++; continue; }
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
      if (this.diretoLigado) this.alinhador.resultado(d);
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

    async _emitStats() {
      const t = now();
      const dt = Math.max(0.001, (t - this._statsAt) / 1000);
      const rtc = this.diretoLigado && this.link ? await this.link.medir() : null;
      const s = {
        direto: Boolean(rtc), rota: rtc ? rtc.rota : '', diretoMotivo: this.diretoMotivo,
        transport: rtc ? 'direto' : (this.transport || 'reconectando'), mode: this.transport,
        captureFps: this.c.captured / dt, sentFps: this.c.sent / dt, resultFps: this.c.results / dt,
        dropped: this.c.dropped, kbps: (this.c.bytes * 8) / 1000 / dt, rtt: this.rtt,
        quality: this.quality, scale: this.scale, targetFps: this.targetFps,
        pending: this.outbox.filter((e) => !e.sent).length,
        serverDelay: this.server.atraso_ms, serverFps: this.server.fps_analise, shownFps: this.server.fps_exibido,
      };
      if (rtc) {
        s.captureFps = s.sentFps = rtc.fps; s.kbps = rtc.kbps; s.quality = 1; s.scale = 1; s.pending = 0;
        s.largura = rtc.largura; s.altura = rtc.altura; s.limite = rtc.limite;
        // menor tempo entre a captura e o servidor ler o quadro: codificar + sair + metade da ida e volta + leitura
        this.alinhador.subida = rtc.codificar + rtc.sair + rtc.rtt / 2 + Alinhador.LEITURA_MS;
        s.alinhado = this.alinhador.pronto();
      }
      this.c = { captured: 0, sent: 0, bytes: 0, dropped: 0, results: 0 };
      this._statsAt = t;
      this.onStats && this.onStats(s);
    }
  }

  /* ── Exibição ──────────────────────────────────────────────────── */
  class LivePlayer {
    constructor({ canvas, getFrame = null, onStats = null, showBoxes = true, alinhador = null, atrasar = false }) {
      this.canvas = canvas;
      this.getFrame = getFrame;
      this.onStats = onStats;
      this.showBoxes = showBoxes;
      // vídeo ao vivo: quem acerta a caixa com o quadro (Alinhador). atrasar: a própria tela segura a
      // imagem (câmera: guarda os quadros e mostra com um pequeno atraso, já com a caixa de cada um).
      this.alinhador = alinhador;
      this.atrasar = atrasar;
      this.fila = [];            // [{tau, bmp}] quadros da prévia esperando a vez
      this.quadroVivo = null;
      this.atrasoVivo = null;
      this.ultimoVivo = null;
      this._capturando = 0;
      this._falhasPrevia = 0;
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
      this.live = null;          // <video> ao vivo por baixo do canvas: só as caixas são desenhadas
      this.liveResult = null;
      this._statsAt = now();
      this._raf = requestAnimationFrame(() => this._render());
    }

    /* Vídeo ao vivo por baixo (câmera local ou vídeo direto): o canvas fica transparente e leva só
       as caixas do resultado mais novo. null volta ao modo de quadros. */
    setLive(video, { alinhador } = {}) {
      if (alinhador !== undefined) this.alinhador = alinhador;
      if (this.live === video) {
        // mesmo vídeo, alinhador novo (o vídeo direto ligou ou caiu): acompanha os quadros ou solta a prévia
        if (!this.alinhador || !this.atrasar) this._soltarPrevia();
        if (this.live && this._seguindo !== this.live) this._seguirQuadros(this.live);
        return;
      }
      this._soltarPrevia();
      this.live = video || null;
      this.liveResult = null;
      this.ultimoVivo = null;
      for (const e of this.entries.values()) if (e.bitmap) e.bitmap.close();
      this.entries.clear();
      if (this.current && this.current.bitmap) this.current.bitmap.close();
      this.current = null;
      if (this.live) this._seguirQuadros(this.live);
      this._paint();
    }

    /* Acompanha cada quadro que o vídeo apresenta: é nessa hora que se sabe o tempo dele. */
    _seguirQuadros(v) {
      if (!this.alinhador || !v.requestVideoFrameCallback) return;
      this._seguindo = v;
      const aoQuadro = (agora, meta) => {
        if (this.closed || this.live !== v) { if (this._seguindo === v) this._seguindo = null; return; }
        v.requestVideoFrameCallback(aoQuadro);
        const al = this.alinhador;
        const tau = al ? al.quadro(meta) : null;
        if (tau === null || !al.pronto()) return;
        if (this.atrasar) this._guardar(v, tau);
        else {
          const r = al.paraQuadro(tau);
          this.liveResult = r || this.ultimoVivo;
          this._paint();
        }
      };
      v.requestVideoFrameCallback(aoQuadro);
    }

    /* Câmera: cópia reduzida do quadro, guardada até a caixa dele chegar. */
    _guardar(v, tau) {
      if (this._falhasPrevia > 8 || this._capturando > 2 || !window.createImageBitmap) return;
      if (tau - (this._tauGuardado || 0) < 24) return;          // prévia a no máximo ~40 quadros por segundo
      const vw = v.videoWidth, vh = v.videoHeight;
      if (!vw || !vh) return;
      this._tauGuardado = tau;
      const dpr = window.devicePixelRatio || 1;
      const e = Math.min(1, 960 / Math.max(vw, vh),
        Math.max(this.canvas.clientWidth * dpr / vw, this.canvas.clientHeight * dpr / vh) || 1);
      // O quadro que o navegador entrega aqui às vezes já é o seguinte ao que ele acabou de anunciar.
      // O VideoFrame traz o tempo do próprio conteúdo: é esse que vale para a caixa.
      let fonte = v, quadro = null;
      if (window.VideoFrame) {
        try { quadro = new VideoFrame(v); fonte = quadro; if (typeof quadro.timestamp === 'number') tau = quadro.timestamp / 1000; }
        catch (err) { quadro = null; fonte = v; }
      }
      this._capturando++;
      createImageBitmap(fonte, { resizeWidth: Math.max(2, Math.round(vw * e)), resizeHeight: Math.max(2, Math.round(vh * e)), resizeQuality: 'low' })
        .finally(() => { if (quadro) quadro.close(); })
        .then((bmp) => {
          this._capturando--;
          if (this.closed || this.live !== v) { bmp.close(); return; }
          let i = this.fila.length;
          while (i > 0 && this.fila[i - 1].tau > tau) i--;
          this.fila.splice(i, 0, { tau, bmp });
          while (this.fila.length > 60) this.fila.shift().bmp.close();
        }, () => { this._capturando--; this._falhasPrevia++; });
    }

    _soltarPrevia() {
      for (const q of this.fila) q.bmp.close();
      this.fila = [];
      if (this.quadroVivo) { this.quadroVivo.bmp.close(); this.quadroVivo = null; }
      this.atrasoVivo = null;
      this._tauGuardado = 0;
    }

    /* Câmera, a cada quadro da tela: mostra o quadro guardado cuja hora chegou, com a caixa dele. */
    _mostrarPrevia(t) {
      const al = this.alinhador;
      if (!al || !al.pronto() || this._falhasPrevia > 8) {
        if (this.quadroVivo || this.fila.length) { this._soltarPrevia(); this.liveResult = this.ultimoVivo; this._paint(); }
        return false;
      }
      const alvo = Math.min(700, al.espera());
      // o atraso sobe depressa (senão a caixa falta) e desce devagar (senão a imagem dá pulos)
      this.atrasoVivo = this.atrasoVivo === null ? alvo : this.atrasoVivo + Math.max(-0.3, Math.min(4, alvo - this.atrasoVivo));
      // a fila guarda o tempo do vídeo de cada quadro; al.off leva esse tempo para o relógio daqui (hora da captura)
      const limite = t - this.atrasoVivo - (al.off || 0);
      let novo = null;
      while (this.fila.length && this.fila[0].tau <= limite) {
        if (novo) novo.bmp.close();
        novo = this.fila.shift();
      }
      if (novo) {
        if (this.quadroVivo) this.quadroVivo.bmp.close();
        this.quadroVivo = novo;
        this.liveResult = al.paraQuadro(novo.tau) || this.ultimoVivo;
        this._paint();
      }
      return true;
    }

    close() {
      this.closed = true;
      cancelAnimationFrame(this._raf);
      this._soltarPrevia();
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

    get latestResult() { return this.liveResult || (this.current ? this.current.result : null); }

    _entry(seq) {
      let e = this.entries.get(seq);
      if (!e) { e = { seq }; this.entries.set(seq, e); }
      return e;
    }

    pushResult(r) {
      if (this.closed) return;
      if (this.live) {
        this.lastArrival = now();
        this.shown++;
        this.ultimoVivo = r;
        // alinhado: o desenho acompanha os quadros do vídeo (cada um com a caixa da hora dele);
        // sem como alinhar (servidor antigo, navegador sem o recurso), o mais novo vale na hora
        const al = this.alinhador;
        if (al && al.pronto() && this.live.requestVideoFrameCallback && (!this.atrasar || this._falhasPrevia <= 8)) return;
        this.liveResult = r;
        this._paint();
        return;
      }
      if (r.seq === null || r.seq === undefined) return;
      const e = this._entry(r.seq);
      e.result = r;
      e.t = r.t;
      e.arrival = e.arrival || now();
      if (!e.blob && this.getFrame) e.blob = this.getFrame(r.seq);
      if (!e.blob && this.getFrame) { this.entries.delete(r.seq); return; }  // quadro local já descartado
      this._decode(e);
    }

    pushFrame(seq, t, blob) {
      if (this.closed || this.live) return;
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
      if (e.result.imediato) { e.agora = true; return; }   // imediato: sai no próximo quadro da tela, sem buffer
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
        if (e.agora || this.base + e.t * 1000 + this.jb <= t) {
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
      } else if (this.live) {
        const previa = this.atrasar && this._mostrarPrevia(t);
        if (t - this.lastArrival > 1200 && this.liveResult) {      // resultado velho some
          this.liveResult = this.ultimoVivo = null;
          if (previa) this._soltarPrevia();
          this._paint();
        } else if (t - (this._livePaint || 0) > 500) this._paint();
      } else if (this.annotated || (this.current && t - this.lastArrival > 1500) || !this.current) {
        this._paint();
      }
      if (t - this._statsAt >= 1000) {
        const dt = (t - this._statsAt) / 1000;
        this.onStats && this.onStats({ fps: this.shown / dt, buffer: Math.round(this.jb), pending: this.entries.size,
          stalled: this.lastArrival > 0 && t - this.lastArrival > 1500,
          alinhado: Boolean(this.live && this.alinhador && this.alinhador.pronto()),
          exato: Boolean(this.live && this.alinhador && this.alinhador.pronto() && this.alinhador.exato),
          atrasoPrevia: this.quadroVivo && this.atrasoVivo !== null ? Math.round(this.atrasoVivo) : null });
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
      if (this.live) {
        this._livePaint = now();
        const q = this.quadroVivo;
        if (q) {                  // câmera: o quadro guardado cobre o vídeo ao vivo que está por baixo
          const e = Math.min(cw / q.bmp.width, ch / q.bmp.height);
          ctx.fillStyle = '#000';
          ctx.fillRect(0, 0, cw, ch);
          ctx.drawImage(q.bmp, (cw - q.bmp.width * e) / 2, (ch - q.bmp.height * e) / 2, q.bmp.width * e, q.bmp.height * e);
        }
        if (this.liveResult) drawOverlay(canvas, this.liveResult, { showBoxes: this.showBoxes, clear: false });
        if (this.message) this._texto(ctx, cw, ch, this.message, false);
        return;
      }
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
        this._texto(ctx, cw, ch, text, Boolean(img));
      }
    }

    _texto(ctx, cw, ch, text, escurecer) {
      {
        if (escurecer) { ctx.fillStyle = 'rgba(0,1,42,.45)'; ctx.fillRect(0, 0, cw, ch); }
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
      // vídeo direto: this.videoEl (um <video>) recebe o mesmo vídeo que a câmera manda por WebRTC
      this.link = null;
      this.diretoLigado = false;
      this.diretoFalhas = 0;
      this.info = null;
      this.alinhador = new Alinhador({ local: false });
      this.atrasoVideo = 0;      // ms que o vídeo está sendo segurado para a caixa chegar junto
    }

    start() {
      this.active = true;
      this._connect();
      this._timer = setInterval(() => this._heartbeat(), 2000);
      this._timerDireto = setInterval(() => this._cuidarDoDireto().catch(() => {}), 1000);
      this._timerAtraso = setInterval(() => this._acertarAtraso(), 400);
    }

    /* Segura o vídeo o bastante para cada quadro aparecer já com a caixa dele (e não mais que isso). */
    _acertarAtraso() {
      const al = this.alinhador, l = this.link;
      if (!this.diretoLigado || !l) return;
      if (++this._voltasAtraso % 8 === 1) l.medir().then((m) => { if (m && this.link === l) al.dv = m.rtt / 2; }).catch(() => {});
      if (!al.pronto() || !al.atrasos.length) return;
      const pedido = Math.max(0, Math.min(600, al.espera()));
      // sobe logo (senão o quadro aparece antes da caixa) e desce devagar (senão o vídeo acelera e freia à toa)
      const alvo = this.atrasoVideo + Math.max(-5, Math.min(60, pedido - this.atrasoVideo));
      if (Math.abs(alvo - this.atrasoVideo) < 1) return;
      this.atrasoVideo = alvo;
      if (!l.atrasar(alvo)) this.atrasoVideo = 0;
    }

    stop() {
      this.active = false;
      clearInterval(this._timer);
      clearInterval(this._timerDireto);
      clearInterval(this._timerAtraso);
      this._fecharDireto();
      clearTimeout(this._retry);
      this.polling = false;
      const ws = this.ws;
      this.ws = null;
      if (ws) try { ws.close(); } catch (e) { /* */ }
    }

    _status(st, extra) { this.onStatus && this.onStatus(st, extra); }

    // ── vídeo direto ──
    /* A câmera está mandando vídeo direto? Então o painel assiste o mesmo vídeo por WebRTC e pede ao
       servidor só os resultados; se a conexão não fecha ou cai, volta aos quadros pelo WebSocket. */
    async _cuidarDoDireto() {
      const i = this.info || {};
      const quer = this.active && this.direto !== false && this.videoEl && temRtc() && i.direto === true
        && (i.config || {}).imediato && this.ws && this.ws.readyState === 1;
      if (!quer) { if (this.link) this._fecharDireto(); return; }
      if (this.link || now() < (this._diretoDepois || 0)) return;
      const link = this.link = new DirectLink({
        backendUrl: this.backendUrl, token: this.token, streamId: this.streamId, modo: 'assistir',
        onTrack: (stream) => {
          if (this.link !== link) return;
          const v = this.videoEl;
          v.srcObject = stream;
          v.muted = true;
          v.play().catch(() => {});
          const aoTocar = () => {
            if (this.link !== link || this.diretoLigado) return;
            this.diretoLigado = true;
            this.diretoFalhas = 0;
            this._voltasAtraso = 0;
            this.atrasoVideo = 0;
            this.alinhador.zerar();
            this._pedirVideo(false);
            this.player.setLive(v, { alinhador: this.alinhador });
            this.onDireto && this.onDireto(true, link);
            this._status('direto');
          };
          if (v.readyState >= 2 && v.videoWidth) aoTocar(); else v.addEventListener('playing', aoTocar, { once: true });
        },
        onState: (st) => { if (this.link === link && st === 'caiu') this._diretoCaiu(); },
      });
      try { await link.abrir(); } catch (e) { if (this.link === link) this._diretoCaiu(e.codigo); }
    }

    _diretoCaiu(codigo) {
      this._fecharDireto();
      this.diretoFalhas++;
      const espera = codigo === 'sem_video' ? 2000 : Math.min(300000, 5000 * 3 ** Math.min(this.diretoFalhas - 1, 4));
      this._diretoDepois = now() + espera;
    }

    _fecharDireto() {
      const l = this.link;
      this.link = null;
      if (l) l.fechar();
      if (this.diretoLigado) {
        this.diretoLigado = false;
        this._pedirVideo(true);
        if (this.player) this.player.setLive(null);
        if (this.videoEl) this.videoEl.srcObject = null;
        this.onDireto && this.onDireto(false, null);
        if (this.active && this.ws) this._status('ws');
      }
    }

    _pedirVideo(on) {
      const ws = this.ws;
      if (ws && ws.readyState === 1) try { ws.send(JSON.stringify({ type: 'video', on })); } catch (e) { /* */ }
    }

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
          if (this.diretoLigado) return;      // já assistindo pelo vídeo direto
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
          if (this.diretoLigado) { this.alinhador.resultado(d); this.player.pushResult(d); }   // caixas sobre o vídeo ao vivo
          else {
            this.pending.set(d.seq, d);
            if (this.pending.size > 240) this.pending.delete(this.pending.keys().next().value);
          }
          this.onResult && this.onResult(d);
        } else if (d.type === 'pong') {
          this.alinhador.pong(d.t, d.s);
        } else if (d.type === 'pronto' || d.type === 'estado') {
          if (!ready) {
            ready = true; clearTimeout(giveUp); this.ws = ws; this.attempt = 0; this.failures = 0; this.polling = false; this.player.setMessage('');
            if (this.diretoLigado) this._pedirVideo(false);   // reconectou com o vídeo direto ainda de pé
            this._status(this.diretoLigado ? 'direto' : 'ws');
          }
          this.info = d;
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
      try { ws.send(JSON.stringify({ type: 'ping', t: now() })); } catch (e) { /* */ }
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
    openCamera, trackInfo, LiveSender, LivePlayer, LiveViewer, DirectLink, Alinhador, entreResultados, uploadAnalysisFile, drawOverlay,
    drawEpiIcon, drawEpiRow, EPI_ICONS,
    packFrame, unpackFrame, itemLabel, formatDuration, LABELS, COLORS, POSTURE, HEAD, MODES, FPS_OPTIONS, RES_OPTIONS,
  };
})();
