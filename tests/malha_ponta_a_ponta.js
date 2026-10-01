// Teste de ponta a ponta da malha: cria uma conta de teste na API, vincula dois
// servidores a ela, confere o isolamento entre contas e a sincronizacao nos dois
// sentidos (com fotos), e por fim desvincula o B.
//
//   1. API local:  node tests/servidor_api_local.js 3999   (com SUPABASE_URL, SUPABASE_SECRET_KEY
//                  e ARGOS_CHAVE_PRIVADA no ambiente)
//   2. Dois servidores com bancos separados, ex.: DATABASE_URL=.../argos_teste_a ARGOS_PORTA=8091
//      ARGOS_API_URL=http://localhost:3999/api ARGOS_TUNEL=0 ARGOS_ABRIR_NAVEGADOR=0 python run.py
//      (e o mesmo para o B, outro banco e ARGOS_PORTA=8097)
//   3. node tests/malha_ponta_a_ponta.js
// Depois apague as contas de teste (e-mail teste.malha.*) no Supabase.
const fs = require('fs');
const path = require('path');
const API = process.env.ARGOS_API || 'http://localhost:3999/api';
const A = process.env.SERVIDOR_A || 'http://localhost:8091', B = process.env.SERVIDOR_B || 'http://localhost:8097';
const FOTO = path.join(__dirname, '..', 'frontend', 'og-argos.png');
const dormir = (ms) => new Promise((r) => setTimeout(r, ms));
let falhas = 0;
const ok = (cond, msg) => { console.log((cond ? '  ok  ' : ' FALHA ') + msg); if (!cond) falhas++; };

async function req(url, { token, method = 'GET', body, headers = {}, form } = {}) {
  const h = { ...headers };
  if (token) h.Authorization = 'Bearer ' + token;
  if (body !== undefined) h['Content-Type'] = 'application/json';
  const r = await fetch(url, { method, headers: h, body: form || (body === undefined ? undefined : JSON.stringify(body)), redirect: 'manual' });
  const t = await r.text();
  let d; try { d = JSON.parse(t); } catch { d = t; }
  return { status: r.status, d, headers: r.headers };
}

async function esperar(fn, msg, ms = 90000) {
  const fim = Date.now() + ms;
  while (Date.now() < fim) { try { if (await fn()) return true; } catch {} await dormir(2000); }
  ok(false, msg + ' (tempo esgotado)');
  return false;
}

(async () => {
  const sufixo = Date.now().toString().slice(-8);
  const email = `teste.malha.${sufixo}@exemplo.com`;
  const cad = await req(API + '/auth/cadastro', { method: 'POST', body: { nome: 'Teste Malha', doc: '9' + sufixo.padStart(10, '0'), email, senha: 'segredo123' } });
  ok(cad.status === 200, 'cadastro da conta de teste ' + cad.status);
  const token = cad.d.token, contaId = cad.d.user.id;
  const outro = await req(API + '/auth/cadastro', { method: 'POST', body: { nome: 'Outra Conta', doc: '8' + sufixo.padStart(10, '0'), email: 'outra.' + email, senha: 'segredo123' } });
  const tokenOutro = outro.d.token;

  // ── vinculo dos dois servidores ──
  for (const [nome, base] of [['A', A], ['B', B]]) {
    const p = await req(base + '/pareamento');
    ok(p.status === 200 && !p.d.vinculado && p.d.codigo, `${nome}: pede vinculo com codigo ${p.d.codigo}`);
    const pag = await req(base + '/parear');
    ok(pag.status === 302 && String(pag.headers.get('location')).includes(p.d.codigo), `${nome}: /parear leva ao site com o codigo`);
    const cf = await req(base + '/parear', { headers: { 'Cf-Ray': 'x' } });
    ok(cf.status === 403, `${nome}: /parear pelo tunel e recusado`);
    const simples = await req(base + '/pareamento?simples=1');
    ok(simples.d === 'nao_vinculado', `${nome}: /pareamento?simples=1 = nao_vinculado`);
    const ver = await req(API + '/parear/' + p.d.codigo, { token });
    ok(ver.status === 200, `${nome}: site mostra o pedido (${ver.d.nome})`);
    const ap = await req(API + '/parear/' + p.d.codigo + '/aprovar', { token, method: 'POST', body: { nome: 'Servidor ' + nome } });
    ok(ap.status === 200, `${nome}: aprovado`);
  }
  for (const [nome, base] of [['A', A], ['B', B]]) {
    await esperar(async () => (await req(base + '/pareamento')).d.vinculado, `${nome}: vinculado`);
    ok((await req(base + '/pareamento?simples=1')).d === 'vinculado', `${nome}: vinculado`);
  }
  const lista = await req(API + '/servidores', { token });
  ok(lista.d.servidores.length === 2, 'conta tem 2 servidores');
  const idA = lista.d.servidores.find((s) => s.nome === 'Servidor A').id;
  const idB = lista.d.servidores.find((s) => s.nome === 'Servidor B').id;
  const stA = await req(A + '/status', { token });
  ok(stA.d.servidor_id === idA && stA.d.vinculado, 'A /status mostra servidor_id e vinculado');

  // ── autenticacao ──
  const me = await req(A + '/auth/me', { token });
  ok(me.status === 200 && me.d.user.id === contaId, 'token do site entra no servidor A');
  ok((await req(B + '/auth/me', { token: tokenOutro })).status === 403, 'outra conta e barrada no servidor B (403)');
  ok((await req(A + '/auth/me', { token: token.slice(0, -3) + 'abc' })).status === 401, 'token adulterado e recusado');
  ok((await req(A + '/auth/login', { method: 'POST', body: { credencial: email, senha: 'segredo123' } })).status === 410, 'login local desativado (410)');
  ok((await req(A + '/malha/mudancas?desde=0')).status === 401, 'malha sem credencial: 401');
  ok((await req(A + '/malha/mudancas?desde=0', { token })).status === 401, 'malha com token de conta (nao de servidor): 401');

  // ── dados criados em A chegam em B ──
  const epi = await req(A + '/epis', { token, method: 'POST', body: { nome: 'Luva teste', descricao: 'malha' } });
  ok(epi.status < 300, 'EPI criado em A');
  const epiId = (epi.d.epi || epi.d).id;
  const fd = new FormData();
  fd.append('foto', new Blob([fs.readFileSync(FOTO)], { type: 'image/png' }), 'luva.png');
  const foto = await req(A + `/epis/${epiId}/fotos`, { token, method: 'POST', form: fd });
  ok(foto.status === 200, 'foto do EPI enviada em A');
  const func = await req(A + '/funcionarios', { token, method: 'POST', body: { nome: 'Joana Teste', cargo: 'Soldadora', matricula: '77' } });
  ok(func.status < 300, 'funcionario criado em A');
  const funcId = (func.d.funcionario || func.d).id;

  await esperar(async () => ((await req(B + '/epis', { token })).d.epis || []).some((e) => e.id === epiId && (e.fotos || []).length === 1),
    'EPI com foto chegou em B');
  ok(true, 'EPI e foto chegaram em B');
  const fB = ((await req(B + '/epis', { token })).d.epis || []).find((e) => e.id === epiId);
  const fotoB = await fetch(B + `/epis/foto/${fB.fotos[0].id}?token=${token}`);
  ok(fotoB.status === 200 && (await fotoB.arrayBuffer()).byteLength > 1000, 'arquivo da foto existe em B');
  await esperar(async () => ((await req(B + '/funcionarios', { token })).d.funcionarios || []).some((f) => f.id === funcId),
    'funcionario chegou em B');

  // ── area e desenho criados em B chegam em A ──
  const area = await req(B + '/areas', { token, method: 'POST', body: { nome: 'Solda', cor: '#ff0000', epis_obrigatorios: ['helmet'] } });
  ok(area.status === 201, 'area criada em B');
  const z = await req(B + `/streams/${contaId}_abc123/zonas`, { token, method: 'POST', body: { zonas: [{ id: 'z1', area_id: area.d.area.id, pontos: [[0, 0], [0.5, 0], [0.5, 0.5]] }] } });
  ok(z.status === 200, 'zona desenhada em B');
  await esperar(async () => ((await req(A + `/streams/${contaId}_abc123/zonas`, { token })).d.zonas || []).length === 1, 'zona chegou em A');
  ok(true, 'area e zona chegaram em A');

  // ── edicao e exclusao propagam ──
  await req(B + '/funcionarios/' + funcId, { token, method: 'PUT', body: { nome: 'Joana Editada', cargo: 'Soldadora' } });
  await esperar(async () => ((await req(A + '/funcionarios', { token })).d.funcionarios || []).some((f) => f.nome === 'Joana Editada'),
    'edicao em B chegou em A');
  ok(true, 'edicao em B chegou em A');
  await req(A + '/epis/' + epiId, { token, method: 'DELETE' });
  await esperar(async () => !((await req(B + '/epis', { token })).d.epis || []).some((e) => e.id === epiId), 'exclusao em A chegou em B');
  ok(true, 'exclusao em A chegou em B');

  // ── estado da malha aparece no site ──
  await dormir(65000);
  const l2 = await req(API + '/servidores', { token });
  const sA = l2.d.servidores.find((s) => s.id === idA);
  ok(sA.online && sA.url_local.includes(':8091'), 'site mostra A online com url_local');
  ok(((sA.estado.malha || {}).pares || []).length === 1, 'A informa 1 par na malha');

  // ── desvincular B ──
  const del = await req(API + '/servidores/' + idB, { token, method: 'DELETE' });
  ok(del.status === 200, 'B desvinculado no site');
  await esperar(async () => !(await req(B + '/pareamento')).d.vinculado, 'B volta a pedir vinculo', 100000);
  ok(true, 'B voltou a pedir vinculo');
  ok((await req(B + '/auth/me', { token })).status === 503, 'B sem vinculo responde 503 nao_vinculado');

  console.log('contas de teste:', email, 'e outra.' + email);
  console.log(falhas ? `\n${falhas} FALHA(S)` : '\nTUDO CERTO');
})().catch((e) => { console.error(e); process.exit(1); });
