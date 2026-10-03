/*
 * Janela do Argos EPI Servidor no Linux (Electron). Faz o que o ArgosEPI.exe faz no Windows:
 *  - liga o supervisor (python servidor_app/supervisor.py), que cuida do banco, do backend e da interface;
 *  - mostra a interface que ele serve em 127.0.0.1 (a linha "ARGOS_UI <url>" na saida dele);
 *  - icone na bandeja; o X segue o ajuste "Ao fechar a janela" (perguntar, segundo plano ou fechar);
 *  - uma janela so: abrir de novo pelo menu so traz a que ja esta aberta;
 *  - --minimizado: liga em segundo plano (o "iniciar com o computador" usa).
 */
const { app, BrowserWindow, Tray, Menu, ipcMain, shell, nativeImage, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const { spawn } = require('child_process');

const RAIZ = process.env.ARGOS_RAIZ || path.resolve(process.resourcesPath, '..', '..');
const PYTHON = path.join(RAIZ, 'python', 'bin', 'python3');
const SUPERVISOR = path.join(RAIZ, 'servidor_app', 'supervisor.py');
const LANCADOR = path.join(RAIZ, 'argos-epi-servidor');
const ICONE = path.join(RAIZ, 'servidor_app', 'ui', 'icone-512.png');
const SITE = 'https://argosepi.vercel.app';

let janela = null, bandeja = null, sup = null, urlUi = '';
let aoFechar = 'perguntar', saindo = false, podeFechar = false;

if (!app.requestSingleInstanceLock()) { app.quit(); process.exit(0); }
app.on('second-instance', () => mostrar());
app.setName('Argos EPI Servidor');
Menu.setApplicationMenu(null);

const SPLASH = 'data:text/html;charset=utf-8,' + encodeURIComponent(`<!doctype html><meta charset="utf-8">
<body style="margin:0;height:100vh;display:grid;place-items:center;background:#0d1137;color:#e8ecf3;font:600 15px system-ui,sans-serif">
<div style="text-align:center"><div style="font:400 34px Impact,'Anton',sans-serif;letter-spacing:.04em;color:#fbc343">ARGOS EPI</div>
<p style="opacity:.8">Ligando o servidor...</p></div></body>`);

function criarJanela(visivel) {
  janela = new BrowserWindow({
    width: 1120, height: 780, minWidth: 420, minHeight: 480, show: visivel,
    backgroundColor: '#0d1137', title: 'Argos EPI Servidor', icon: ICONE, autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  janela.loadURL(urlUi || SPLASH);
  // links para fora (site, Cloudflare) abrem no navegador
  janela.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: 'deny' }; });
  janela.webContents.on('will-navigate', (ev, url) => {
    if (!url.startsWith('http://127.0.0.1:') && !url.startsWith('data:')) { ev.preventDefault(); shell.openExternal(url); }
  });
  janela.on('close', (ev) => {
    if (podeFechar) return;
    ev.preventDefault();
    if (saindo) return;
    if (aoFechar === 'sair') sairDeVez();
    else if (aoFechar === 'bandeja') janela.hide();
    else if (urlUi) { mostrar(); janela.webContents.send('argos', { tipo: 'pedir_fechar' }); }
    else janela.hide();
  });
}

function mostrar(pagina) {
  if (!janela) return;
  if (janela.isMinimized()) janela.restore();
  janela.show();
  janela.focus();
  if (pagina) janela.webContents.send('argos', { tipo: 'ir', pagina });
}

function criarBandeja() {
  try {
    bandeja = new Tray(nativeImage.createFromPath(ICONE).resize({ width: 22, height: 22 }));
    bandeja.setToolTip('Argos EPI Servidor');
    bandeja.setContextMenu(Menu.buildFromTemplate([
      { label: 'Abrir', click: () => mostrar() },
      { label: 'Abrir o painel (site)', click: () => shell.openExternal(SITE) },
      { label: 'Câmeras deste servidor', click: () => mostrar('cameras') },
      { type: 'separator' },
      { label: 'Sair e desligar o servidor', click: () => sairDeVez() },
    ]));
    bandeja.on('click', () => mostrar());
  } catch (e) {
    bandeja = null;   // sem bandeja (alguns GNOME): "segundo plano" so esconde; abrir pelo menu traz de volta
  }
}

function ligarSupervisor() {
  if (!fs.existsSync(PYTHON)) {
    dialog.showErrorBox('Argos EPI Servidor', `Arquivos do programa incompletos (falta ${PYTHON}). Instale de novo pelo site.`);
    podeFechar = true; app.quit(); return;
  }
  sup = spawn(PYTHON, ['-X', 'utf8', '-u', SUPERVISOR, '--pai', String(process.pid), '--exe', LANCADOR],
    { cwd: RAIZ, stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, PYTHONNOUSERSITE: '1' } });
  let resto = '';
  sup.stdout.on('data', (b) => {
    resto += b.toString('utf8');
    let i;
    while ((i = resto.indexOf('\n')) >= 0) {
      const linha = resto.slice(0, i).trim();
      resto = resto.slice(i + 1);
      if (linha.startsWith('ARGOS_UI ')) {
        urlUi = linha.slice(9).trim();
        if (janela) janela.loadURL(urlUi);
      }
    }
  });
  sup.stderr.on('data', () => { /* o supervisor grava tudo em dados/logs/servidor.log */ });
  sup.on('exit', (codigo) => {
    sup = null;
    if (saindo) { podeFechar = true; app.quit(); return; }
    mostrar();
    const r = dialog.showMessageBoxSync(janela, {
      type: 'error', title: 'Argos EPI Servidor', buttons: ['Ligar de novo', 'Fechar'], defaultId: 0, cancelId: 1,
      message: 'O programa do servidor parou.', detail: `Código ${codigo}. Veja o registro em dados/logs/servidor.log.`,
    });
    if (r === 0) { urlUi = ''; janela.loadURL(SPLASH); ligarSupervisor(); }
    else { podeFechar = true; app.quit(); }
  });
}

function pedirSair() {
  return new Promise((ok) => {
    if (!urlUi) return ok(false);
    const u = new URL(urlUi);
    const corpo = JSON.stringify({ acao: 'sair' });
    const req = http.request({ host: '127.0.0.1', port: u.port, path: '/api/acao', method: 'POST', timeout: 5000,
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(corpo), 'X-Argos-Token': u.searchParams.get('t') || '' } },
      (r) => { r.resume(); ok(r.statusCode === 200); });
    req.on('error', () => ok(false));
    req.on('timeout', () => { req.destroy(); ok(false); });
    req.end(corpo);
  });
}

async function sairDeVez() {
  if (saindo) return;
  saindo = true;
  if (!sup) { podeFechar = true; app.quit(); return; }
  if (!(await pedirSair())) { try { sup.kill('SIGTERM'); } catch { /* ja saiu */ } }
  // o supervisor desliga o backend e o banco; se travar, encerra na marra
  setTimeout(() => { if (sup) { try { sup.kill('SIGKILL'); } catch { /* */ } } podeFechar = true; app.quit(); }, 45000);
}

ipcMain.on('argos', (_e, m) => {
  if (!m) return;
  if (m.tipo === 'config' && m.ao_fechar) aoFechar = m.ao_fechar;
  if (m.tipo === 'esconder' && janela) janela.hide();
  if (m.tipo === 'saindo') { saindo = true; if (!sup) { podeFechar = true; app.quit(); } }
});

app.whenReady().then(() => {
  criarJanela(!process.argv.includes('--minimizado'));
  criarBandeja();
  ligarSupervisor();
});
app.on('window-all-closed', () => { /* fica na bandeja */ });
app.on('before-quit', () => { if (sup && !saindo) { saindo = true; try { sup.kill('SIGTERM'); } catch { /* */ } } });
