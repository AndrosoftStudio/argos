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

### YOLO e DETR

O detector de EPIs aceita as duas famílias da Ultralytics pelo mesmo caminho:
**YOLO** (rede convolucional com NMS, `argos_epi_v1`) e **RT-DETR**, o *Detection
Transformer* em tempo real (atenção global sobre a imagem e saída sem NMS). O
servidor descobre a arquitetura pelo próprio arquivo `.pt`
(`epi_detector.arquitetura_de`) e a tela mostra *YOLO* ou *DETR (transformer)* no
cartão do modelo e nos detalhes da câmera. Para treinar um DETR de EPIs com o
mesmo dataset:

```bash
python treinamento/treinar.py --dados D:/ArgosEPI/datasets/argos_epi_v1/data.yaml \
  --nome argos_epi_detr_v1 --arquitetura detr --exportar-para models/
```

O RT-DETR precisa de GPU para treinar (lote 8 a 640 px em 8 GB) e de mais épocas
que o YOLO. Sem GPU ele também roda, porém mais devagar: cerca de 0,55 s por imagem
num notebook, contra 0,07 s do `argos_epi_v1`.

## Rodando

Tudo sobe por Docker Compose, a partir da raiz do projeto:

```bash
cp .env.example .env      # ajuste antes de subir
docker compose --profile gpu up -d --build     # NVIDIA
docker compose --profile cpu up -d --build     # sem GPU
```

O painel fica em `http://localhost:8088`.

O serviço `db` é um PostgreSQL 16 publicado só em `127.0.0.1:5432`. Nele ficam
contas, sessões, EPIs, funcionários, embeddings de rosto, modelos, áreas, zonas,
câmeras e a auditoria. Em disco, sob `dados/`, ficam apenas os binários: fotos,
pesos `.pt` e evidências.

### Frontend na Vercel

`frontend/` também é publicado como site estático em
[argosepi.vercel.app](https://argosepi.vercel.app), pelo repositório
[argosepi-frontend](https://github.com/AndrosoftStudio/argosepi-frontend). O site
encontra o backend pelo hub, porque o túnel Cloudflare sorteia um endereço novo a
cada reinício.

> **Ordem ao mexer nos dois lados:** reconstruir o Docker primeiro, conferir que
> as rotas novas respondem **401 em vez de 404**, e só então publicar o frontend.
> `docker-compose.yml` monta `./frontend` como volume, então mudança de frontend
> vale na hora localmente — mas `backend/` é copiado para dentro da imagem.

### Contas e segurança

Cada câmera pertence a uma conta: o painel só vê, altera ou remove as câmeras
que criou e as dos celulares com link da própria conta; o celular só fala com
o próprio stream. As senhas são guardadas com PBKDF2 e sal (contas antigas são
convertidas no primeiro login), e a sessão expira após dias sem uso.

Ajustes no `.env` (todos têm padrão; veja `.env.example`):

| Variável | Padrão | Para quê |
|---|---|---|
| `ARGOS_CADASTRO_ABERTO` | `1` | `0` fecha a tela "Criar conta" (use numa empresa, depois de criar as contas) |
| `ARGOS_UPLOAD_MODELOS` | `todos` | `admin` ou `desligado`: o `.pt` enviado executa código ao ser carregado |
| `ARGOS_SESSAO_DIAS` | `30` | dias sem uso até pedir login de novo |
| `ARGOS_MAX_UPLOAD_MB` | `2048` | tamanho máximo de vídeo, modelo ou zip enviado |

No Render, defina `HUB_API_KEY` no hub **e** no `.env` dos backends: sem ela,
qualquer pessoa registra um “backend” no hub e o site passa a enviar para ele
o login de quem entra.

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

`POSTGRES_PASSWORD` no `docker-compose.yml` e no `.env.example` é um valor
padrão de desenvolvimento (`argos`), não a senha real — o banco só escuta em
`127.0.0.1`. Para expor o banco, troque a senha no `.env`.

## Gerando o fundo da marca

O fundo azul do sistema é uma malha (mesh gradient) gerada, não um gradiente de
CSS — gradiente radial sempre lê como círculo e a emenda entre dois aparece:

```bash
python scripts/gerar_fundo.py     # escreve frontend/fundo-malha.jpg
```
