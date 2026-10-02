# Argos EPI v20 — como instalar o backend

Dois jeitos, escolha um:

- **Com Docker** (PC próprio): `instalar_docker.bat`. Liga sozinho junto com o Docker Desktop.
- **Sem Docker** (PC da escola, sem administrador): `configurar.bat` uma vez e depois
  `iniciar.bat` sempre que for usar.

Os dois usam a mesma pasta `models` e o mesmo `.env`, e o painel fica em **http://localhost:8088**.
Não ligue os dois ao mesmo tempo, porque os dois usam a porta 8088.

## Com Docker (1 clique)

1. Instale o **Docker Desktop**: https://www.docker.com/products/docker-desktop/
   (no Windows ele pede o WSL 2 na primeira vez; aceite e reinicie o PC se pedir).
2. Extraia a pasta do Argos onde quiser (ex.: `C:\ArgosEPI`).
3. Dê dois cliques em **`instalar_docker.bat`**.
4. Espere terminar. Na primeira vez demora de 10 a 30 minutos porque baixa o PyTorch.
5. Na primeira vez abre sozinha a página **Vincular servidor**: entre na sua conta do
   Argos (senha ou Google), confira o código e clique em **Vincular**. Se não abrir,
   acesse **http://localhost:8088/parear**.
6. Use o painel em **https://argosepi.vercel.app** (ou **http://localhost:8088**).

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

## Sem Docker (PC sem administrador)

Serve para o PC do laboratório, onde não dá para instalar o Docker nem o Python.

1. Extraia a pasta do Argos onde tiver permissão de escrita (ex.: `Documentos` ou um
   pendrive). Evite pastas do OneDrive: o banco não gosta de ser sincronizado.
2. Dê dois cliques em **`configurar.bat`** e espere. Ele baixa cerca de 1,5 GB.
3. Dê dois cliques em **`iniciar.bat`**. Na primeira vez o navegador abre a página
   **Vincular servidor**: entre na sua conta e clique em **Vincular**.
4. Use o painel em **https://argosepi.vercel.app** (ou **http://localhost:8088**).
5. Para desligar, Ctrl+C ou feche a janela.

**Linux e macOS (sem Docker):** com um usuário comum (não root), rode `bash configurar.sh`
e depois `bash iniciar.sh`. O `configurar.sh` baixa um PostgreSQL 16 portátil (~15 MB, em
`bin/pgsql`) e cria o banco em `dados/pgdata`, porta 5433. O `iniciar.sh` liga o banco, o
servidor e, ao sair, desliga o banco. Só precisa de Python 3.11+ instalado.

O banco portátil e o do Docker são servidores diferentes: cada um é vinculado uma vez.
Vinculados à mesma conta, eles se sincronizam sozinhos (veja "Vários servidores").

O `configurar.bat` não instala nada no Windows. Tudo fica dentro da pasta:

| Pasta | O que é |
|---|---|
| `python\` | Python 3.12 portátil com o PyTorch e as dependências |
| `bin\uv.exe` | instalador de pacotes |
| `bin\pgsql\` | PostgreSQL 16 portátil |
| `dados\pgdata\` | o banco (usuário `argos`, porta 5433, só aceita conexão da própria máquina) |
| `cloudflared.exe` | link público para o site e para o celular-câmera |

**Dica para apresentação:** configure em casa, teste e leve a pasta inteira num pendrive.
Ela roda em outro PC Windows sem baixar nada de novo. A internet só é usada pelo site e
pelo link do Cloudflare.

**Levar os dados do Docker:** com o Docker ligado, rode `copiar_banco_do_docker.bat`. Ele
copia contas, funcionários, rostos, áreas e auditoria para o banco portátil. As fotos já
ficam na pasta `dados\`.

Para forçar o modo: `configurar.bat cpu` ou `configurar.bat gpu`. Rodar o `configurar.bat`
de novo é seguro: ele aproveita o que já foi baixado e não apaga o banco.

## O que precisa estar na pasta

| Item | Para quê | Vem do GitHub? |
|---|---|---|
| `backend/`, `frontend/`, `scripts/`, `run.py`, `Dockerfile`, `docker-compose.yml`, `requirements.txt`, `.env.example` | o sistema | sim |
| `models/argos_epi_v1.pt` (+ `.json`) | modelo treinado que detecta os EPIs | **não** (peça a pasta `models`) |
| `models/yolo26*.pt`, `models/yolo26*-pose.pt` | pessoas e pose | **não** (vêm no pacote) |
| `models/insightface/` | reconhecimento de rosto | **não** (vêm no pacote) |

A pasta `models` não fica no GitHub porque é pesada. Ela vai no pacote `.zip` enviado pelo
André. Quem clonar o repositório precisa copiar essa pasta para dentro do projeto.

## Site argosepi.vercel.app e contas

As contas ficam no site. Cada servidor (Docker, `iniciar.bat` ou `iniciar.sh`) é vinculado
a **uma** conta e só processa para ela. Depois do vínculo, ele aparece no site em
**Servidores** e o painel o encontra sozinho, sem `HUB_API_KEY` e sem configurar nada.

- Trocar a conta do servidor: no site, **Servidores → Desvincular**. Em até 1 minuto
  ele volta a pedir vínculo (abra **http://localhost:8088/parear**).
- O vínculo só pode ser feito no próprio computador do servidor (a página `/parear`
  recusa quem chega pelo link público).

## Vários servidores (malha)

Vincule mais de um servidor à mesma conta (outro PC, o Docker e o `iniciar.bat`, etc.).
Eles formam uma malha: EPIs, equipe com os rostos, áreas e desenhos, links de celular e o
histórico de faltas são copiados entre eles a cada 20 segundos, com as fotos. O painel
mostra as câmeras de todos e põe as câmeras novas no servidor mais livre. Modelos `.pt`
ficam em cada máquina.

Para os servidores se acharem, eles precisam estar na mesma rede ou ter o link do
Cloudflare ligado (padrão).

## Problemas comuns

- **"porta 5432 em uso"**: tem um PostgreSQL instalado no PC. Pare o serviço dele
  (Serviços do Windows → postgresql → Parar).
- **"porta 8088 em uso"**: outro Argos ligado. Se for o do Docker, rode `parar_docker.bat`
  antes do `iniciar.bat`; se for o `iniciar.bat`, feche a janela dele antes de usar o Docker.
- **O Windows pergunta sobre o Firewall** ao ligar o `iniciar.bat`: sem administrador, pode
  cancelar. O painel em `localhost` e o link do Cloudflare continuam funcionando; só o acesso
  pelo IP da rede local fica bloqueado.
- **Erro de DLL ou "Visual C++"** no `configurar.bat`: falta o Visual C++ Redistributable
  (https://aka.ms/vs/17/release/vc_redist.x64.exe). Quase todo PC já tem.
- **Erro de GPU**: atualize o driver da NVIDIA ou use `instalar_docker.bat cpu`.
- **Ver o que está acontecendo**: `docker logs -f argosepi-backend` (ou `argosepi-backend-cpu`).
- **A página de vínculo não abriu**: acesse **http://localhost:8088/parear** no próprio PC.
  O código também aparece na janela do servidor (ou em `docker logs argosepi-backend`).
- **"Este servidor pertence a outra conta"**: ele foi vinculado a outra pessoa. Quem é
  dono desvincula em **Servidores**, ou entre com a conta certa.
- **"Nenhum servidor ligado"** no site: o servidor está desligado ou ainda não foi vinculado.

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
   Se a máquina não tiver Docker nem permissão de administrador, use `configurar.bat` e
   `iniciar.bat` (ver "Sem Docker"); o banco portátil fica em `dados\pgdata`, porta 5433.
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
6. **Vínculo com a conta**: `curl "http://localhost:8088/pareamento?simples=1"` responde
   `nao_vinculado` na primeira vez. Abra (ou peça ao usuário para abrir)
   `http://localhost:8088/parear` **nesta máquina**: o **usuário** entra na conta dele e
   clica em Vincular. Você não aprova o vínculo nem pede a senha dele. Depois disso a
   resposta vira `vinculado`. Não apague a tabela `servidor_local` do banco (é a
   credencial do servidor).
6. **Não envie para o git**: `.env`, `dados/`, `models/`, `*.pt`, `cloudflared.exe`, `bin/`, `python/`
   (já estão no `.gitignore`). `dados/` tem fotos e rostos de pessoas reais.
