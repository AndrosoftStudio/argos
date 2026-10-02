# Argos EPI

Monitoramento do uso de EPIs por câmera e visão computacional.
TCC de Desenvolvimento de Sistemas — SENAI Lauro de Freitas/BA — AndrosoftStudio.

O sistema olha as câmeras em tempo real, identifica cada pessoa na cena, verifica
se ela está com os EPIs exigidos naquela área e, quando falta alguma coisa,
registra o episódio com foto e atribui ao funcionário reconhecido.

---

## O que tem aqui

| Pasta | O que é |
|---|---|
| `backend/` | Servidor Flask: streams, IA, áreas, auditoria, contas, hub |
| `frontend/` | Painel web (`index.html`) e a página do celular-câmera (`cam.html`) |
| `treinamento/` | Pipeline de dataset e treino dos modelos de EPI |
| `scripts/` | Utilidades — migração para Postgres, geração do fundo da marca |
| `docs/tcc/` | Documentação acadêmica do TCC |
| `coisas/` | Logo oficial da equipe (AJCAM Linyx) |

## Como funciona, em uma passada

1. Uma câmera entra no sistema — celular pelo link, navegador, RTSP ou a tela.
2. Cada câmera roda em uma linha de processamento própria: um detector de
   EPIs (YOLO ou DETR) + um modelo de pose que separa as pessoas e rastreia cada uma.
3. O rosto de quem aparece é comparado com a galeria de funcionários
   (SCRFD + ArcFace, por embedding — **não existe etapa de treino**).
4. Cada pessoa é cobrada pelos EPIs da **área** onde está pisando, não por uma
   lista única da câmera.
5. Falta confirmada vira um **episódio** na auditoria, com até 3 fotos de
   evidência, e entra na ficha de desempenho do funcionário.

### Regiões de interesse (ROI)

O sistema usa ROI em três níveis:

| Nível | Onde | O que faz |
| --- | --- | --- |
| Área da câmera | `areas.py`, tela *Mapear áreas* | Polígono desenhado sobre a imagem. A pessoa entra na área pelo ponto de apoio (entre os pés) e é cobrada pelos EPIs daquela área; uma área sem EPIs é *área livre*. |
| Pessoa | `EpiDetector.detect_crops` (modo *Super detalhado*) | O detector roda de novo em um recorte ampliado de cada pessoa, para achar EPIs pequenos. |
| Parte do corpo | `ppe_analyzer.body_regions` | Os pontos da pose dividem o corpo em cabeça, tronco, mãos e pés; cada EPI só conta na sua região (capacete na cabeça, bota nos pés). |

### Trabalhador encoberto: o sistema decide sozinho quando reforçar

Quando algo fica na frente do trabalhador (uma caixa, uma máquina, outra pessoa), o
modelo de pose "completa" o corpo com pontos inventados e ainda cria uma segunda cópia
da pessoa só com a parte de cima. Sem tratar isso, o EPI ficava com uma cópia e a outra
aparecia sem EPI (alarme falso). A análise agora:

1. **percebe a oclusão** (`ppe_analyzer.regioes_encobertas`): junta as cópias da mesma
   pessoa e apaga os pontos inventados; marca a parte do corpo coberta por outra pessoa
   que está na frente; e a parte que a pose estimou fora da silhueta que o modelo viu;
2. **não deduz falta onde não dá para ver**: numa parte encoberta, não achar o EPI vira
   *não visível*, e vale o último estado visto por até 12 s. A falta vista de fato
   (classe `sem_capacete`, por exemplo) continua valendo atrás do objeto;
3. **dá uma segunda olhada só em quem precisa** (`reforco.py`): encobertos e pessoas sem
   evidência de algum EPI ganham o detector no recorte ampliado delas (ROI), no máximo
   uma vez por segundo quando estão à vista. Nos encobertos, se houver um modelo Argos
   da outra arquitetura em `models/` (um DETR treinado, se o padrão é YOLO), ele dá a
   segunda opinião no mesmo recorte.

Na tela, a pessoa aparece como *encoberto* e o alarme espera. Numa cena de teste com
uma caixa na frente do tronco de um trabalhador (vídeo real, caixa desenhada), os quadros
com alarme falso caíram de 50 para 0; sem obstáculo, o custo subiu cerca de 14 ms por
quadro num notebook sem GPU.

### YOLO e DETR

O detector de EPIs aceita as duas famílias da Ultralytics pelo mesmo caminho:
**YOLO** (rede convolucional com NMS, `argos_epi_v1`) e **RT-DETR**, o *Detection
Transformer* em tempo real (atenção global sobre a imagem e saída sem NMS). O
servidor descobre a arquitetura pelo próprio arquivo `.pt`
(`epi_detector.arquitetura_de`). Quem usa o painel não escolhe modelo: o padrão é
o modelo Argos de `models/` com o maior mAP50 no teste (`melhor_modelo_argos`, lido
do `.json` do treino), seja YOLO ou DETR. Um modelo da outra arquitetura com pelo
menos 80% da nota do padrão entra como segunda opinião em trabalhador encoberto.

Para treinar o DETR de EPIs, dê dois cliques em `treinamento/treinar_detr.bat` no PC
com GPU, depois do `treinar_tudo.bat`. Ele usa o mesmo dataset do `argos_epi_v1`, então as
notas são comparáveis (ver `treinamento/README.md`). O treino leva de 1 a 2 dias numa RTX
4060 Ti. Sem GPU não compensa. Na inferência, o RT-DETR roda em cerca de 0,55 s por
imagem num notebook sem GPU, contra 0,07 s do `argos_epi_v1`.

## Rodando

Há dois jeitos de ligar o backend. Nos dois, o painel fica em `http://localhost:8088`.
Passo a passo, pasta `models` e instruções para IA em [`COMO_INSTALAR.md`](COMO_INSTALAR.md).

**Na primeira vez, o servidor pede para ser vinculado a uma conta:** o navegador abre
`http://localhost:8088/parear`, que leva ao site com um código. A pessoa entra na conta
(senha ou Google), confere o código e clica em **Vincular**. No Docker quem abre o
navegador é o `instalar_docker.bat`; no `iniciar.bat`/`iniciar.sh`, o próprio servidor.

| | Com Docker | Sem Docker (Windows) | Sem Docker (Linux/macOS) |
|---|---|---|---|
| Quando usar | PC próprio, servidor | PC sem Docker ou sem administrador (ex.: laboratório da escola) | idem, em Linux ou macOS |
| Instalar | `instalar_docker.bat` | `configurar.bat` (uma vez) | `bash configurar.sh` (uma vez) |
| Ligar | sozinho, junto com o Docker Desktop | `iniciar.bat` | `bash iniciar.sh` |
| Desligar | `parar_docker.bat` | Ctrl+C na janela do `iniciar.bat` | Ctrl+C no terminal |
| Banco | container `argosepi-db` (porta 5432) | PostgreSQL portátil em `dados\pgdata` (porta 5433) | PostgreSQL portátil em `dados/pgdata` (porta 5433) |

**Com Docker:** dois cliques em **`instalar_docker.bat`**. Ele liga o Docker Desktop, cria o
`.env`, escolhe GPU ou CPU, constrói e sobe tudo, e espera o painel responder. À mão, a partir
da raiz do projeto:

```bash
cp .env.example .env      # ajuste antes de subir
docker compose --profile gpu up -d --build     # NVIDIA
docker compose --profile cpu up -d --build     # sem GPU
```

**Sem Docker:** dois cliques em **`configurar.bat`** e depois em **`iniciar.bat`**. Não precisa
de administrador nem de Python instalado: o `configurar.bat` baixa para dentro da pasta um
Python 3.12 portátil (`python\`), o PyTorch (GPU NVIDIA ou CPU), as dependências, um
PostgreSQL 16 portátil (`bin\pgsql`) e o `cloudflared`. São cerca de 1,5 GB na primeira vez.
Depois de configurada, a pasta inteira pode ir num pendrive para outro PC Windows.

- `configurar.bat cpu` ou `configurar.bat gpu` força o modo.
- `copiar_banco_do_docker.bat` copia contas, funcionários, rostos, áreas e auditoria do
  banco do Docker para o banco portátil (as fotos já estão em `dados\`).
- `atualizar.bat` reinstala as dependências depois de mudar o `requirements.txt`.

**Linux e macOS:** `bash configurar.sh` e depois `bash iniciar.sh`, com um usuário comum (o
PostgreSQL não roda como root). Também não precisam de Docker: o `configurar.sh` baixa um
PostgreSQL 16 portátil (~15 MB, em `bin/pgsql`) e cria o banco em `dados/pgdata` (porta 5433);
o `iniciar.sh` liga e desliga esse banco junto com o servidor. Precisa só de Python 3.11+.
Para usar outro PostgreSQL, defina `DATABASE_URL` antes de rodar o `iniciar.sh`.

O banco é um PostgreSQL 16 que só aceita conexão da própria máquina. Nele ficam
contas, sessões, EPIs, funcionários, embeddings de rosto, modelos, áreas, zonas,
câmeras e a auditoria. Em disco, sob `dados/`, ficam apenas os binários: fotos,
pesos `.pt` e evidências.

### Frontend na Vercel

`frontend/` também é publicado como site estático em
[argosepi.vercel.app](https://argosepi.vercel.app), pelo repositório
[argosepi-frontend](https://github.com/AndrosoftStudio/argosepi-frontend). Junto vai a
API de contas (`frontend/api/argos.js`, uma função da Vercel): todas as rotas `/api/...`
chegam nela pelo `vercel.json`.

### Contas, servidores e malha

- **Contas** ficam no Supabase (`supabase/esquema.sql`, rodado uma vez no SQL Editor).
  Só a API da Vercel fala com ele, usando a secret key; o navegador nunca vê a tabela.
  Entra-se com CPF/e-mail e senha ou com o Google (Firebase). Quem entra pelo Google
  completa o cadastro (CPF, telefone, setor) depois, em **Ajustes → Minha conta**.
- **Tokens**: a API assina o login com uma chave Ed25519. Os servidores conferem a
  assinatura com a chave pública (embutida em `backend/conta.py`), sem ir à internet a
  cada pedido. Um servidor só atende a conta a que foi vinculado.
- **Vínculo do servidor**: na primeira vez o servidor pede um código à API, abre a
  página `/parear` (só funciona na própria máquina, nunca pelo túnel) e espera a pessoa
  aprovar no site. A credencial do servidor fica no banco local (tabela `servidor_local`).
  Desvincular em **Servidores** faz ele pedir um vínculo novo.
- **Página Servidores**: lista os servidores da conta (online, GPU/CPU, carga, câmeras,
  malha), renomeia e desvincula. O painel escolhe sozinho o servidor mais livre que
  responde; câmeras novas vão para o servidor mais livre.
- **Malha** (`backend/malha.py`): servidores da mesma conta copiam entre si EPIs, equipe
  (com os rostos), áreas e desenhos, links de celular e o histórico de faltas, com as
  fotos. A cada 20 s cada um pergunta aos outros o que mudou; vale a mudança mais nova.
  Modelos `.pt` e câmeras ligadas ficam em cada máquina.
- **Variáveis da Vercel** (Settings → Environment Variables): `SUPABASE_URL`,
  `SUPABASE_SECRET_KEY` e `ARGOS_CHAVE_PRIVADA`. Nenhuma delas vai para o GitHub nem
  para os servidores. Depois de mudar, faça um Redeploy.
- **Configuração do Firebase** (`frontend/config.js`): não é segredo. A `apiKey` do
  Firebase só identifica o projeto e vai para todo navegador que abre o site, então
  esconder não adianta. O que protege é: o Firebase só faz login nos domínios
  autorizados, e a API da Vercel confere a assinatura do token do Google (projeto
  `argos-epi`) antes de entrar na conta.
- **Contas antigas** (do banco local): `scripts/migrar_contas_supabase.py` copia para o
  Supabase mantendo o mesmo id e a mesma senha.

> **Ordem ao mexer nos dois lados:** reconstruir o Docker primeiro, conferir que
> as rotas novas respondem **401 em vez de 404**, e só então publicar o frontend.
> `docker-compose.yml` monta `./frontend` como volume, então mudança de frontend
> vale na hora localmente — mas `backend/` é copiado para dentro da imagem.

### Contas e segurança

Cada câmera pertence a uma conta: o painel só vê, altera ou remove as câmeras
que criou e as dos celulares com link da própria conta; o celular só fala com
o próprio stream. As senhas são guardadas com PBKDF2 e sal (contas antigas são
convertidas no primeiro login), e o login do painel vale 7 dias (o site renova sozinho).

Ajustes no `.env` (todos têm padrão; veja `.env.example`):

| Variável | Padrão | Para quê |
|---|---|---|
| `ARGOS_API_URL` | `https://argosepi.vercel.app/api` | API de contas (vínculo do servidor e lista dos pares da malha) |
| `ARGOS_SERVIDOR_NOME` | nome do PC | como o servidor aparece em **Servidores** |
| `ARGOS_PORTA` | `8088` | porta do painel (para ligar dois servidores no mesmo PC) |
| `ARGOS_ABRIR_NAVEGADOR` | `1` | `0` não abre o navegador para o vínculo (fora do Docker) |
| `ARGOS_TUNEL` | `1` | `0` não abre o túnel do Cloudflare (só rede local) |
| `ARGOS_FUSO` | `America/Bahia` | fuso dos gráficos de desempenho (dias) |
| `ARGOS_LOGIN_LOCAL` | `0` | `1` volta a aceitar o login antigo, guardado no servidor (teste sem internet) |
| `ARGOS_CADASTRO_ABERTO` | `1` | com `ARGOS_LOGIN_LOCAL=1`: `0` fecha o cadastro local |
| `ARGOS_UPLOAD_MODELOS` | `todos` | `admin` ou `desligado`: o `.pt` enviado executa código ao ser carregado |
| `ARGOS_SESSAO_DIAS` | `30` | dias sem uso até pedir login de novo |
| `ARGOS_MAX_UPLOAD_MB` | `2048` | tamanho máximo de vídeo, modelo ou zip enviado |

O hub do Render continua só para os links antigos de celular (`?node=`). Se usar,
defina `HUB_API_KEY` no hub **e** no `.env` dos backends. O painel não depende mais
dele: acha os servidores pela conta.

Testes das regras de segurança (não precisam de GPU nem de banco):

```bash
python -m pytest tests
```

---

## O que NÃO está neste repositório, de propósito

O `.gitignore` bloqueia, e é para continuar assim:

- **`.env`** — tem a `HUB_API_KEY` do servidor.
- **`dados/`** — vetores de rosto, fotos de evidência de funcionários flagrados
  sem EPI e vídeos gravados das câmeras. É dado pessoal e biométrico de pessoas
  reais. Não entra em repositório nenhum, nem privado: histórico de git não se
  apaga fácil.
- **`backend/users.json.migrated`** — nomes, CPF e telefone de contas reais.
- **`models/`, `weights/`, `runs/`, `*.pt`** — pesados e recriáveis pelo
  `treinamento/`.
- **`cloudflared.exe`** — baixado na construção da imagem (ver `Dockerfile`).
- **`bin/`, `python/`** — Python, uv e PostgreSQL portáteis, baixados pelo `configurar.bat`.

`POSTGRES_PASSWORD` no `docker-compose.yml` e no `.env.example` é um valor
padrão de desenvolvimento (`argos`), não a senha real — o banco só escuta em
`127.0.0.1`. Para expor o banco, troque a senha no `.env`.

## Gerando o fundo da marca

O fundo azul do sistema é uma malha (mesh gradient) gerada, não um gradiente de
CSS — gradiente radial sempre lê como círculo e a emenda entre dois aparece:

```bash
python scripts/gerar_fundo.py     # escreve frontend/fundo-malha.jpg
```

## Trocando a logo

A logo, os ícones (192, 512, apple-touch), o favicon e a prévia de link
(`og-argos.png`) saem todos da arte original com fundo branco:

```bash
python scripts/gerar_logo.py caminho/da/arte.jpeg
```

O script tira o fundo branco e grava em `frontend/`, `coisas/` e
`treinamento/site_treinamento/public/`. Os ícones usam só a cabeça do lince.
