-- Argos EPI - banco de contas no Supabase
--
-- Rode uma vez no Supabase: SQL Editor -> New query -> cole tudo -> Run.
-- Pode rodar de novo sem perder nada (so cria o que falta).
--
-- Quem le e grava aqui e so a API da Vercel (argosepi.vercel.app/api), com a
-- secret key. O RLS fica ligado e sem nenhuma politica: a chave publica
-- (publishable) e o navegador nao enxergam nenhuma linha destas tabelas.

-- Contas: cadastro com CPF/e-mail e senha, ou pelo Google (Firebase).
create table if not exists public.contas (
  id            uuid primary key default gen_random_uuid(),
  nome          text not null default '',
  doc           text unique,                 -- CPF/CNPJ so com digitos; quem entrou pelo Google pode ainda nao ter
  telefone      text not null default '',
  email         text not null unique,
  setor         text not null default '',
  senha_hash    text,                        -- pbkdf2_sha256$iteracoes$sal$hash; vazio = so entra pelo Google
  google_uid    text unique,
  role          text not null default 'user',
  bloqueada     boolean not null default false,
  criado_em     timestamptz not null default now(),
  atualizado_em timestamptz not null default now()
);

-- Servidores (backends) vinculados a uma conta. Cada um processa so para a sua conta
-- e conversa com os outros da mesma conta (malha).
create table if not exists public.servidores (
  id         uuid primary key default gen_random_uuid(),
  conta_id   uuid not null references public.contas(id) on delete cascade,
  nome       text not null,
  node_id    text not null default '',
  url        text not null default '',        -- link publico (tunel do Cloudflare)
  url_local  text not null default '',        -- endereco na rede local
  tipo       text not null default '',        -- gpu / cpu
  hardware   jsonb not null default '{}'::jsonb,
  estado     jsonb not null default '{}'::jsonb,   -- disponibilidade, cameras, malha...
  versao     text not null default '',
  visto_em   timestamptz,
  criado_em  timestamptz not null default now()
);
create index if not exists ix_servidores_conta on public.servidores(conta_id);

-- Pedido de vinculo de um servidor: o servidor pede um codigo, a pessoa entra no
-- site e aprova, o servidor busca a credencial. Vale 15 minutos.
create table if not exists public.pareamentos (
  codigo        text primary key,
  segredo_hash  text not null,              -- so o servidor que pediu sabe o segredo
  nome          text not null default '',
  node_id       text not null default '',
  hardware      jsonb not null default '{}'::jsonb,
  conta_id      uuid references public.contas(id) on delete cascade,
  aprovado_em   timestamptz,
  expira_em     timestamptz not null,
  criado_em     timestamptz not null default now()
);

-- Senha errada seguida: bloqueia a credencial por alguns minutos.
create table if not exists public.tentativas_login (
  chave    text primary key,
  falhas   integer not null default 0,
  desde    timestamptz not null default now()
);

alter table public.contas           enable row level security;
alter table public.servidores       enable row level security;
alter table public.pareamentos      enable row level security;
alter table public.tentativas_login enable row level security;

-- So a API (secret key = service_role) acessa. anon e authenticated ficam de fora.
revoke all on public.contas, public.servidores, public.pareamentos, public.tentativas_login from anon, authenticated;
grant usage on schema public to service_role;
grant all on public.contas, public.servidores, public.pareamentos, public.tentativas_login to service_role;
