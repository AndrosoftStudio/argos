// Ponte entre a interface (servidor_app/ui) e a janela: o mesmo papel do chrome.webview no Windows.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('argosJanela', {
  enviar: (m) => ipcRenderer.send('argos', m),
  ouvir: (f) => ipcRenderer.on('argos', (_e, m) => f(m)),
});
