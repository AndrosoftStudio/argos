// Uso local: defina BACKEND_URL (ex.: "http://localhost:8088").
// Na Vercel: deixe BACKEND_URL vazio para o site pedir ao hub o backend disponivel.
window.BACKEND_URL = "";
window.BACKEND_HUB_URL = "https://backend-hub-vigilanciaepi.onrender.com";
// Endereco fixo usado nos links de compartilhamento do celular-camera.
// Precisa ser um site estavel (a Vercel), porque o tunel do Cloudflare sorteia
// um endereco novo a cada reinicio e o link antigo morria. A pagina /cam.html
// pergunta ao hub onde esta o backend daquele node.
// Vazio volta ao comportamento antigo (link apontando para o tunel).
window.CAM_SHARE_BASE = "https://argosepi.vercel.app";
// Logo oficial da equipe. Vazio mostra apenas o texto ARGOS.
window.ARGOS_LOGO_URL = "logo.png";
