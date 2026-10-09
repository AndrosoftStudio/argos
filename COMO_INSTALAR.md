# Argos EPI v20 — como instalar o backend

Três jeitos, escolha um:

- **Programa Argos EPI Servidor** (o mais fácil): no site, **Ajustes → Servidores → Baixar o
  programa do servidor**. Escolha o sistema (Windows ou Linux) e a placa de vídeo (NVIDIA,
  AMD/Intel ou só o processador). Vem tudo já compilado; não precisa de administrador.
- **Com Docker** (PC próprio): `instalar_docker.bat`. Liga sozinho junto com o Docker Desktop.
- **Sem Docker** (PC da escola, sem administrador): `configurar.bat` uma vez e depois
  `iniciar.bat` sempre que for usar.

Os dois usam a mesma pasta `models` e o mesmo `.env`, e o painel fica em **http://localhost:8088**.
Se a porta 8088 já estiver ocupada (por outro programa ou pelo outro modo do Argos), o
servidor usa a próxima livre, até a 8097, e mostra o endereço na janela.

## Programa Argos EPI Servidor (instalador)

**Windows 10/11:** baixe `ArgosEPI-Servidor-Setup-<placa>.exe` pelo site e abra. O instalador
(pequeno) mostra a placa detectada, a pasta (padrão `%LOCALAPPDATA%\Programs\Argos EPI Servidor`),
"Iniciar junto com o Windows" e o atalho na área de trabalho; depois baixa o programa **já
compilado** para a placa escolhida, com os modelos de IA junto. Na pasta instalada só ficam
`.exe`, `.dll` e os arquivos dos modelos (nenhum código-fonte, nenhum Python solto), e depois de
instalado nada mais é baixado. Instalar por cima de uma versão antiga limpa a pasta: ficam só
`dados\`, `.env`, `models\` e `uploads\`. Não pede administrador em nenhum momento.

O download vem por 8 conexões ao mesmo tempo e continua de onde parou se a internet cair ou a
janela for fechada (uma conexão que fica lenta é trocada sozinha). **Internet lenta ou vários
computadores (laboratório):** instale por pendrive, sem internet. No site, em "Baixar o programa
do servidor", abra "Instale por pendrive" e baixe os 5 arquivos; ponha o instalador e o
`latest.json` numa pasta e os 3 `.zip` numa subpasta `win`. Aberto dessa pasta, o instalador usa
os arquivos ao lado e não baixa nada. (Quem tem a pasta de build monta isso com
`servidor_app\build\montar_pendrive.ps1 -Destino E:\ArgosEPI -Placas cpu`.)

**Linux x64:** baixe o `.tar.gz` (NVIDIA ou CPU) e, no terminal, sem sudo:

```bash
tar -xzf argos-epi-servidor-*.tar.gz
cd argos-epi-servidor && ./instalar.sh
```

Fica em `~/.local/share/argos-epi-servidor`, com atalho no menu de aplicativos.

O programa abre numa janela própria (sem console e sem a barra de título do sistema: a barra
azul do topo arrasta a janela e tem os botões), com o ícone perto do relógio. Mostra a conta
vinculada, o computador (CPU, RAM, GPU), as câmeras que este servidor está processando (com
miniaturas) e o registro. Em **Ajustes**: iniciar com o sistema (em segundo plano ou com a
janela), o que o **X** faz (perguntar, segundo plano ou fechar o servidor), tema e **Processar
câmeras com** (automático, placa de vídeo ou processador) e **Aceitar conexões da rede local**
(desligado por padrão: o servidor só escuta em `127.0.0.1` e o acesso de fora vem pelo endereço
seguro do túnel, então o Windows não mostra o aviso de firewall que pede administrador). Ele
mesmo liga o PostgreSQL portátil, escolhe portas livres e se atualiza. Os modelos (EPIs, pose e
rosto) já vêm na instalação. Na versão NVIDIA, a conversão para TensorRT é um pacote opcional
(3 GB de DLLs): o botão "Instalar dependências do TensorRT", em **Ajustes → Servidores → Modelos
de IA** no painel, baixa o pacote já compilado para dentro da pasta do programa. O andamento
(porcentagem do download, tempo da conversão) aparece no painel e na tela **Início** do programa.

**Sem administrador:** o programa não pede permissão de administrador nem mostra o aviso do
Firewall do Windows. Tudo o que fica escutando usa só `127.0.0.1`; o vídeo direto abre portas
de saída (escolhidas pelo sistema, uma por conexão), as câmeras RTSP são lidas só por TCP e o
túnel do Cloudflare usa só conexões TCP de saída (`--protocol http2`; antes, em QUIC, ele reabria
a mesma porta UDP ao reconectar e o Windows mostrava o aviso; `ARGOS_TUNEL_PROTOCOLO=quic` no
`.env` volta ao modo antigo). Dá para instalar e usar num computador de escola ou de empresa
com usuário comum.
Os dados ficam em `dados\` dentro da pasta do programa e nunca são apagados ao atualizar.

**Atualizar** (botão do programa, no Windows) não instala tudo de novo: bibliotecas, modelos e
banco ficam como estão e só o programa em si é trocado (cerca de 3 MB a partir da 20.3.2; a
passagem de uma versão anterior para a 20.3.2 ainda baixa uns 45 MB, uma vez só). As bibliotecas
só voltam a ser baixadas quando uma delas muda de versão. A passagem para a 20.4.0 baixa, uma
vez só, o pacote do vídeo direto e da voz dos avisos (cerca de 105 MB). Ao tocar em Atualizar a
janela do programa fecha, aparece uma janelinha com o andamento e, quando termina, o programa
abre de novo sozinho (a tela grande do instalador só aparece se der algum erro).

## Vários servidores na mesma conta

Cada câmera tem um servidor dono (o painel sugere o mais livre ao adicionar). Além disso,
servidores da mesma conta que estejam **na mesma rede local** dividem o trabalho de uma câmera:
o dono continua com o rastreio das pessoas e a decisão sobre os EPIs, e o outro adianta o que é
pesado e não depende de memória (achar os rostos e rodar o detector de EPIs, quando ele tem o
mesmo arquivo de modelo). O dono cronometra cada resposta e só conta com o outro quando ele
responde em menos tempo do que o trabalho levaria aqui; se a resposta atrasa, faz sozinho e
deixa o outro de fora por um tempo. Para isso o servidor que ajuda precisa aceitar a rede local
(programa: **Ajustes → Aceitar conexões da rede local**; no Docker já aceita). O quadro da
câmera nunca viaja pela internet para isso. `ARGOS_AJUDA=0` desliga.

## EPIs por câmera, áreas e validação

- **Câmera:** em Ajustes da câmera, cada ícone de EPI é um botão. Ligado = aquela câmera cobra
  esse EPI de quem aparece. "Padrão" volta aos EPIs do modelo.
- **Áreas:** o botão de mapa no topo da câmera abre o editor (imagem grande e inteira). Dá para
  desenhar ou usar **Câmera inteira**; os EPIs de cada área são os mesmos botões. Onde duas
  áreas se cruzam vale a menor; fora de todas valem os EPIs da câmera.
- **Validação:** quem foi visto com o EPI há pouco não o "perde" por alguns quadros em que o
  modelo falhou: a falta fica em verificação e só vale se durar 4 s. No histórico, a falta curta
  que o modelo só deduziu e que terminou com o EPI de volta é marcada como **provável engano** e
  sai dos gráficos (dá para desfazer, ou marcar outra na mão, na ficha do funcionário).

## Tempo real e vídeo direto

No modo **Tempo real** o servidor não guarda mais segundos de vídeo: analisa sempre o quadro
mais novo e mostra o resultado na hora. **Detalhado** (1,5 s) e **Super detalhado** (4 s)
continuam com atraso de propósito, porque olham um trecho do vídeo antes de decidir.

Ainda no Tempo real, a câmera do celular (link de **Dispositivos**) ou do navegador manda
**vídeo direto** para o servidor (WebRTC), em vez de foto por foto pelo túnel:

- O túnel do Cloudflare (ou a rede local) serve só para combinar a conexão. O vídeo vai por UDP
  direto para o computador do servidor, e o painel assiste esse mesmo vídeo, sem recodificar.
- Quando a rede não deixa fechar a conexão direta, tudo volta sozinho para o caminho antigo
  (o card da câmera mostra "direto" quando o vídeo direto está valendo).
- Quem faz esse trabalho é o **MediaMTX**, um programa separado que fica em `midia/mediamtx`.
  O instalador do programa já traz; no `iniciar.bat`, `iniciar.sh` e Docker ele é baixado
  sozinho na primeira vez (`ARGOS_MIDIA_BAIXAR=0` impede).
- **Não precisa de administrador nem de liberar o Firewall:** o MediaMTX não fica escutando em
  porta fixa. Para cada conexão ele abre uma porta de saída e é o servidor que manda o primeiro
  pacote, então o Firewall do Windows deixa a resposta voltar sem perguntar nada.
- **Rede ruim:** o servidor mede os pacotes perdidos do vídeo direto. Se a perda passa de 2% por
  6 segundos (imagem com blocos e manchas), ele volta sozinho ao envio normal (foto por foto,
  que não se corrompe) e só tenta o direto de novo depois de 2, 5 e 10 minutos.
- **Resolução e quadros por segundo de verdade (20.4.2):** o vídeo direto tem banda para o que
  foi escolhido no painel (até 12 Mb/s em 1080p a 60 q/s; 8 a 30 q/s; 4 em 720p; 1,5 em 480p) e,
  quando a rede aperta, cai o ritmo, não a resolução: é a resolução que deixa ver quem está
  longe. Com o teto antigo (2,8 Mb/s) o celular mostrava "60 fps · 1080p", mas o navegador
  encolhia a imagem para caber e saía borrada. A tela do celular agora mostra o que está saindo
  de fato e, se for menos que o pedido, diz o motivo (a câmera do aparelho não dá, a rede não
  dá, o aparelho não dá conta). "1080p" conta o lado menor: vale com o celular em pé ou deitado.
- **Gente longe:** no Tempo real com placa NVIDIA a imagem vai inteira para a análise (antes era
  encolhida para 640 pontos e quem estava longe sumia). A rede de pose roda no tamanho da
  imagem enquanto a análise couber em 20 por segundo; se ficar lenta, ela diminui sozinha. Quem
  está sem EPI confirmado ganha um recorte ampliado (segunda olhada). Em teste com 30 pessoas de
  ~65 pontos de altura numa imagem Full HD: antes 10 a 12 achadas, agora 30.
- **Caixa no quadro certo:** cada resultado diz de que quadro do vídeo ele é (o número que a
  câmera carimba em cada quadro), e o painel, a TV e o próprio celular desenham em cada quadro a
  caixa daquele quadro, segurando a imagem uma fração de segundo (100 a 200 ms) até a caixa dela
  chegar. Antes a caixa mais nova ia por cima da imagem do momento e ficava para trás de quem
  corre. Medido com um vídeo de teste a 60 q/s: a caixa ficava 45 ms atrás da pessoa (mais pelo
  túnel); agora fica a 3–5 ms, em média 5 pontos da posição certa numa imagem de 1920.
- **Modo de eficiência do Windows:** o Windows 11 deixa programa sem janela com os núcleos lentos
  do processador. O servidor agora pede para ficar de fora (não precisa de administrador): numa
  RTX 4060 Ti a análise passou de 25 para 50 quadros por segundo. `ARGOS_ECONOMIA=1` desfaz.
- Quem assiste o painel de **outro computador** pode não conseguir o vídeo direto (o Firewall do
  servidor não aceita conexão de entrada): nesse caso o painel mostra as imagens pelo caminho
  normal, sem ninguém precisar fazer nada. No próprio computador do servidor o direto funciona.
- **TURN (opcional):** para redes que bloqueiam a conexão direta (4G de algumas operadoras,
  rede de empresa), crie uma chave em Cloudflare → Realtime → TURN e cadastre **só na Vercel**
  as variáveis `CF_TURN_KEY_ID` e `CF_TURN_API_TOKEN`. Sem elas o sistema usa só o STUN
  (gratuito) e o caminho antigo como reserva.
- Ajustes no `.env` do servidor: `ARGOS_RTC=0` desliga o vídeo direto; `ARGOS_RTC_PORTA_UDP=8189`
  volta a usar uma porta fixa (só para quem pode liberar o Firewall como administrador: melhora
  a conexão direta de quem assiste de outro computador; no Docker, publique a porta);
  `ARGOS_RTC_HOSTS=ip` anuncia um endereço a mais (Docker: o IP do computador na rede);
  `ARGOS_RTC_PERDA_LIMITE=2` é a perda (em %) que faz voltar ao envio normal.

## TV de avisos (sirene e voz)

A sirene e os avisos falados saem numa **TV**, não no painel:

1. No site, **Dispositivos → + Link → TV de avisos**, dê um nome e copie o link.
2. Abra o link no navegador da TV (ou num computador ligado à TV por HDMI) e toque em
   **Ativar som** (o navegador só libera áudio depois de um toque).
3. Quando alguém aparece sem EPI, a TV toca a sirene e fala, por exemplo: "André Jorge, por
   favor, coloque o capacete e os óculos de proteção, na área Solda." Se a pessoa continuar sem
   o EPI, o aviso se repete a cada 30 s (`ARGOS_AVISO_REPETIR_S`).

- A TV também mostra **as câmeras ao vivo** (com as marcações), ao lado dos avisos; a câmera
  de quem está sem EPI fica com a borda em destaque. O botão **Câmeras**, na própria TV, esconde ou
  mostra o vídeo.
- O volume, a sirene e a voz são ajustados na própria TV (ficam guardados nela). Em
  **Dispositivos** dá para ver se a TV está aberta, o volume dela e mandar um aviso de teste.
- Cada aviso fica no **desempenho do funcionário**: a frase falada, a TV e o volume, junto do
  período sem o EPI, dos EPIs exigidos, da área e da câmera.
- A voz é gerada no próprio servidor pelo **Piper** (`midia/piper`, voz `pt_BR-faber-medium`),
  sem internet. Enquanto a voz não estiver instalada, a TV toca só a sirene.
- No painel a sirene vem desligada; quem não tem TV liga em **Ajustes → Sirene neste painel**.
- Área com o alarme desligado não gera aviso na TV.

## Ajustes da câmera

O botão de ajustes do card da câmera abre uma janela com tudo: nome, fonte do vídeo, modelo,
modo de análise, EPIs cobrados, **Reconhecer o rosto** (desligue em computador fraco: o sistema
continua dizendo se há EPI, só não diz quem é) e **Servidor** (qual servidor da conta processa
esta câmera). **Salvar alterações** aplica no servidor mesmo com a câmera ligada; tocar fora
da janela pergunta se quer salvar.

## Placa de vídeo ou processador

Cada servidor escolhe onde a IA roda: **site → Ajustes → Servidores**, no cartão do
servidor, ou no programa do servidor em **Ajustes**. **Automático** usa a placa de vídeo se
houver; **Processador** deixa a placa livre. A escolha fica no próprio servidor
(`dados/hardware.json`) e as câmeras ligadas recarregam sozinhas. O reconhecimento de rosto
também vai para a placa: no Docker com GPU e no programa NVIDIA entra o `onnxruntime-gpu`
(CUDA 12); no programa AMD/Intel, o `onnxruntime-directml`.

## Com Docker (1 clique)

1. Instale o **Docker Desktop**: https://www.docker.com/products/docker-desktop/
   (no Windows ele pede o WSL 2 na primeira vez; aceite e reinicie o PC se pedir).
2. Extraia a pasta do Argos onde quiser (ex.: `C:\ArgosEPI`).
3. Dê dois cliques em **`instalar_docker.bat`**.
4. Espere terminar. Na primeira vez demora de 10 a 30 minutos porque baixa o PyTorch.
5. Na primeira vez abre sozinho o site na página **Vincular servidor**. Ele encontra o
   servidor deste computador sozinho: entre na sua conta do Argos (senha ou Google) e
   clique em **Vincular**. Se não abrir, entre em **https://argosepi.vercel.app** →
   **Ajustes** → **Servidores** → **Adicionar este computador**.
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
| `midia/` (MediaMTX, Piper e a voz) | vídeo direto e avisos falados | **não** (baixado sozinho na primeira vez) |

A pasta `models` não fica no GitHub porque é pesada. Ela vai no pacote `.zip` enviado pelo
André. Quem clonar o repositório precisa copiar essa pasta para dentro do projeto.

## Site argosepi.vercel.app e contas

As contas ficam no site. Cada servidor (Docker, `iniciar.bat` ou `iniciar.sh`) é vinculado
a **uma** conta e só processa para ela. Depois do vínculo, ele aparece no site em
**Servidores** e o painel o encontra sozinho, sem `HUB_API_KEY` e sem configurar nada.

- Trocar a conta do servidor: na janela do programa, **Início → Desvincular da conta** (as
  câmeras dele param; funcionários, EPIs e histórico continuam guardados no computador), ou no
  site, **Servidores → Desvincular**. Em até 1 minuto ele volta a pedir vínculo (no site:
  **Servidores → Adicionar este computador**).
- O vínculo é feito no site, **no próprio computador do servidor**: a página
  `argosepi.vercel.app/parear.html` lê o código direto do servidor em
  `http://127.0.0.1:8088` (ou na próxima porta até 8097). O servidor só entrega o código
  para o próprio computador e só para o site oficial; quem chega pelo link público ou de
  outro site não consegue ler.
- O Chrome pode perguntar se o site pode **acessar apps e dispositivos deste computador**:
  clique em **Permitir**. Sem isso o site não acha o servidor (dá para digitar o código
  que aparece na janela do servidor).

## TensorRT (mais velocidade com GPU NVIDIA)

O TensorRT deixa a detecção bem mais rápida em placa NVIDIA, mas é grande (~2 GB) e
por isso não vem instalado. No painel, em **Ajustes → Servidores → Modelos de IA** (os modelos
são de cada servidor: com mais de um na conta, escolha qual), aparece o botão **Instalar
dependências do TensorRT**. Toque nele e espere (5 a 20 minutos, conforme a internet); as
câmeras seguem funcionando. Quando terminar, toque no raio (⚡) do card para converter o modelo.

- O download mostra a porcentagem e há quanto tempo começou, no painel e na tela **Início** do
  programa do servidor.
- A **conversão** leva de 3 a 10 minutos (até 20 com a placa de vídeo ocupada por outro
  programa). O TensorRT não informa a porcentagem, então o painel e o programa mostram há
  quanto tempo começou e, a partir da segunda vez, quanto levou da última vez naquele computador.
- Enquanto converte, a placa de vídeo fica ocupada: a análise das câmeras pode ficar mais lenta
  ou falhar por instantes. Prefira converter com as câmeras paradas.

- No Docker ele fica no volume `argosepi-pylibs` e continua lá quando o container é
  recriado. No `iniciar.bat`/`iniciar.sh` vai para o Python da própria pasta.
- Sem GPU NVIDIA o botão não aparece: o TensorRT só funciona com ela.

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
- **"porta 8088 em uso"**: o servidor usa sozinho a próxima porta livre (até 8097) e
  mostra o endereço. No Docker a escolha fica no `.env` (`ARGOS_PORTA_DOCKER`).
- **O Windows pergunta sobre o Firewall** ao ligar o `iniciar.bat`: sem administrador, pode
  cancelar. O painel em `localhost` e o link do Cloudflare continuam funcionando; só o acesso
  pelo IP da rede local fica bloqueado.
- **O Windows pergunta sobre o Firewall para o `mediamtx.exe` ou o `ArgosMotor.exe`**: isso era
  da 20.4.0. A partir da 20.4.1 o programa não abre mais porta fixa nem lê câmera por UDP, e o
  aviso não aparece. Se aparecer por causa de uma versão antiga, pode cancelar: nada quebra.
- **Imagem da câmera do celular com blocos e manchas**: a rede está perdendo pacotes (Wi-Fi
  fraco ou computador muito ocupado). O servidor volta sozinho ao envio normal em alguns
  segundos; o card deixa de mostrar "direto". Ver "Tempo real e vídeo direto".
- **A detecção para e volta**: normalmente é o computador sem folga (veja "Livre" em
  Servidores). Converter um modelo para TensorRT, por exemplo, ocupa a placa de vídeo por
  alguns minutos. Na 20.4.0 e 20.4.1 havia também dois erros com o modelo em TensorRT, que
  perdiam quadros inteiros ("input size ... not equal to max model size" quando havia duas ou
  mais pessoas para a segunda olhada, e "operation not permitted when stream is capturing" logo
  ao ligar a câmera). Corrigidos na 20.4.2; se o TensorRT falhar 3 vezes seguidas, o servidor
  passa a usar o modelo original sozinho.
- **O celular diz "60 fps · 1080p" mas a imagem chega menor**: a partir da 20.4.2 a tela do
  celular mostra o que sai de verdade e o motivo de sair menos. Muito celular só filma 60 q/s em
  720p: escolha 30 q/s no painel para ter 1080p.
- **A TV não fala, só toca a sirene**: a voz ainda está sendo baixada ou o download falhou
  (procure `[midia]` e `[voz]` no registro). O teste em **Dispositivos → Testar som das TVs**
  diz se a voz está instalada.
- **A câmera não mostra "direto"**: a rede não deixou fechar a conexão direta e o sistema está
  no caminho antigo (funciona, com um pouco mais de atraso). Veja o TURN em "Tempo real e vídeo
  direto".
- **Erro de DLL ou "Visual C++"** no `configurar.bat`: falta o Visual C++ Redistributable
  (https://aka.ms/vs/17/release/vc_redist.x64.exe). Quase todo PC já tem.
- **Erro de GPU**: atualize o driver da NVIDIA ou use `instalar_docker.bat cpu`.
- **Ver o que está acontecendo**: `docker logs -f argosepi-backend` (ou `argosepi-backend-cpu`).
- **A página de vínculo não abriu ou não achou o servidor**: no próprio PC, abra
  **https://argosepi.vercel.app** → **Ajustes** → **Servidores** → **Adicionar este computador**, e
  permita o acesso se o navegador perguntar. Ainda não achou? Digite o código que aparece
  na janela do servidor (ou em `docker logs argosepi-backend`).
- **"Este servidor pertence a outra conta"**: ele foi vinculado a outra pessoa. Quem é
  dono desvincula em **Servidores**, ou entre com a conta certa.
- **"Nenhum servidor ligado"** no site: o servidor está desligado ou ainda não foi vinculado.
- **O celular-câmera não acha o servidor** (link de **Dispositivos**): o celular chega no servidor
  pelo endereço de internet (túnel do Cloudflare). A página do celular diz o motivo; no programa
  do servidor, veja a linha **Internet** na tela **Início** (na versão 20.3.1 em diante ela mostra
  "sem endereço público" e a causa). Rede de escola ou empresa costuma bloquear o túnel: ligue o
  computador do servidor em outra rede (por exemplo, a internet compartilhada do celular). No
  `iniciar.bat`/Docker, procure as linhas `[CF]` no registro.

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
   `nao_vinculado` na primeira vez. Peça ao usuário para abrir
   `https://argosepi.vercel.app/parear.html` **nesta máquina** (o site acha o servidor
   sozinho): o **usuário** entra na conta dele e clica em Vincular. Você não aprova o vínculo nem pede a senha dele. Depois disso a
   resposta vira `vinculado`. Não apague a tabela `servidor_local` do banco (é a
   credencial do servidor).
6. **Não envie para o git**: `.env`, `dados/`, `models/`, `*.pt`, `cloudflared.exe`, `bin/`, `python/`, `midia/`
   (já estão no `.gitignore`). `dados/` tem fotos e rostos de pessoas reais.
