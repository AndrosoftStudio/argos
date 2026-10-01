// Contas e servidores: API do site (Vercel + Supabase). O painel entra pela API e
// fala direto com os servidores vinculados a conta.
window.ARGOS_API_URL = "https://argosepi.vercel.app/api";
// Servidor fixo (opcional, so para testes): vazio = usa os servidores da conta.
window.BACKEND_URL = "";
// Hub antigo: continua so para o link de celular antigo (?node=) achar o servidor.
window.BACKEND_HUB_URL = "https://backend-hub-vigilanciaepi.onrender.com";
// Endereco fixo usado nos links de compartilhamento do celular-camera.
// Precisa ser um site estavel (a Vercel), porque o tunel do Cloudflare sorteia
// um endereco novo a cada reinicio e o link antigo morria. A pagina /cam.html
// pergunta a API quais servidores da conta estao ligados.
// Vazio volta ao comportamento antigo (link apontando para o tunel).
window.CAM_SHARE_BASE = "https://argosepi.vercel.app";
// Logo oficial da equipe. Vazio mostra apenas o texto ARGOS.
window.ARGOS_LOGO_URL = "logo.png";
// Login com o Google (Firebase). Esta configuracao e publica: o Firebase so
// aceita login vindo dos dominios autorizados no console (localhost e a Vercel).
window.ARGOS_FIREBASE = {
  apiKey: "AIzaSyA5_Xwok5n2l2qmA2847IIEqLnvxQ985Ak",
  authDomain: "argos-epi.firebaseapp.com",
  projectId: "argos-epi",
  storageBucket: "argos-epi.firebasestorage.app",
  messagingSenderId: "193973334025",
  appId: "1:193973334025:web:fa789a4f679ac96aec48d8",
  measurementId: "G-0ZNH3953LY"
};
