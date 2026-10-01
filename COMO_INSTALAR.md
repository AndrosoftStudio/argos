# Argos EPI v20 — como instalar o backend no Docker

## Jeito rápido (1 clique)

1. Instale o **Docker Desktop**: https://www.docker.com/products/docker-desktop/
   (no Windows ele pede o WSL 2 na primeira vez; aceite e reinicie o PC se pedir).
2. Extraia a pasta do Argos onde quiser (ex.: `C:\ArgosEPI`).
3. Dê dois cliques em **`instalar_docker.bat`**.
4. Espere terminar. Na primeira vez demora de 10 a 30 minutos porque baixa o PyTorch.
5. Abra **http://localhost:8088** no navegador.

O `.bat` faz tudo sozinho:

- liga o Docker Desktop se ele estiver desligado;
- cria o `.env` a partir do `.env.example` (se ainda não existir);
- usa a GPU NVIDIA se tiver, senão o modo CPU (se a GPU falhar, tenta CPU);
- remove containers antigos do Argos (o banco continua no volume `argosepi-pgdata`);
- constrói a imagem, liga o banco (`argosepi-db`) e o backend (`argosepi-backend`);
- espera o servidor responder e mostra os endereços.

Para forçar o modo: `instalar_docker.bat cpu` ou `instalar_docker.bat gpu`.
Para desligar: **`parar_docker.bat`**. Depois de instalado, o Argos liga sozinho junto
com o Docker Desktop.

## O que precisa estar na pasta

| Item | Para quê | Vem do GitHub? |
|---|---|---|
| `backend/`, `frontend/`, `scripts/`, `run.py`, `Dockerfile`, `docker-compose.yml`, `requirements.txt`, `.env.example` | o sistema | sim |
| `models/argos_epi_v1.pt` (+ `.json`) | modelo treinado que detecta os EPIs | **não** (peça a pasta `models`) |
| `models/yolo26*.pt`, `models/yolo26*-pose.pt` | pessoas e pose | **não** (vêm no pacote) |
| `models/insightface/` | reconhecimento de rosto | **não** (vêm no pacote) |

A pasta `models` não fica no GitHub porque é pesada. Ela vai no pacote `.zip` enviado pelo
André. Quem clonar o repositório precisa copiar essa pasta para dentro do projeto.

## Site argosepi.vercel.app

O backend local funciona sem nada além disso. Para o **site** encontrar o seu backend,
preencha `HUB_API_KEY` no `.env` com a chave do hub (peça ao André; ela não vai no
pacote nem no GitHub) e rode o `instalar_docker.bat` de novo.

## Problemas comuns

- **"porta 5432 em uso"**: tem um PostgreSQL instalado no PC. Pare o serviço dele
  (Serviços do Windows → postgresql → Parar).
- **"porta 8088 em uso"**: outro Argos rodando fora do Docker (`iniciar.bat`). Feche-o.
- **Erro de GPU**: atualize o driver da NVIDIA ou use `instalar_docker.bat cpu`.
- **Ver o que está acontecendo**: `docker logs -f argosepi-backend` (ou `argosepi-backend-cpu`).

---

## Instruções para IA (Claude, Copilot, etc.)

Se você é uma IA ajudando a instalar o Argos EPI v20 nesta máquina Windows, siga:

1. **Código**: use a pasta atual. Se não houver, clone a `main`:
   `git clone https://github.com/AndrosoftStudio/argos.git` e entre na pasta.
2. **Modelos**: confira se existem `models/argos_epi_v1.pt` e
   `models/insightface/models/buffalo_l/*.onnx`. Se faltarem, peça ao usuário a pasta
   `models` do pacote do André. Não tente treinar nem baixar outro modelo no lugar.
3. **Docker**: confirme `docker info` e `docker compose version`. Se o Docker Desktop
   estiver desligado, peça para o usuário abrir.
4. **Instalar**: rode `instalar_docker.bat` (ou `instalar_docker.bat cpu` sem GPU NVIDIA).
   Equivalente manual, na raiz do projeto:
   ```
   copy .env.example .env            (só se .env não existir)
   docker rm -f argosepi-backend argosepi-backend-cpu argosepi-db
   docker compose -p argosepi --profile gpu up -d --build     (ou --profile cpu)
   ```
   Os nomes dos containers são fixos (`argosepi-backend`, `argosepi-backend-cpu`,
   `argosepi-db`), por isso os antigos precisam sair antes. O volume `argosepi-pgdata`
   guarda o banco: **nunca** rode `docker volume rm argosepi-pgdata` nem `down -v`.
5. **Conferir**: `curl http://localhost:8088/status` deve responder 200 e o container
   ficar `healthy` (`docker ps`). O campo do túnel Cloudflare pode levar alguns segundos.
6. **Não envie para o git**: `.env`, `dados/`, `models/`, `*.pt`, `cloudflared.exe`
   (já estão no `.gitignore`). `dados/` tem fotos e rostos de pessoas reais.
