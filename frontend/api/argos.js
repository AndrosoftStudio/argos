/*
 * API de contas do Argos EPI (funcao da Vercel, sem dependencias).
 *
 * Contas e servidores vivem no Supabase; so esta funcao fala com ele, usando a
 * secret key. O navegador e os servidores (backends) recebem tokens assinados
 * aqui com Ed25519: o backend confere a assinatura com a chave publica, sem
 * precisar perguntar nada para a internet a cada pedido.
 *
 * Variaveis de ambiente (Vercel -> Settings -> Environment Variables):
 *   SUPABASE_URL          https://<projeto>.supabase.co
 *   SUPABASE_SECRET_KEY   sb_secret_...
 *   ARGOS_CHAVE_PRIVADA   chave Ed25519 (PEM ou so a linha base64 do PEM)
 *   FIREBASE_PROJECT_ID   opcional, padrao "argos-epi" (login com o Google)
 *
 * Todas as rotas chegam aqui pelo vercel.json (/api/... -> /api/argos).
 */
const crypto = require('crypto');

const SB_URL = String(process.env.SUPABASE_URL || '').trim().replace(/\/+$/, '').replace(/\/rest\/v1$/, '');
const SB_KEY = String(process.env.SUPABASE_SECRET_KEY || '').trim();
const FIREBASE_PROJETO = String(process.env.FIREBASE_PROJECT_ID || 'argos-epi').trim();
const SITE = String(process.env.ARGOS_SITE_URL || 'https://argosepi.vercel.app').replace(/\/+$/, '');

const SESSAO_S = 7 * 86400;              // token do painel; o site renova sozinho
const PAREAMENTO_S = 15 * 60;            // codigo de vinculo do servidor
const ONLINE_S = 180;                    // servidor sem sinal ha mais que isso = offline
const ITERACOES = 600000;                // PBKDF2-SHA256, igual ao backend
const MAX_FALHAS = 8, JANELA_FALHAS_S = 15 * 60;

// ── Chaves ──────────────────────────────────────────────────────────
function carregarChavePrivada() {
  let v = String(process.env.ARGOS_CHAVE_PRIVADA || '').trim().replace(/\\n/g, '\n');
  if (!v) return null;
  if (!v.includes('BEGIN')) v = `-----BEGIN PRIVATE KEY-----\n${v.replace(/\s+/g, '')}\n-----END PRIVATE KEY-----\n`;
  try { return crypto.createPrivateKey(v); } catch { return null; }
}
const CHAVE_PRIVADA = carregarChavePrivada();
const CHAVE_PUBLICA = CHAVE_PRIVADA ? crypto.createPublicKey(CHAVE_PRIVADA) : null;

// ── Erros e respostas ───────────────────────────────────────────────
class Falha extends Error {
  constructor(status, mensagem, extra) { super(mensagem); this.status = status; this.extra = extra; }
}
const falha = (status, mensagem, extra) => { throw new Falha(status, mensagem, extra); };

// ── Tokens (JWT com EdDSA) ──────────────────────────────────────────
const b64u = (v) => Buffer.from(v).toString('base64url');
const agora = () => Math.floor(Date.now() / 1000);

function assinar(dados) {
  if (!CHAVE_PRIVADA) falha(503, 'A API ainda não foi configurada (ARGOS_CHAVE_PRIVADA).');
  const cab = b64u(JSON.stringify({ alg: 'EdDSA', typ: 'JWT' }));
  const corpo = b64u(JSON.stringify({ iat: agora(), ...dados }));
  const sig = crypto.sign(null, Buffer.from(cab + '.' + corpo), CHAVE_PRIVADA);
  return cab + '.' + corpo + '.' + b64u(sig);
}

function lerToken(token) {
  const partes = String(token || '').split('.');
  if (partes.length !== 3 || !CHAVE_PUBLICA) return null;
  try {
    const cab = JSON.parse(Buffer.from(partes[0], 'base64url'));
    if (cab.alg !== 'EdDSA') return null;
    const ok = crypto.verify(null, Buffer.from(partes[0] + '.' + partes[1]), CHAVE_PUBLICA,
      Buffer.from(partes[2], 'base64url'));
    if (!ok) return null;
    const dados = JSON.parse(Buffer.from(partes[1], 'base64url'));
    if (dados.exp && dados.exp < agora()) return null;
    return dados;
  } catch { return null; }
}

function tokenDoPedido(req) {
  const h = String(req.headers.authorization || '');
  return h.startsWith('Bearer ') ? h.slice(7).trim() : '';
}

// ── Supabase (PostgREST) ────────────────────────────────────────────
async function sb(metodo, caminho, corpo, prefer) {
  if (!SB_URL || !SB_KEY) falha(503, 'A API ainda não foi configurada (SUPABASE_URL e SUPABASE_SECRET_KEY).');
  const headers = { apikey: SB_KEY, 'Content-Type': 'application/json' };
  if (prefer) headers.Prefer = prefer;
  const r = await fetch(SB_URL + '/rest/v1/' + caminho, {
    method: metodo, headers, body: corpo === undefined ? undefined : JSON.stringify(corpo),
  });
  const texto = await r.text();
  let dados = null;
  try { dados = texto ? JSON.parse(texto) : null; } catch { dados = texto; }
  if (!r.ok) {
    const e = new Error((dados && dados.message) || ('Supabase respondeu ' + r.status));
    e.code = dados && dados.code; e.status = r.status;
    throw e;
  }
  return dados;
}
const q = encodeURIComponent;
const umaLinha = async (caminho) => ((await sb('GET', caminho)) || [])[0] || null;

// ── Senhas (mesmo formato do backend: pbkdf2_sha256$iter$sal$hash) ──
const pbkdf2 = (senha, sal, iter) => new Promise((ok, erro) =>
  crypto.pbkdf2(String(senha), sal, iter, 32, 'sha256', (e, k) => (e ? erro(e) : ok(k))));

async function hashSenha(senha) {
  const sal = crypto.randomBytes(16);
  const chave = await pbkdf2(senha, sal, ITERACOES);
  return `pbkdf2_sha256$${ITERACOES}$${sal.toString('hex')}$${chave.toString('hex')}`;
}

async function conferirSenha(senha, guardado) {
  guardado = String(guardado || '');
  const igual = (a, b) => a.length === b.length && crypto.timingSafeEqual(Buffer.from(a), Buffer.from(b));
  if (guardado.startsWith('pbkdf2_sha256$')) {
    const [, iter, sal, chave] = guardado.split('$');
    if (!iter || !sal || !chave) return { ok: false };
    const calc = (await pbkdf2(senha, Buffer.from(sal, 'hex'), Number(iter))).toString('hex');
    const ok = igual(calc, chave);
    return { ok, regravar: ok && Number(iter) < ITERACOES };
  }
  if (!guardado) return { ok: false };
  // contas bem antigas: SHA-256 puro, sem sal (regravado no primeiro login certo)
  const ok = igual(crypto.createHash('sha256').update(String(senha)).digest('hex'), guardado);
  return { ok, regravar: ok };
}
let hashFicticio = null;
async function gastarOMesmoTempo(senha) {
  // conta inexistente demora o mesmo que senha errada: a demora nao entrega quem tem cadastro
  if (!hashFicticio) hashFicticio = await hashSenha(crypto.randomBytes(8).toString('hex'));
  await conferirSenha(senha, hashFicticio);
}

// ── Validacao ───────────────────────────────────────────────────────
const soDigitos = (v) => String(v || '').replace(/\D/g, '');
const docValido = (v) => [11, 14].includes(soDigitos(v).length);
const emailNorm = (v) => String(v || '').replace(/\s+/g, '').toLowerCase();
const emailValido = (v) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(emailNorm(v));
const texto = (v, max = 200) => String(v == null ? '' : v).trim().slice(0, max);

// ── Contas ──────────────────────────────────────────────────────────
function perfil(c) {
  return {
    id: c.id, uid: c.id, nome: c.nome, doc: c.doc || '', telefone: c.telefone || '',
    email: c.email, setor: c.setor || '', role: c.role || 'user', blocked: Boolean(c.bloqueada),
    restricoes: [], created_at: c.criado_em ? Date.parse(c.criado_em) / 1000 : null,
    tem_senha: Boolean(c.senha_hash), google: Boolean(c.google_uid),
    perfil_incompleto: !c.doc,
  };
}

function sessao(c) {
  return assinar({ tipo: 'conta', sub: c.id, nome: c.nome, email: c.email, doc: c.doc || '',
                   role: c.role || 'user', exp: agora() + SESSAO_S });
}
const respostaLogin = (c) => ({ token: sessao(c), user: perfil(c) });

async function contaPorCredencial(cred) {
  const email = emailNorm(cred);
  if (email.includes('@')) return umaLinha(`contas?select=*&email=eq.${q(email)}`);
  const doc = soDigitos(cred);
  return doc ? umaLinha(`contas?select=*&doc=eq.${q(doc)}`) : null;
}

async function exigirConta(req) {
  const d = lerToken(tokenDoPedido(req));
  if (!d || d.tipo !== 'conta') falha(401, 'Sua sessão expirou. Entre de novo.');
  const c = await umaLinha(`contas?select=*&id=eq.${q(d.sub)}`);
  if (!c) falha(401, 'Conta não encontrada. Entre de novo.');
  if (c.bloqueada) falha(403, 'Esta conta está bloqueada. Fale com o administrador.');
  return c;
}

// Tentativas de senha errada (por CPF/e-mail: atras do tunel todos tem o mesmo IP)
async function bloqueadoPorTentativas(chave) {
  const t = await umaLinha(`tentativas_login?select=*&chave=eq.${q(chave)}`);
  if (!t) return 0;
  const passou = agora() - Date.parse(t.desde) / 1000;
  if (passou > JANELA_FALHAS_S || t.falhas < MAX_FALHAS) return 0;
  return Math.ceil(JANELA_FALHAS_S - passou);
}
async function registrarFalha(chave) {
  const t = await umaLinha(`tentativas_login?select=*&chave=eq.${q(chave)}`);
  const velho = !t || agora() - Date.parse(t.desde) / 1000 > JANELA_FALHAS_S;
  await sb('POST', 'tentativas_login?on_conflict=chave',
    { chave, falhas: velho ? 1 : t.falhas + 1, desde: velho ? new Date().toISOString() : t.desde },
    'resolution=merge-duplicates,return=minimal');
}
const limparFalhas = (chave) => sb('DELETE', `tentativas_login?chave=eq.${q(chave)}`).catch(() => {});

async function entrar(req) {
  const d = req.body || {};
  const cred = texto(d.credencial || d.email || d.doc, 120);
  const senha = String(d.senha || '');
  if (!cred || !senha) falha(400, 'Preencha o CPF (ou e-mail) e a senha.');
  const chave = cred.toLowerCase();
  const espera = await bloqueadoPorTentativas(chave);
  if (espera) falha(429, `Muitas tentativas com senha errada. Tente de novo em ${Math.max(1, Math.round(espera / 60))} min.`);
  const c = await contaPorCredencial(cred);
  let conf = { ok: false };
  if (c && c.senha_hash) conf = await conferirSenha(senha, c.senha_hash);
  else await gastarOMesmoTempo(senha);
  if (!conf.ok) {
    await registrarFalha(chave);
    if (c && !c.senha_hash) falha(401, 'Esta conta entra pelo Google. Use o botão "Entrar com Google".');
    falha(401, 'CPF/e-mail ou senha incorretos.');
  }
  if (c.bloqueada) falha(403, 'Esta conta está bloqueada. Fale com o administrador.');
  await limparFalhas(chave);
  if (conf.regravar) {
    await sb('PATCH', `contas?id=eq.${q(c.id)}`, { senha_hash: await hashSenha(senha) }, 'return=minimal');
  }
  return respostaLogin(c);
}

async function cadastrar(req) {
  const d = req.body || {};
  const nome = texto(d.nome), doc = soDigitos(d.doc), email = emailNorm(d.email);
  if (!nome) falha(400, 'Preencha o nome completo.');
  if (!docValido(doc)) falha(400, 'CPF precisa ter 11 números (ou CNPJ, 14).');
  if (!emailValido(email)) falha(400, 'E-mail inválido.');
  if (String(d.senha || '').length < 6) falha(400, 'A senha precisa ter pelo menos 6 caracteres.');
  if (d.confirmar_senha && d.confirmar_senha !== d.senha) falha(400, 'As duas senhas não são iguais.');
  try {
    const [c] = await sb('POST', 'contas', {
      nome, doc, email, telefone: texto(d.telefone, 40), setor: texto(d.setor, 80),
      senha_hash: await hashSenha(d.senha),
    }, 'return=representation');
    return { message: 'Cadastro realizado!', uid: c.id, ...respostaLogin(c) };
  } catch (e) {
    if (e.code === '23505') falha(409, 'Já existe uma conta com este CPF/CNPJ ou e-mail.');
    throw e;
  }
}

// ── Google (Firebase Authentication) ────────────────────────────────
let certsGoogle = { certs: null, ate: 0 };
async function certificadosGoogle() {
  if (certsGoogle.certs && Date.now() < certsGoogle.ate) return certsGoogle.certs;
  const r = await fetch('https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com');
  if (!r.ok) falha(502, 'Não foi possível falar com o Google agora. Tente de novo.');
  const certs = await r.json();
  const m = /max-age=(\d+)/.exec(r.headers.get('cache-control') || '');
  certsGoogle = { certs, ate: Date.now() + (m ? Number(m[1]) * 1000 : 3600e3) };
  return certs;
}

async function verificarTokenFirebase(idToken) {
  const partes = String(idToken || '').split('.');
  if (partes.length !== 3) falha(401, 'Login do Google inválido.');
  let cab, dados;
  try {
    cab = JSON.parse(Buffer.from(partes[0], 'base64url'));
    dados = JSON.parse(Buffer.from(partes[1], 'base64url'));
  } catch { falha(401, 'Login do Google inválido.'); }
  if (cab.alg !== 'RS256') falha(401, 'Login do Google inválido.');
  const pem = (await certificadosGoogle())[cab.kid];
  if (!pem) falha(401, 'Login do Google vencido. Tente de novo.');
  const ok = crypto.verify('sha256', Buffer.from(partes[0] + '.' + partes[1]),
    crypto.createPublicKey(pem), Buffer.from(partes[2], 'base64url'));
  const t = agora();
  if (!ok || dados.aud !== FIREBASE_PROJETO || dados.iss !== 'https://securetoken.google.com/' + FIREBASE_PROJETO
      || !dados.sub || !(dados.exp > t) || dados.iat > t + 300) {
    falha(401, 'Login do Google inválido. Tente de novo.');
  }
  return dados;
}

async function entrarComGoogle(req) {
  const g = await verificarTokenFirebase((req.body || {}).idToken);
  const email = emailNorm(g.email);
  if (!email) falha(400, 'A conta do Google não informou o e-mail.');
  let c = await umaLinha(`contas?select=*&google_uid=eq.${q(g.sub)}`);
  if (!c) {
    const porEmail = await umaLinha(`contas?select=*&email=eq.${q(email)}`);
    if (porEmail) {
      // so liga o Google a uma conta existente se o Google confirmou o e-mail
      if (!g.email_verified) falha(409, 'Já existe uma conta com este e-mail. Entre com a senha.');
      [c] = await sb('PATCH', `contas?id=eq.${q(porEmail.id)}`, { google_uid: g.sub }, 'return=representation');
    } else {
      [c] = await sb('POST', 'contas', { nome: texto(g.name) || email.split('@')[0], email, google_uid: g.sub },
        'return=representation');
    }
  }
  if (c.bloqueada) falha(403, 'Esta conta está bloqueada. Fale com o administrador.');
  return { ...respostaLogin(c), novo: !c.doc };
}

// ── Minha conta ─────────────────────────────────────────────────────
async function atualizarPerfil(req) {
  const c = await exigirConta(req);
  const d = req.body || {};
  const mudar = {};
  if (d.nome !== undefined) { if (!texto(d.nome)) falha(400, 'Preencha o nome completo.'); mudar.nome = texto(d.nome); }
  if (d.doc !== undefined && soDigitos(d.doc) !== (c.doc || '')) {
    if (!docValido(d.doc)) falha(400, 'CPF precisa ter 11 números (ou CNPJ, 14).');
    mudar.doc = soDigitos(d.doc);
  }
  if (d.telefone !== undefined) mudar.telefone = texto(d.telefone, 40);
  if (d.setor !== undefined) mudar.setor = texto(d.setor, 80);
  mudar.atualizado_em = new Date().toISOString();
  try {
    const [nova] = await sb('PATCH', `contas?id=eq.${q(c.id)}`, mudar, 'return=representation');
    return respostaLogin(nova);
  } catch (e) {
    if (e.code === '23505') falha(409, 'Este CPF/CNPJ já está em outra conta.');
    throw e;
  }
}

async function trocarSenha(req) {
  const c = await exigirConta(req);
  const d = req.body || {};
  if (String(d.nova || '').length < 6) falha(400, 'A senha precisa ter pelo menos 6 caracteres.');
  if (c.senha_hash) {
    const conf = await conferirSenha(String(d.atual || ''), c.senha_hash);
    if (!conf.ok) falha(401, 'A senha atual não confere.');
  }
  await sb('PATCH', `contas?id=eq.${q(c.id)}`, { senha_hash: await hashSenha(d.nova) }, 'return=minimal');
  return { message: c.senha_hash ? 'Senha trocada!' : 'Senha criada! Agora também dá para entrar com CPF/e-mail.' };
}

// ── Servidores da conta ─────────────────────────────────────────────
function servidorPublico(s) {
  const visto = s.visto_em ? Date.parse(s.visto_em) / 1000 : 0;
  return {
    id: s.id, nome: s.nome, node_id: s.node_id, url: s.url, url_local: s.url_local, tipo: s.tipo,
    hardware: s.hardware || {}, estado: s.estado || {}, versao: s.versao,
    visto_em: visto || null, online: Boolean(visto) && agora() - visto < ONLINE_S,
    criado_em: s.criado_em ? Date.parse(s.criado_em) / 1000 : null,
  };
}

async function listarServidores(req) {
  const c = await exigirConta(req);
  const lista = await sb('GET', `servidores?select=*&conta_id=eq.${q(c.id)}&order=criado_em`);
  return { servidores: (lista || []).map(servidorPublico) };
}

async function servidorDaConta(req, id) {
  const c = await exigirConta(req);
  const s = await umaLinha(`servidores?select=*&id=eq.${q(id)}&conta_id=eq.${q(c.id)}`);
  if (!s) falha(404, 'Servidor não encontrado nesta conta.');
  return s;
}

async function renomearServidor(req, id) {
  const s = await servidorDaConta(req, id);
  const nome = texto((req.body || {}).nome, 60);
  if (!nome) falha(400, 'Dê um nome ao servidor.');
  const [novo] = await sb('PATCH', `servidores?id=eq.${q(s.id)}`, { nome }, 'return=representation');
  return { servidor: servidorPublico(novo) };
}

async function removerServidor(req, id) {
  const s = await servidorDaConta(req, id);
  await sb('DELETE', `servidores?id=eq.${q(s.id)}`);
  return { message: 'Servidor desvinculado. Ele pede um novo vínculo na próxima vez que ligar.' };
}

// Enderecos dos servidores online de uma conta, sem login: o celular usado como
// camera so tem o token cam_ (que vale em todos os servidores da conta, pela
// malha) e precisa achar um servidor ligado. So sai o link publico (tunel), que
// sozinho nao da acesso a nada.
async function enderecosDaConta(contaId) {
  if (!/^[0-9a-f-]{36}$/i.test(String(contaId || ''))) falha(404, 'Conta não encontrada.');
  const lista = await sb('GET', `servidores?select=*&conta_id=eq.${q(contaId)}&order=visto_em.desc`);
  const todos = (lista || []).map(servidorPublico);
  const ligados = todos.filter((s) => s.online);
  const online = ligados.filter((s) => s.url);
  // ligado mas sem link publico: o tunel nao abriu naquela rede. A pagina do celular
  // explica isso em vez de dizer so que nao achou servidor.
  const semLink = ligados.filter((s) => !s.url).map((s) => (s.estado || {}).tunel || {});
  return { servidores: online.map((s) => ({ id: s.id, url: s.url,
    availability: Number((s.estado || {}).availability || 0) })),
    vinculados: todos.length, ligados: ligados.length, sem_link: semLink.length,
    abrindo: semLink.filter((t) => t.estado === 'abrindo').length,
    motivo: texto((semLink.find((t) => t.erro) || {}).erro, 240) };
}

async function exigirServidor(req) {
  const d = lerToken(tokenDoPedido(req));
  if (!d || d.tipo !== 'servidor') falha(401, 'Credencial de servidor inválida.', { codigo: 'desvinculado' });
  const s = await umaLinha(`servidores?select=*&id=eq.${q(d.sub)}&conta_id=eq.${q(d.conta)}`);
  if (!s) falha(401, 'Este servidor foi desvinculado da conta.', { codigo: 'desvinculado' });
  return s;
}

// O proprio servidor pede para sair da conta (botao "Desvincular" na janela do programa)
async function desvincularServidor(req) {
  const s = await exigirServidor(req);
  await sb('DELETE', `servidores?id=eq.${q(s.id)}`);
  return { ok: true, message: 'Servidor desvinculado da conta.' };
}

// O servidor avisa que esta vivo, diz o endereco atual e recebe a lista dos pares da malha
async function sinalServidor(req) {
  const s = await exigirServidor(req);
  const d = req.body || {};
  const url = (v) => (/^https?:\/\/[^\s]+$/i.test(String(v || '')) ? String(v).replace(/\/+$/, '').slice(0, 300) : '');
  await sb('PATCH', `servidores?id=eq.${q(s.id)}`, {
    url: url(d.url), url_local: url(d.url_local), tipo: texto(d.tipo, 20), versao: texto(d.versao, 40),
    node_id: texto(d.node_id, 120) || s.node_id,
    hardware: (d.hardware && typeof d.hardware === 'object') ? d.hardware : {},
    estado: (d.estado && typeof d.estado === 'object') ? d.estado : {},
    visto_em: new Date().toISOString(),
  }, 'return=minimal');
  const [conta, todos] = await Promise.all([
    umaLinha(`contas?select=*&id=eq.${q(s.conta_id)}`),
    sb('GET', `servidores?select=*&conta_id=eq.${q(s.conta_id)}&order=criado_em`),
  ]);
  return {
    ok: true,
    servidor: { id: s.id, nome: s.nome },
    conta: conta ? perfil(conta) : null,
    pares: (todos || []).filter((x) => x.id !== s.id).map(servidorPublico)
      .map(({ id, nome, url, url_local, online, visto_em }) => ({ id, nome, url, url_local, online, visto_em })),
  };
}

// ── Vinculo (pareamento) de servidor ────────────────────────────────
const ALFABETO = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';   // sem 0/O e 1/I
const novoCodigo = () => {
  const b = crypto.randomBytes(8);
  const s = Array.from(b, (x) => ALFABETO[x % ALFABETO.length]).join('');
  return s.slice(0, 4) + '-' + s.slice(4);
};
const sha256 = (v) => crypto.createHash('sha256').update(String(v)).digest('hex');
const codigoNorm = (v) => {
  const s = String(v || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  return s.length === 8 ? s.slice(0, 4) + '-' + s.slice(4) : '';
};

async function iniciarPareamento(req) {
  const d = req.body || {};
  await sb('DELETE', `pareamentos?expira_em=lt.${q(new Date().toISOString())}`).catch(() => {});
  const segredo = crypto.randomBytes(32).toString('base64url');
  const codigo = novoCodigo();
  const expira = new Date(Date.now() + PAREAMENTO_S * 1000).toISOString();
  await sb('POST', 'pareamentos', {
    codigo, segredo_hash: sha256(segredo), nome: texto(d.nome, 60) || 'Servidor Argos',
    node_id: texto(d.node_id, 120), hardware: (d.hardware && typeof d.hardware === 'object') ? d.hardware : {},
    expira_em: expira,
  }, 'return=minimal');
  return { codigo, segredo, expira_em: Date.parse(expira) / 1000,
           link: `${SITE}/parear.html?codigo=${q(codigo)}`, intervalo_s: 3 };
}

async function pareamentoValido(codigo) {
  const p = await umaLinha(`pareamentos?select=*&codigo=eq.${q(codigoNorm(codigo))}`);
  if (!p || Date.parse(p.expira_em) < Date.now()) {
    falha(404, 'Código não encontrado ou vencido. Reinicie o servidor para gerar outro.');
  }
  return p;
}

async function verPareamento(req, codigo) {
  const c = await exigirConta(req);
  const p = await pareamentoValido(codigo);
  return { codigo: p.codigo, nome: p.nome, node_id: p.node_id, hardware: p.hardware || {},
           expira_em: Date.parse(p.expira_em) / 1000, aprovado: Boolean(p.aprovado_em),
           da_minha_conta: p.conta_id === c.id };
}

async function aprovarPareamento(req, codigo) {
  const c = await exigirConta(req);
  const p = await pareamentoValido(codigo);
  if (p.aprovado_em && p.conta_id !== c.id) falha(409, 'Este servidor já foi vinculado a outra conta.');
  const nome = texto((req.body || {}).nome, 60) || p.nome;
  await sb('PATCH', `pareamentos?codigo=eq.${q(p.codigo)}`,
    { conta_id: c.id, aprovado_em: new Date().toISOString(), nome }, 'return=minimal');
  return { message: `Servidor "${nome}" vinculado à sua conta.` };
}

// O servidor pergunta (com o segredo que so ele tem) se ja foi aprovado
async function concluirPareamento(req, codigo) {
  const segredo = String((req.body || {}).segredo || '');
  const p = await pareamentoValido(codigo);
  const esperado = Buffer.from(p.segredo_hash), recebido = Buffer.from(sha256(segredo));
  if (esperado.length !== recebido.length || !crypto.timingSafeEqual(esperado, recebido)) {
    falha(403, 'Segredo do pareamento não confere.');
  }
  if (!p.aprovado_em) return { pendente: true, expira_em: Date.parse(p.expira_em) / 1000 };
  // apaga o pedido antes de criar o servidor: dois pedidos ao mesmo tempo nao geram dois servidores
  const apagados = await sb('DELETE', `pareamentos?codigo=eq.${q(p.codigo)}&aprovado_em=not.is.null`, undefined,
    'return=representation');
  if (!apagados || !apagados.length) falha(409, 'Pareamento já concluído.');
  const [s] = await sb('POST', 'servidores', {
    conta_id: p.conta_id, nome: p.nome, node_id: p.node_id, hardware: p.hardware || {},
    visto_em: new Date().toISOString(),
  }, 'return=representation');
  const conta = await umaLinha(`contas?select=*&id=eq.${q(p.conta_id)}`);
  return {
    pendente: false,
    credencial: assinar({ tipo: 'servidor', sub: s.id, conta: p.conta_id }),
    servidor: { id: s.id, nome: s.nome },
    conta: conta ? perfil(conta) : null,
  };
}

// ── Roteamento ──────────────────────────────────────────────────────
async function rotear(req, caminho) {
  const m = req.method;
  const p = caminho.split('/').filter(Boolean);
  const [a, b, c] = p;
  if (a === 'saude' && m === 'GET') {
    return { ok: true, supabase: Boolean(SB_URL && SB_KEY), chave: Boolean(CHAVE_PRIVADA) };
  }
  if (a === 'chave' && m === 'GET') {
    if (!CHAVE_PUBLICA) falha(503, 'Chave não configurada.');
    return { chave_publica: CHAVE_PUBLICA.export({ type: 'spki', format: 'pem' }) };
  }
  if (a === 'auth') {
    if (b === 'entrar' && m === 'POST') return entrar(req);
    if (b === 'cadastro' && m === 'POST') return cadastrar(req);
    if (b === 'google' && m === 'POST') return entrarComGoogle(req);
    if (b === 'eu' && m === 'GET') return { user: perfil(await exigirConta(req)) };
    if (b === 'renovar' && m === 'POST') return respostaLogin(await exigirConta(req));
  }
  if (a === 'conta') {
    if (!b && m === 'PUT') return atualizarPerfil(req);
    if (b === 'senha' && m === 'POST') return trocarSenha(req);
  }
  if (a === 'enderecos' && b && !c && m === 'GET') return enderecosDaConta(b);
  if (a === 'servidores') {
    if (!b && m === 'GET') return listarServidores(req);
    if (b === 'sinal' && m === 'POST') return sinalServidor(req);
    if (b === 'desvincular' && m === 'POST') return desvincularServidor(req);
    if (b && !c && m === 'PATCH') return renomearServidor(req, b);
    if (b && !c && m === 'DELETE') return removerServidor(req, b);
  }
  if (a === 'parear') {
    if (b === 'iniciar' && m === 'POST') return iniciarPareamento(req);
    if (b && !c && m === 'GET') return verPareamento(req, b);
    if (b && c === 'aprovar' && m === 'POST') return aprovarPareamento(req, b);
    if (b && c === 'concluir' && m === 'POST') return concluirPareamento(req, b);
  }
  falha(404, 'Rota não encontrada.');
}

async function lerCorpo(req) {
  if (req.body !== undefined && req.body !== null && typeof req.body !== 'string') return req.body;
  if (typeof req.body === 'string') { try { return JSON.parse(req.body || '{}'); } catch { return {}; } }
  const pedacos = [];
  for await (const p of req) pedacos.push(p);
  try { return JSON.parse(Buffer.concat(pedacos).toString('utf8') || '{}'); } catch { return {}; }
}

module.exports = async function handler(req, res) {
  // tokens vao no cabecalho Authorization (sem cookie): liberar qualquer origem e seguro
  // e permite o painel servido pelo proprio backend (localhost:8088, tunel) usar a API
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Headers', 'Authorization, Content-Type');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, PATCH, DELETE, OPTIONS');
  res.setHeader('Cache-Control', 'no-store');
  if (req.method === 'OPTIONS') { res.statusCode = 204; return res.end(); }

  const url = new URL(req.url, 'http://x');
  const caminho = url.searchParams.get('caminho') || url.pathname.replace(/^\/api\/?/, '').replace(/^argos\/?/, '');
  let status = 200, corpo;
  try {
    req.body = await lerCorpo(req);
    corpo = await rotear(req, caminho);
  } catch (e) {
    if (e instanceof Falha) { status = e.status; corpo = { error: e.message, ...(e.extra || {}) }; }
    else { console.error('[api]', caminho, e); status = 500; corpo = { error: 'Erro no servidor de contas. Tente de novo.' }; }
  }
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.end(JSON.stringify(corpo));
};
