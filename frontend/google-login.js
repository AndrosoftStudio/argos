// Login com o Google pelo Firebase Authentication.
// Abre a janela do Google e devolve o idToken; quem confere o token e cria/acha
// a conta e a API do site (/api/auth/google). A sessao do Firebase nao fica
// guardada: o painel usa so o token do Argos.
import { initializeApp } from 'https://www.gstatic.com/firebasejs/12.19.0/firebase-app.js';
import { getAuth, GoogleAuthProvider, signInWithPopup, signOut, inMemoryPersistence, setPersistence }
  from 'https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js';

const cfg = window.ARGOS_FIREBASE;
if (cfg && cfg.apiKey) {
  const auth = getAuth(initializeApp(cfg));
  auth.languageCode = 'pt-BR';
  setPersistence(auth, inMemoryPersistence).catch(() => {});
  window.argosGoogleLogin = async () => {
    const provedor = new GoogleAuthProvider();
    provedor.setCustomParameters({ prompt: 'select_account' });
    const r = await signInWithPopup(auth, provedor);
    const idToken = await r.user.getIdToken();
    signOut(auth).catch(() => {});
    return idToken;
  };
}
