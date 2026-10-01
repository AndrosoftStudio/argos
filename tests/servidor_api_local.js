// Roda a API da Vercel (frontend/api/argos.js) localmente, para teste:
//   SUPABASE_URL=... SUPABASE_SECRET_KEY=... ARGOS_CHAVE_PRIVADA=... node tests/servidor_api_local.js 3999
const http = require('http');
const path = require('path');
const handler = require(path.join(__dirname, '..', 'frontend', 'api', 'argos.js'));
const porta = Number(process.argv[2] || 3999);
http.createServer((req, res) => {
  if (!req.url.startsWith('/api/')) { res.statusCode = 404; return res.end(); }
  const u = new URL(req.url, 'http://x');
  u.searchParams.set('caminho', u.pathname.replace(/^\/api\//, ''));
  req.url = '/api/argos?' + u.searchParams.toString();
  handler(req, res);
}).listen(porta, () => console.log('API local em http://localhost:' + porta + '/api'));
