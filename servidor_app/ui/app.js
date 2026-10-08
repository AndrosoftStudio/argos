/* Argos EPI Servidor - interface da janela.
   Conversa com o supervisor (supervisor.py) pela API local /api/* usando o
   token que veio no endereco. Com a janela do programa (WebView2 no Windows,
   Electron no Linux) troca mensagens para fechar/esconder. */
'use strict';
const TOKEN = new URLSearchParams(location.search).get('t') || sessionStorage.getItem('argos_t') || '';
sessionStorage.setItem('argos_t', TOKEN);
if (location.search) history.replaceState(null, '', location.pathname + location.hash);

const ICONES = {
  home: '<path d="M4 10.5 12 4l8 6.5V19a1.5 1.5 0 0 1-1.5 1.5H15v-6H9v6H5.5A1.5 1.5 0 0 1 4 19z"/>',
  camera: '<rect x="2.5" y="6" width="13" height="12" rx="2"/><path d="m15.5 10.5 5.3-3a.5.5 0 0 1 .7.4v8.2a.5.5 0 0 1-.7.4l-5.3-3"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4.5 6h.01M4.5 12h.01M4.5 18h.01" stroke-width="3"/>',
  gear: '<path d="M19.27 9.78L22.05 10.23L22.05 13.77L19.27 14.22L18.71 15.57L20.36 17.85L17.85 20.36L15.57 18.71L14.22 19.27L13.77 22.05L10.23 22.05L9.78 19.27L8.43 18.71L6.15 20.36L3.64 17.85L5.29 15.57L4.73 14.22L1.95 13.77L1.95 10.23L4.73 9.78L5.29 8.43L3.64 6.15L6.15 3.64L8.43 5.29L9.78 4.73L10.23 1.95L13.77 1.95L14.22 4.73L15.57 5.29L17.85 3.64L20.36 6.15L18.71 8.43Z" stroke-width="1.7"/><circle cx="12" cy="12" r="3.2"/>',
  globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.6 3.7 5.4 3.7 8.5s-1.2 5.9-3.7 8.5c-2.5-2.6-3.7-5.4-3.7-8.5S9.5 6.1 12 3.5z"/>',
  power: '<path d="M12 3.5v8"/><path d="M6.6 6.8a7.5 7.5 0 1 0 10.8 0"/>',
  down: '<path d="M12 4v11"/><path d="m7 10.5 5 5 5-5"/><path d="M5 20h14"/>',
  server: '<rect x="3.5" y="4" width="17" height="7" rx="1.5"/><rect x="3.5" y="13" width="17" height="7" rx="1.5"/><path d="M7 7.5h.01M7 16.5h.01"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  link: '<path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.3-4.7L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.3 4.7L20 16"/><path d="M20 20v-4h-4"/>',
  play: '<path d="M7 4.8v14.4a.8.8 0 0 0 1.2.7l11.3-7.2a.8.8 0 0 0 0-1.4L8.2 4.1A.8.8 0 0 0 7 4.8z" fill="currentColor"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
  alert: '<path d="M10.3 4.2 2.6 17.5a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4.5M12 17.2v.3"/>',
  chip: '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/><path d="M9.5 3v3.5M14.5 3v3.5M9.5 17.5V21M14.5 17.5V21M3 9.5h3.5M3 14.5h3.5M17.5 9.5H21M17.5 14.5H21"/>',
  gauge: '<path d="M4 17a8 8 0 1 1 16 0"/><path d="m12 17 4-5"/>',
  wifi: '<path d="M2 9a14.5 14.5 0 0 1 20 0"/><path d="M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0"/><circle cx="12" cy="19.3" r="1.1" fill="currentColor"/>',
  download: '<path d="M12 4v11"/><path d="m7 10.5 5 5 5-5"/><path d="M5 20h14"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  monitor: '<rect x="3" y="4" width="18" height="12.5" rx="2"/><path d="M8.5 20.5h7M12 16.5v4"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
  key: '<circle cx="8" cy="15" r="4.5"/><path d="m11.2 11.8 8.8-8.8M16.5 6.5 19 9M14 9l2 2"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8"/><path d="M18.2 14.2A6.5 6.5 0 0 1 21.5 20"/>',
  zap: '<path d="M13 2.5 4.5 13.5H12l-1 8 8.5-11H12z"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8"/>',
  helmet: '<path d="M6.5 9.5a5.5 5.5 0 0 1 11 0"/><path d="M4.5 9.5h15"/><path d="M12 4v3"/><path d="M8.6 11a3.4 3.4 0 0 0 6.8 0"/><path d="M4.5 21v-1a5 5 0 0 1 5-5h5a5 5 0 0 1 5 5v1"/><path d="m9.5 15 2.5 3.2 2.5-3.2"/>',
  'video-off': '<path d="M10.5 6h3.5a1.5 1.5 0 0 1 1.5 1.5v3l5.3-3a.5.5 0 0 1 .7.4v8.2a.5.5 0 0 1-.4.5"/><path d="M15.5 15.5v1a1.5 1.5 0 0 1-1.5 1.5H4A1.5 1.5 0 0 1 2.5 16.5v-9A1.5 1.5 0 0 1 4 6h1"/><path d="m3 3 18 18"/>',
  tray: '<path d="M3.5 13.5h5l1.5 2.5h4l1.5-2.5h5"/><path d="M5.5 5h13l2 8.5V18a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18v-4.5z"/>',
  hash: '<path d="M5 9h15M4 15h15M10 3.5 8 20.5M16 3.5l-2 17"/>',
  unlink: '<path d="M13.5 6.5l1-1a4.5 4.5 0 0 1 6.4 6.4l-1 1"/><path d="M10.5 17.5l-1 1a4.5 4.5 0 0 1-6.4-6.4l1-1"/><path d="m4 4 16 16"/>',
};
const ic = (n, cls = '') => `<svg class="ic ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONES[n] || ''}</svg>`;
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const $ = (s, el = document) => el.querySelector(s);
function hidratarIcones(raiz = document) {
  raiz.querySelectorAll('svg[data-ic]').forEach(s => { s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true'); s.innerHTML = ICONES[s.dataset.ic] || ''; });
}

/* ── janela do programa (ponte) ───────────────────────────── */
const host = window.chrome && window.chrome.webview ? {
  casca: 'webview2',
  enviar: (m) => window.chrome.webview.postMessage(JSON.stringify(m)),
  ouvir: (f) => window.chrome.webview.addEventListener('message', e => f(typeof e.data === 'string' ? JSON.parse(e.data) : e.data)),
} : window.argosJanela ? {
  casca: 'electron',
  enviar: (m) => window.argosJanela.enviar(m),
  ouvir: (f) => window.argosJanela.ouvir(f),
} : null;

/* janela com design proprio: a barra azul do topo e a barra de titulo (arrasta, minimiza, maximiza, fecha) */
if (host) {
  document.documentElement.classList.add('na-janela', 'casca-' + host.casca);
  if (host.casca === 'webview2') {   // no Electron quem arrasta e o CSS (app-region)
    document.querySelector('.topbar').addEventListener('mousedown', (ev) => {
      if (ev.button !== 0 || ev.target.closest('button,a,input,select,label')) return;
      host.enviar({ tipo: 'janela', acao: ev.detail === 2 ? 'max' : 'arrastar' });
    });
  }
  document.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-janela]');
    if (b) host.enviar({ tipo: 'janela', acao: b.dataset.janela });
  });
}
function janelaMaximizada(sim) {
  document.documentElement.classList.toggle('maximizada', !!sim);
  const b = document.querySelector('[data-janela="max"]');
  if (b) { b.title = sim ? 'Restaurar' : 'Maximizar'; b.setAttribute('aria-label', b.title); }
}
// cor da barra do topo no tema atual: a moldura da janela acompanha
const corDaBarra = () => getComputedStyle(document.documentElement).getPropertyValue('--brand').trim();

/* ── API ──────────────────────────────────────────────────── */
async function api(caminho, corpo) {
  const r = await fetch(caminho, {
    method: corpo ? 'POST' : 'GET', cache: 'no-store',
    headers: { 'X-Argos-Token': TOKEN, ...(corpo ? { 'Content-Type': 'application/json' } : {}) },
    body: corpo ? JSON.stringify(corpo) : undefined,
  });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}
const acao = (nome, extra = {}) => api('/api/acao', { acao: nome, ...extra }).catch(() => aviso('Não consegui falar com o programa.'));

let toastT;
function aviso(txt) {
  const t = $('#toast');
  t.textContent = txt;
  t.classList.add('ver');
  clearTimeout(toastT);
  toastT = setTimeout(() => t.classList.remove('ver'), 2800);
}

/* ── estado ───────────────────────────────────────────────── */
let E = null;            // ultimo /api/estado
let pagina = '';
let ultimaAssinatura = '';
let conectado = true;

function aplicarTema(tema) {
  if (tema === 'claro' || tema === 'escuro') document.documentElement.dataset.tema = tema;
  else delete document.documentElement.dataset.tema;
}

const VARIANTES = { nvidia: 'Placa NVIDIA (CUDA)', dml: 'Placa AMD/Intel (DirectML)', cpu: 'Só processador (CPU)' };
const TIPOS = { nvidia: 'GPU NVIDIA', dml: 'GPU AMD/Intel', cpu: 'Processador' };
const pct = (v) => (v == null || isNaN(v)) ? null : Math.max(0, Math.min(100, Math.round(Number(v))));
const gb = (b) => b ? (b / 1073741824).toFixed(0) + ' GB' : '';
const iniciais = (n) => String(n || '?').trim().split(/\s+/).filter(Boolean).slice(0, 2).map(p => p[0]).join('').toUpperCase() || '?';

function situacao() {
  if (!E) return { txt: 'Abrindo...', cls: 'meio' };
  if (E.app.saindo) return { txt: 'Encerrando...', cls: 'meio' };
  const s = E.servidor.estado;
  if (s === 'ligado') return { txt: 'Servidor ligado', cls: 'on' };
  if (s === 'iniciando') return { txt: 'Ligando...', cls: 'meio' };
  if (s === 'parando') return { txt: 'Desligando...', cls: 'meio' };
  if (s === 'erro' || E.banco.estado === 'erro') return { txt: 'Com problema', cls: '' };
  if (E.app.etapa) return { txt: 'Preparando...', cls: 'meio' };
  return { txt: 'Servidor desligado', cls: '' };
}

function atualizarTopo() {
  const s = situacao();
  $('#pill .dot').className = 'dot ' + s.cls;
  $('#pill-txt').textContent = conectado ? s.txt : 'Sem resposta do programa';
}

/* ── páginas ──────────────────────────────────────────────── */
function cabeca(icone, titulo, extra = '', acoes = '') {
  return `<header class="cabeca"><span class="cabeca-ico">${ic(icone)}</span><h1>${titulo}</h1>${extra}${acoes ? `<div class="cabeca-acoes">${acoes}</div>` : ''}</header>`;
}

function blocoConta(r) {
  if (!r) {
    return `<section class="bloco"><header class="bloco-titulo">${ic('user')}<h2>Conta</h2></header>
      <p class="texto">A conta aparece aqui quando o servidor terminar de ligar.</p></section>`;
  }
  if (r.vinculado) {
    const c = r.conta || {};
    const srv = r.servidor || {};
    return `<section class="bloco"><header class="bloco-titulo">${ic('user')}<h2>Conta</h2></header>
      <div class="conta"><span class="avatar" aria-hidden="true">${esc(iniciais(c.nome || c.email))}</span>
        <div><p class="conta-nome">${esc(c.nome || 'Conta do Argos EPI')}</p><p class="conta-email">${esc(c.email || '')}</p>
        <p class="selo-ok">${ic('check')}Servidor vinculado${srv.nome ? ' como “' + esc(srv.nome) + '”' : ''}</p></div></div>
      <p class="texto" style="margin-top:12px">As câmeras desta conta podem rodar neste computador. Os EPIs, a equipe e as áreas chegam sozinhos pela malha${r.pares ? ` (${r.pares} outro(s) servidor(es))` : ''}.</p>
      <div class="acoes"><button type="button" class="btn btn-primary" data-acao="painel">${ic('globe')}Abrir o painel</button>
        <button type="button" class="btn btn-secondary" data-desvincular-abrir>${ic('unlink')}Desvincular da conta</button></div>
    </section>`;
  }
  const exp = r.expira_em ? Math.max(0, Math.round((r.expira_em * 1000 - Date.now()) / 60000)) : 0;
  return `<section class="bloco"><header class="bloco-titulo">${ic('link')}<h2>Vincule à sua conta</h2></header>
    <ol class="passos">
      <li>Toque em “Vincular agora”: o site do Argos EPI abre no navegador e encontra este servidor sozinho.</li>
      <li>Se o navegador perguntar, permita o acesso aos apps deste computador.</li>
      <li>Entre na sua conta e clique em Vincular. Pronto: esta tela mostra a conta.</li>
    </ol>
    <div class="acoes"><button type="button" class="btn btn-gold" data-acao="vincular">${ic('link')}Vincular agora</button></div>
    ${r.codigo ? `<p class="codigo">O site não achou? Digite este código: <code>${esc(r.codigo)}</code>${exp ? `<span>(vale ${exp} min)</span>` : ''}</p>` : ''}
    ${r.erro ? `<p class="texto" style="margin-top:8px;color:var(--dn)">${esc(r.erro)}</p>` : ''}
  </section>`;
}

function blocoServidor() {
  const s = E.servidor, r = E.resumo, b = E.banco, m = E.modelos;
  let ico = 'server', cls = '', nome = 'Desligado', sub = 'Toque em Ligar para voltar a processar as câmeras.';
  if (s.estado === 'ligado') { ico = 'check'; cls = 'ok'; nome = 'Ligado'; sub = `Porta ${s.porta} · ligado ${desde(s.desde)}`; }
  else if (s.estado === 'iniciando' || E.app.etapa) { ico = 'clock'; nome = 'Ligando...'; sub = E.app.etapa || 'Carregando a IA e o banco. Leva alguns segundos.'; }
  else if (s.estado === 'parando') { ico = 'clock'; nome = 'Desligando...'; sub = ''; }
  else if (s.estado === 'erro' || b.estado === 'erro') { ico = 'alert'; cls = 'erro'; nome = 'Com problema'; sub = s.erro || b.erro || 'Veja o Registro.'; }
  const ligado = s.estado === 'ligado' || s.estado === 'iniciando';
  const mq = (r && r.maquina) || {};
  const tipo = r ? (TIPOS[r.tipo] || r.tipo) + (mq.gpu_name ? ' · ' + mq.gpu_name : '') : (VARIANTES[E.app.variante] || '');
  const medidor = (rot, v) => v == null ? '' : `<div class="medidor"><span>${rot}</span><span class="trilho"><span class="enche" style="width:${v}%"></span></span><span class="num">${v}%</span></div>`;
  return `<section class="bloco"><header class="bloco-titulo">${ic('server')}<h2>Este servidor</h2></header>
    <div class="estado-grande"><span class="estado-ico ${cls}">${ic(ico)}</span><div><p class="estado-nome">${nome}</p><p class="estado-sub">${esc(sub)}</p></div></div>
    ${m.estado === 'baixando' ? `<div class="progresso"><span class="giro"></span>Modelos de rosto<span class="trilho"><span class="enche" style="width:${m.pct}%"></span></span>${m.pct}%</div>` : ''}
    <ul class="lista-info">
      ${tipo ? `<li>${ic('chip')}<span>${esc(tipo)}</span></li>` : ''}
      ${mq.cpu_count ? `<li>${ic('gauge')}<span>${mq.cpu_count} núcleos · ${gb(mq.ram_total)} de memória</span></li>` : ''}
      ${r && r.url_local ? `<li>${ic('wifi')}<span>Na rede: <a data-acao="link" data-url="${esc(r.url_local)}">${esc(r.url_local)}</a></span></li>` : ''}
      ${linhaInternet(r)}
      ${blocoAjuda(r)}
    </ul>
    ${avisoInternet(r)}
    ${r ? `<div class="medidores">${medidor('Processador', pct(mq.cpu))}${medidor('Memória', pct(mq.ram_pct))}${medidor('Livre p/ câmeras', pct(r.disponivel))}</div>` : ''}
    <div class="acoes">
      ${ligado ? `<button type="button" class="btn btn-secondary" data-acao="reiniciar">${ic('refresh')}Reiniciar</button>
                  <button type="button" class="btn btn-danger" data-acao="desligar">${ic('power')}Desligar</button>`
               : `<button type="button" class="btn btn-primary" data-acao="ligar" ${s.estado === 'parando' ? 'disabled' : ''}>${ic('play')}Ligar</button>`}
    </div>
  </section>`;
}

/* endereço público (túnel): é por ele que o celular-câmera e o painel aberto em outro lugar chegam aqui */
const TUNEL_FALHOU = ['falhou', 'bloqueado', 'sem_programa'];
function linhaInternet(r) {
  if (!r) return '';
  if (r.cloudflare_url) return `<li>${ic('globe')}<span>Internet: <a data-acao="link" data-url="${esc(r.cloudflare_url)}">${esc(r.cloudflare_url)}</a></span></li>`;
  const t = r.tunel || {};
  if (t.estado === 'abrindo') return `<li>${ic('globe')}<span>Internet: abrindo o endereço público...</span></li>`;
  if (TUNEL_FALHOU.includes(t.estado)) return `<li>${ic('globe')}<span>Internet: <strong style="color:var(--dn)">sem endereço público</strong></span></li>`;
  return '';
}
function avisoInternet(r) {
  const t = (r && !r.cloudflare_url && r.tunel) || {};
  if (!TUNEL_FALHOU.includes(t.estado)) return '';
  return `<p class="faixa erro" style="margin-top:12px;flex-wrap:nowrap;align-items:flex-start">${ic('alert')}<span style="flex:1;min-width:0">Sem endereço de internet: celular usado como câmera e painel aberto em outro computador não alcançam este servidor. ${esc(t.erro || '')}${t.estado === 'falhou' ? ' Nova tentativa automática em instantes.' : ''}</span></p>`;
}

/* servidores da mesma conta e da mesma rede dividem o trabalho das câmeras (rostos e EPIs) */
function blocoAjuda(r) {
  if (!r) return '';
  const pares = (r.ajuda || []).filter(p => p.disponivel);
  const usados = pares.filter(p => (p.usados.rosto || 0) + (p.usados.epi || 0) > 0);
  const feitos = r.ajudou ? (r.ajudou.rosto || 0) + (r.ajudou.epi || 0) : 0;
  let h = '';
  if (usados.length) h += `<li>${ic('users')}<span>Divide o trabalho das câmeras com ${usados.map(p => esc(p.nome)).join(', ')}</span></li>`;
  else if (pares.length) h += `<li>${ic('users')}<span>${pares.map(p => esc(p.nome)).join(', ')} na mesma rede: ajuda quando for mais rápido que este</span></li>`;
  if (feitos) h += `<li>${ic('zap')}<span>Já adiantou ${feitos.toLocaleString('pt-BR')} quadro(s) para outros servidores da conta</span></li>`;
  return h;
}

function blocoCamerasResumo() {
  const cams = (E.resumo && E.resumo.cameras) || [];
  const ativas = cams.filter(c => c.ativa).length;
  const comFalta = cams.filter(c => (c.faltando || []).length).length;
  return `<section class="bloco"><header class="bloco-titulo">${ic('camera')}<h2>Câmeras</h2>
      <div class="acoes-dir"><a class="btn btn-secondary btn-sm" href="#cameras">${ic('camera')}Ver câmeras</a></div></header>
    ${cams.length
      ? `<p class="texto"><strong>${cams.length}</strong> câmera(s) neste servidor, <strong>${ativas}</strong> analisando agora${comFalta ? `, <strong style="color:var(--dn)">${comFalta}</strong> com alguém sem EPI` : ''}.</p>
         <div class="chips" style="margin-top:10px">${cams.slice(0, 6).map(c => `<span class="chip ${(c.faltando || []).length ? 'alerta' : c.ativa ? 'bom' : ''}">${ic('camera')}${esc(nomeCam(c))}</span>`).join('')}</div>`
      : `<p class="texto">Nenhuma câmera neste servidor ainda. Adicione pelo painel: “Câmeras” &gt; “+ Câmera” ou “Rede”.</p>`}
  </section>`;
}

function desde(ts) {
  if (!ts) return '';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return 'agora';
  if (s < 3600) return `há ${Math.round(s / 60)} min`;
  if (s < 86400) return `há ${Math.round(s / 3600)} h`;
  return `há ${Math.round(s / 86400)} dia(s)`;
}

function nomeCam(c) {
  const f = String(c.fonte || '');
  if (/^remote_cam_|^cam_/.test(c.id) || f === 'external' || f === 'None') return 'Celular / navegador';
  if (/^\d+$/.test(f)) return 'Webcam ' + f;
  const m = f.match(/^\w+:\/\/([^/:]+)/);
  return m ? m[1] : (f || c.id);
}

function paginaInicio() {
  const faixa = [];
  if (E.atualizacao) {
    const a = E.atualizacao;
    faixa.push(`<p class="faixa">${ic('download')}Versão ${esc(a.versao)} disponível.${a.erro ? ' ' + esc(a.erro) : ''}
      <button type="button" class="btn btn-primary btn-sm" data-acao="atualizar" ${a.baixando ? 'disabled' : ''}>${a.baixando ? `Baixando ${a.pct}%` : `${ic('download')}Atualizar`}</button></p>`);
  }
  if (E.banco.estado === 'erro') faixa.push(`<p class="faixa erro">${ic('alert')}Banco de dados: ${esc(E.banco.erro)}</p>`);
  return cabeca('home', 'Início') + faixa.join('') +
    `<div class="grade2">${blocoConta(E.resumo)}${blocoServidor()}</div>` + blocoCamerasResumo();
}

/* câmeras: atualiza no lugar (as miniaturas não piscam) */
function paginaCameras() {
  return cabeca('camera', 'Câmeras', `<span class="contador" id="cams-n">0</span>`,
    `<button type="button" class="btn btn-gold" data-acao="painel">${ic('globe')}<span class="btn-rotulo">Adicionar pelo painel</span></button>`) +
    `<div id="cams-area"></div>`;
}
let quadroN = 0;
function atualizarCameras() {
  const area = $('#cams-area');
  if (!area) return;
  const cams = (E && E.resumo && E.resumo.cameras) || [];
  $('#cams-n').textContent = cams.length;
  if (!cams.length) {
    if (!area.querySelector('.vazio')) {
      area.innerHTML = `<div class="vazio"><span class="vazio-ico">${ic('camera')}</span><h2>${E && E.servidor.estado === 'ligado' ? 'Nenhuma câmera aqui' : 'Servidor desligado'}</h2>
        <p>As câmeras são adicionadas pelo painel do Argos EPI. As que ficarem neste servidor aparecem aqui, ao vivo.</p>
        <button type="button" class="btn btn-gold" data-acao="painel">${ic('globe')}Abrir o painel</button></div>`;
    }
    return;
  }
  let grade = area.querySelector('.cams');
  if (!grade) { area.innerHTML = '<ul class="cams" aria-label="Câmeras deste servidor"></ul>'; grade = area.querySelector('.cams'); }
  const ids = new Set(cams.map(c => c.id));
  grade.querySelectorAll('li[data-sid]').forEach(li => { if (!ids.has(li.dataset.sid)) li.remove(); });
  quadroN++;
  for (const c of cams) {
    let li = grade.querySelector(`li[data-sid="${CSS.escape(c.id)}"]`);
    if (!li) {
      li = document.createElement('li');
      li.dataset.sid = c.id;
      li.innerHTML = `<article class="cam"><div class="cam-video">${ic('video-off')}<img alt="" hidden><span class="cam-selo"></span></div>
        <div class="cam-corpo"><p class="cam-nome"></p><p class="cam-fonte"></p><div class="chips"></div></div></article>`;
      grade.appendChild(li);
      const img = li.querySelector('img');
      img.onload = () => { img.hidden = false; };
      img.onerror = () => { img.hidden = true; };
    }
    const falta = c.faltando || [];
    const selo = li.querySelector('.cam-selo');
    selo.className = 'cam-selo ' + (falta.length ? 'perigo' : c.ativa ? 'ok' : '');
    selo.textContent = !c.ativa ? 'Parada' : falta.length ? 'Sem EPI' : c.status === 'sem_sinal' ? 'Sem sinal' : 'Analisando';
    li.querySelector('.cam-nome').textContent = nomeCam(c);
    li.querySelector('.cam-fonte').textContent = c.fonte && !['None', 'external'].includes(c.fonte) ? c.fonte : 'link de celular · ' + c.id;
    li.querySelector('.chips').innerHTML =
      `<span class="chip">${ic('users')}${c.pessoas} pessoa(s)</span>` +
      (c.fps != null ? `<span class="chip">${ic('zap')}${Number(c.fps).toFixed(1)} fps</span>` : '') +
      (c.modelo ? `<span class="chip">${ic('chip')}${esc(c.modelo)}${c.motor && c.motor !== 'pytorch' ? ' · ' + esc(c.motor) : ''}</span>` : '') +
      falta.map(f => `<span class="chip alerta">${ic('helmet')}${esc(f)}</span>`).join('');
    if (c.ativa && quadroN % 2 === 0 || !li.querySelector('img').src) {
      li.querySelector('img').src = `/api/quadro/${encodeURIComponent(c.id)}?t=${encodeURIComponent(TOKEN)}&n=${quadroN}`;
    }
  }
}

/* registro */
let regUltimo = 0, regFiltro = 'tudo', regSeguir = true;
function paginaRegistro() {
  regUltimo = 0;
  const opc = [['tudo', 'Tudo'], ['servidor', 'Servidor'], ['banco', 'Banco'], ['app', 'Programa']];
  return cabeca('list', 'Registro', '', `<button type="button" class="btn btn-claro" data-acao="pasta_logs">${ic('folder')}<span class="btn-rotulo">Abrir pasta</span></button>
      <button type="button" class="btn btn-claro" id="reg-copiar">${ic('copy')}<span class="btn-rotulo">Copiar</span></button>`) +
    `<section class="bloco"><div class="registro-barra"><fieldset class="seg"><legend class="sr">Mostrar</legend>
      ${opc.map(([v, t]) => `<label class="seg-btn ${regFiltro === v ? 'ativo' : ''}"><input class="sr" type="radio" name="regf" value="${v}" ${regFiltro === v ? 'checked' : ''}>${t}</label>`).join('')}
      </fieldset><label class="check" style="margin-left:auto"><input type="checkbox" id="reg-seguir" ${regSeguir ? 'checked' : ''}>Acompanhar o final</label></div>
      <div class="registro" id="registro" tabindex="0" aria-label="Mensagens do servidor"></div></section>`;
}
async function atualizarRegistro() {
  const caixa = $('#registro');
  if (!caixa) return;
  try {
    const d = await api('/api/registro?desde=' + regUltimo);
    const frag = document.createDocumentFragment();
    for (const [n, ts, origem, texto] of d.linhas) {
      regUltimo = n;
      const div = document.createElement('div');
      div.dataset.o = origem;
      div.className = 'l-' + origem + (/erro|error|traceback|falh|exception/i.test(texto) ? ' l-erro' : '');
      if (regFiltro !== 'tudo' && origem !== regFiltro) div.hidden = true;
      const h = new Date(ts * 1000);
      div.innerHTML = `<span class="l-hora">${h.toLocaleTimeString('pt-BR')}</span>${esc(texto)}`;
      frag.appendChild(div);
    }
    caixa.appendChild(frag);
    while (caixa.childElementCount > 3000) caixa.firstElementChild.remove();
    if (regSeguir) caixa.scrollTop = caixa.scrollHeight;
  } catch { /* sem resposta: tenta no proximo ciclo */ }
}

/* ajustes */
// Placa de video ou processador: guardado no proprio servidor (o site tambem troca, em Ajustes > Servidores)
const NOME_PLACA = { nvidia: 'NVIDIA', dml: 'AMD/Intel' };
function blocoHardware() {
  const r = E.resumo || {};
  const h = r.hardware;
  const ligado = E.servidor.estado === 'ligado' && h;
  const v = (h && h.dispositivo) || 'auto';
  const gpu = h ? h.gpu : '';
  const nome = (h && h.gpu_nome) || NOME_PLACA[gpu] || '';
  const opc = (valor, txt, icone, off, dica) => `<label class="seg-btn ${v === valor ? 'ativo' : ''} ${off ? 'off' : ''}" title="${esc(dica)}"><input class="sr" type="radio" name="dispositivo" value="${valor}" ${v === valor ? 'checked' : ''} ${off ? 'disabled' : ''}>${ic(icone)}${txt}</label>`;
  let dica;
  if (!ligado) dica = 'Ligue o servidor para escolher.';
  else if (!gpu) dica = 'Sem placa de vídeo compatível: as câmeras rodam no processador.';
  else if (gpu === 'nvidia' && h.rosto_na_gpu === false) dica = `${nome}: câmeras na placa; o reconhecimento de rosto ainda roda no processador.`;
  else dica = `${nome} · em uso agora: ${h.em_uso === 'gpu' ? 'placa de vídeo' : 'processador'}. As câmeras ligadas recarregam sozinhas ao trocar.`;
  return `<section class="bloco"><h2 class="sr">Servidor</h2><ul class="pref-lista">
      <li><span class="pref-ico">${ic('chip')}</span><div class="pref-txt"><p class="pref-nome">Processar câmeras com</p>
          <p class="pref-dica">${esc(dica)}</p></div>
        <fieldset class="seg" ${ligado ? '' : 'disabled'}><legend class="sr">Processar câmeras com</legend>${opc('auto', 'Automático', 'zap', !ligado, 'Placa de vídeo se houver, senão o processador')}${opc('gpu', 'Placa de vídeo', 'chip', !ligado || !gpu, nome || 'Nenhuma placa compatível')}${opc('cpu', 'Processador', 'gauge', !ligado, 'Deixa a placa de vídeo livre')}</fieldset>
      </li>
    </ul></section>`;
}
function paginaAjustes() {
  const c = E.config, a = E.app;
  const win = a.sistema === 'windows';
  const sistema = win ? 'o Windows' : 'o computador';
  const radio = (nome, valor, txt, icone) => `<label class="seg-btn ${c[nome] === valor ? 'ativo' : ''}"><input class="sr" type="radio" name="${nome}" value="${valor}" ${c[nome] === valor ? 'checked' : ''}>${icone ? ic(icone) : ''}${txt}</label>`;
  return cabeca('gear', 'Ajustes') + `
    <section class="bloco"><h2 class="sr">Programa</h2><ul class="pref-lista">
      <li><span class="pref-ico">${ic('power')}</span><div class="pref-txt"><p class="pref-nome">Iniciar com ${sistema}</p>
          <p class="pref-dica">O servidor liga sozinho quando o computador ligar, sem ninguém abrir o programa.</p></div>
        <label class="switch"><input type="checkbox" role="switch" data-cfg="iniciar_com_sistema" ${c.iniciar_com_sistema ? 'checked' : ''} aria-label="Iniciar com ${sistema}"><span class="switch-trilho"></span><span class="switch-bolha"></span></label>
        ${c.iniciar_com_sistema ? `<div class="pref-sub"><fieldset class="seg"><legend class="sr">Como abrir</legend>
          <label class="seg-btn ${c.iniciar_minimizado ? 'ativo' : ''}"><input class="sr" type="radio" name="iniciar_minimizado" value="1" ${c.iniciar_minimizado ? 'checked' : ''}>${ic('tray')}Em segundo plano</label>
          <label class="seg-btn ${!c.iniciar_minimizado ? 'ativo' : ''}"><input class="sr" type="radio" name="iniciar_minimizado" value="0" ${!c.iniciar_minimizado ? 'checked' : ''}>${ic('monitor')}Com a janela aberta</label>
        </fieldset></div>` : ''}
      </li>
      <li><span class="pref-ico">${ic('tray')}</span><div class="pref-txt"><p class="pref-nome">Ao fechar a janela (X)</p>
          <p class="pref-dica">Em segundo plano o servidor continua e o ícone fica perto do relógio. Para fechar de vez, use “Sair” no ícone ou aqui embaixo.</p></div>
        <fieldset class="seg"><legend class="sr">Ao fechar a janela</legend>${radio('ao_fechar', 'perguntar', 'Perguntar')}${radio('ao_fechar', 'bandeja', 'Segundo plano')}${radio('ao_fechar', 'sair', 'Fechar o servidor')}</fieldset>
      </li>
      <li><span class="pref-ico">${ic('sun')}</span><div class="pref-txt"><p class="pref-nome">Tema</p></div>
        <fieldset class="seg"><legend class="sr">Tema</legend>${radio('tema', 'sistema', 'Auto', 'monitor')}${radio('tema', 'claro', 'Claro', 'sun')}${radio('tema', 'escuro', 'Escuro', 'moon')}</fieldset>
      </li>
      <li><span class="pref-ico">${ic('wifi')}</span><div class="pref-txt"><p class="pref-nome">Aceitar conexões da rede local</p>
          <p class="pref-dica">Desligado, só este computador e o endereço seguro do site falam com o servidor, e ${win ? 'o Windows não pede permissão de firewall (administrador)' : 'nenhuma porta fica aberta na rede'}. Ligue para outro servidor Argos da mesma rede dividir o trabalho das câmeras com este.</p></div>
        <label class="switch"><input type="checkbox" role="switch" data-cfg="rede_local" ${c.rede_local ? 'checked' : ''} aria-label="Aceitar conexões da rede local"><span class="switch-trilho"></span><span class="switch-bolha"></span></label>
      </li>
    </ul></section>
    ${blocoHardware()}
    <section class="bloco"><header class="bloco-titulo">${ic('server')}<h2>Sobre</h2></header>
      <ul class="lista-info">
        <li>${ic('hash')}<span>Versão ${esc(a.versao)}</span></li>
        <li>${ic('chip')}<span>${esc(VARIANTES[a.variante] || 'Instalação manual (sem variante)')}</span></li>
        <li>${ic('folder')}<span title="${esc(a.pasta)}">${esc(a.pasta)}</span></li>
      </ul>
      <div class="acoes">
        <button type="button" class="btn btn-secondary btn-sm" data-acao="pasta_dados">${ic('folder')}Pasta de dados</button>
        <button type="button" class="btn btn-secondary btn-sm" data-acao="conferir_atualizacao">${ic('refresh')}Procurar atualização</button>
        <button type="button" class="btn btn-danger btn-sm" data-sair>${ic('power')}Sair e desligar o servidor</button>
      </div>
    </section>`;
}

/* ── desenhar ─────────────────────────────────────────────── */
const PAGINAS = { inicio: paginaInicio, cameras: paginaCameras, registro: paginaRegistro, ajustes: paginaAjustes };
function paginaDoHash() { const h = location.hash.slice(1); return PAGINAS[h] ? h : 'inicio'; }

function assinatura() {
  if (!E) return '';
  if (pagina === 'inicio') {
    const r = E.resumo;
    return JSON.stringify([E.servidor, E.banco.estado, E.banco.erro, E.modelos, E.app.etapa, E.atualizacao,
      r && [r.vinculado, r.conta, r.servidor, r.codigo, r.erro, r.url_local, r.cloudflare_url, r.tunel, r.pares, r.tipo,
        (r.ajuda || []).map(p => [p.nome, p.disponivel, (p.usados.rosto || 0) + (p.usados.epi || 0) > 0]), r.ajudou && Math.floor(((r.ajudou.rosto || 0) + (r.ajudou.epi || 0)) / 500),
        r.maquina, r.disponivel, (r.cameras || []).map(c => [c.id, c.ativa, (c.faltando || []).length])],
      Math.floor(Date.now() / 30000)]);
  }
  if (pagina === 'ajustes') return JSON.stringify([E.config, E.app.versao, E.app.variante, E.servidor.estado, E.resumo && E.resumo.hardware]);
  return pagina;   // cameras e registro atualizam no lugar
}

function desenhar(forcar) {
  const p = paginaDoHash();
  if (p !== pagina) { pagina = p; forcar = true; }
  document.querySelectorAll('.rail-item').forEach(a => {
    const at = a.dataset.pag === pagina;
    a.classList.toggle('ativo', at);
    if (at) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  atualizarTopo();
  if (!E) return;
  const ass = assinatura();
  if (forcar || ass !== ultimaAssinatura) {
    ultimaAssinatura = ass;
    const main = $('#main');
    const foco = document.activeElement && document.activeElement.closest('#main') ? document.activeElement : null;
    const chaveFoco = foco && (foco.dataset.acao || foco.dataset.cfg || (foco.name ? foco.name + '=' + foco.value : ''));
    const rolagem = main.scrollTop;
    main.innerHTML = PAGINAS[pagina]();
    main.scrollTop = rolagem;
    if (chaveFoco) {
      const de = main.querySelector(`[data-acao="${chaveFoco}"],[data-cfg="${chaveFoco}"]`) ||
        (chaveFoco.includes('=') ? main.querySelector(`input[name="${chaveFoco.split('=')[0]}"][value="${chaveFoco.split('=')[1]}"]`) : null);
      if (de) de.focus();
    }
  }
  if (pagina === 'cameras') atualizarCameras();
  if (pagina === 'registro') atualizarRegistro();
}

async function ciclo() {
  try {
    E = await api('/api/estado');
    conectado = true;
    aplicarTema(E.config.tema);
    if (host) host.enviar({ tipo: 'config', ao_fechar: E.config.ao_fechar, saindo: E.app.saindo, cor: corDaBarra() });
  } catch { conectado = false; }
  desenhar(false);
}

/* ── eventos ──────────────────────────────────────────────── */
document.addEventListener('click', (ev) => {
  const b = ev.target.closest('[data-acao]');
  if (b) {
    ev.preventDefault();
    const nome = b.dataset.acao;
    acao(nome, b.dataset.url ? { url: b.dataset.url } : {});
    const msgs = { ligar: 'Ligando o servidor...', desligar: 'Desligando o servidor...', reiniciar: 'Reiniciando...',
      vincular: 'Abrindo o site para vincular...', painel: 'Abrindo o painel no navegador...', conferir_atualizacao: 'Procurando atualização...' };
    if (msgs[nome]) aviso(msgs[nome]);
    setTimeout(ciclo, 400);
    return;
  }
  if (ev.target.closest('[data-sair]')) { ev.preventDefault(); sair(); return; }
  if (ev.target.closest('[data-desvincular-abrir]')) { ev.preventDefault(); perguntarDesvincular(); return; }
  if (ev.target.closest('#reg-copiar')) {
    const txt = [...document.querySelectorAll('#registro > div:not([hidden])')].map(d => d.textContent).join('\n');
    navigator.clipboard.writeText(txt).then(() => aviso('Registro copiado.'), () => aviso('Não consegui copiar.'));
  }
});

document.addEventListener('change', async (ev) => {
  const t = ev.target;
  if (t.id === 'reg-seguir') { regSeguir = t.checked; return; }
  if (t.name === 'regf') {
    regFiltro = t.value;
    document.querySelectorAll('.registro-barra .seg-btn').forEach(l => l.classList.toggle('ativo', l.querySelector('input').checked));
    document.querySelectorAll('#registro > div').forEach(d => { d.hidden = regFiltro !== 'tudo' && d.dataset.o !== regFiltro; });
    return;
  }
  if (t.name === 'dispositivo') {
    try {
      const hw = await api('/api/hardware', { dispositivo: t.value });
      if (E.resumo) E.resumo.hardware = hw;
      aviso(t.value === 'cpu' ? 'Processando só no processador.' : t.value === 'gpu' ? 'Processando na placa de vídeo.' : 'Escolha automática.');
    } catch { aviso('Não consegui trocar: o servidor está ligado?'); }
    desenhar(true);
    return;
  }
  let mud = null;
  if (t.dataset.cfg) mud = { [t.dataset.cfg]: t.checked };
  else if (t.name === 'iniciar_minimizado') mud = { iniciar_minimizado: t.value === '1' };
  else if (t.name === 'ao_fechar' || t.name === 'tema') mud = { [t.name]: t.value };
  if (!mud) return;
  try {
    const d = await api('/api/config', mud);
    E.config = d.config;
    aplicarTema(E.config.tema);
    if (host) host.enviar({ tipo: 'config', ao_fechar: E.config.ao_fechar, cor: corDaBarra() });
    aviso(mud.rede_local !== undefined ? 'Ajuste salvo. Reiniciando o servidor...' : 'Ajuste salvo.');
    desenhar(true);
  } catch { aviso('Não consegui salvar o ajuste.'); }
});

window.addEventListener('hashchange', () => desenhar(true));

async function sair() {
  aviso('Desligando o servidor e o banco...');
  await acao('sair');
  if (host) host.enviar({ tipo: 'saindo' });
}

/* pergunta do X (a janela pede; aqui a pessoa escolhe) */
const dlg = $('#dlg-fechar');
function perguntarFechar() {
  if (dlg.open) return;
  $('#dlg-fechar-lembrar').checked = false;
  dlg.showModal();
}
dlg.addEventListener('click', async (ev) => {
  const b = ev.target.closest('[data-fechar]');
  if (!b) { if (ev.target === dlg) dlg.close(); return; }
  const escolha = b.dataset.fechar;
  dlg.close();
  if (escolha === 'cancelar') return;
  if ($('#dlg-fechar-lembrar').checked) {
    try { const d = await api('/api/config', { ao_fechar: escolha }); E.config = d.config; } catch { /* segue */ }
  }
  if (escolha === 'sair') sair();
  else if (host) host.enviar({ tipo: 'esconder' });
});

/* desvincular da conta: so depois de confirmar */
const dlgDesv = $('#dlg-desvincular');
function perguntarDesvincular() {
  if (dlgDesv.open) return;
  const c = (E && E.resumo && E.resumo.conta) || {};
  const cams = ((E && E.resumo && E.resumo.cameras) || []).filter(x => x.ativa).length;
  $('#dlg-desvincular-txt').textContent =
    `O servidor sai da conta${c.nome || c.email ? ' de ' + (c.nome || c.email) : ''}` +
    (cams ? ` e ${cams === 1 ? 'a câmera ligada nele para' : 'as ' + cams + ' câmeras ligadas nele param'}` : '') +
    '. Os funcionários, EPIs e o histórico continuam guardados neste computador. Depois é só vincular de novo, na mesma conta ou em outra.';
  dlgDesv.showModal();
}
dlgDesv.addEventListener('click', async (ev) => {
  const b = ev.target.closest('[data-desvincular]');
  if (!b) { if (ev.target === dlgDesv) dlgDesv.close(); return; }
  dlgDesv.close();
  if (b.dataset.desvincular !== 'sim') return;
  aviso('Desvinculando...');
  try {
    await api('/api/acao', { acao: 'desvincular' });
    aviso('Servidor desvinculado da conta.');
  } catch { aviso('Não consegui desvincular: o servidor está ligado?'); }
  ciclo();
});

if (host) host.ouvir((m) => {
  if (!m) return;
  if (m.tipo === 'pedir_fechar') perguntarFechar();
  if (m.tipo === 'ir') location.hash = m.pagina || 'inicio';
  if (m.tipo === 'janela_estado') janelaMaximizada(m.maximizada);
});
if (host) host.enviar({ tipo: 'janela', acao: 'estado' });

hidratarIcones();
desenhar(true);
ciclo();
setInterval(ciclo, 1500);
