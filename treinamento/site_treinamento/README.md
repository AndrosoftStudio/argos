# Acompanhar treinamento · Argos EPI

Site que mostra, de qualquer lugar, como vai o treinamento do detector de EPIs do Argos —
progresso, época atual, mAP, perdas, GPU, RAM, checkpoints e as últimas linhas do log.

O treino roda no PC de casa. O site **acha esse PC sozinho**: o painel do PC se registra num hub
de discovery (o mesmo serviço que o app do Argos usa para achar o backend de inferência) e o site
pergunta ao hub qual é o endereço do momento. Ninguém digita URL de túnel.

```
navegador → site → hub /training/best → URL pública do painel no PC
              └──────── /api/status ───────────┘
```

O repasse acontece no servidor do site, então o navegador só fala com o site: nada de CORS,
conteúdo misto ou endereço colado na mão. Quando o PC religa, o túnel muda de endereço, o painel
se registra de novo e o site segue a mudança na hora — se um pedido falha, ele repergunta ao hub
antes de desistir.

## Rodar

### Vercel

É o que está no ar em https://treinamentoargosepi.vercel.app. Basta importar o repositório:
sem build, a pasta `public/` é o site e cada arquivo de `api/` vira uma função.

* `vercel.json` já fixa `outputDirectory: public` e `framework: null` — sem isso a Vercel não acha
  o que servir e a página dá **404**.
* As variáveis de ambiente são opcionais (o hub padrão já está no código). Para mudar alguma,
  use *Settings → Environment Variables* e faça um *Redeploy*.

### Docker

```bash
docker compose up -d --build       # http://localhost:8080
```

Ou sem compose:

```bash
docker build -t argos-epi-treinamento .
docker run -d --name argos-epi-treinamento -p 8080:8080 argos-epi-treinamento
```

Sem nada disso, para testar: `python app.py` (só stdlib, Python 3.10+).

### Variáveis de ambiente

Todas opcionais — copie `.env.example` para `.env` se for usar o compose.

| Variável | Padrão | Para quê |
| --- | --- | --- |
| `PORT` | `8080` | porta do site (Docker; na Vercel não se usa) |
| `HUB_URL` | hub do Argos no Render | onde perguntar quem está treinando |
| `NODE_ID` | vazio | fixa um PC, quando há mais de um painel publicado |
| `PAINEL_URL` | vazio | pula o hub e fala direto com o painel (`http://192.168.0.10:8765`) |
| `PAINEL_TOKEN` | vazio | mesmo valor do `--token` do painel, se ele exigir |
| `HUB_API_KEY` | vazio | só se o hub exigir chave |
| `DESCOBERTA_S` | `20` | por quanto tempo a resposta do hub vale |

## Publicar o painel no PC que treina

No projeto do Argos (pasta `v19`):

```bat
treinamento\acompanhar_treinamento_online.bat
```

Isso abre o painel em `http://127.0.0.1:8765`, sobe um túnel do Cloudflare para essa porta e
manda a URL pública para o hub a cada 30 s. Fechar a janela tira o painel do ar e da lista do hub —
o treino continua rodando, porque o painel só lê arquivos.

Para exigir senha no painel, rode com `--token SEGREDO` e coloque o mesmo valor em `PAINEL_TOKEN`
aqui no site.

## Como está organizado

| Arquivo | Para quê |
| --- | --- |
| `public/` | a página do painel (o site em si) |
| `api/_comum.py` | acha o PC no hub e busca o status dele — usado pelos dois modos |
| `api/status.py`, `api/conexao.py` | as funções da Vercel |
| `app.py` | o servidor do Docker: serve `public/` e usa o mesmo `api/_comum.py` |
| `Dockerfile`, `docker-compose.yml` | o modo Docker |
| `vercel.json` | diz à Vercel que o site é `public/` e que `api/` são funções |
| `sincronizar.py` | recopia a página do painel para `public/` |

| Rota | O que faz |
| --- | --- |
| `/` | a página do painel |
| `/api/status` | o status do treino, repassado do PC (+ `conexao`, dizendo de onde veio) |
| `/api/conexao` | só a parte da descoberta: hub, painel, último erro |
| `/health` | healthcheck do Docker (não existe na Vercel) |

## Manutenção

`public/index.html` é uma cópia da página que o painel serve no PC
(`v19/treinamento/acompanhar_treinamento/index.html`, no repositório do Argos). Depois de mexer
nela lá, rode `python sincronizar.py` e mande o resultado para cá, para as duas ficarem iguais.

O lado do hub fica em `v19/backend/hub/hub.py` (rotas `/training/register`, `/training/heartbeat`,
`/training/nodes`, `/training/best` e `DELETE /training/<node_id>`).
