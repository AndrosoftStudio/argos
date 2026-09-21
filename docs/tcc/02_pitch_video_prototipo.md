# Roteiro do video demonstrativo do prototipo

Duração sugerida: 3 a 5 minutos.  
Tom da fala: explicar como aluno apresentando um prototipo funcional, sem tentar vender como se fosse um produto pronto.

## Antes de gravar

Deixe tudo preparado para nao perder tempo no video:

1. Abra o backend do projeto.
2. Abra o frontend no navegador.
3. Deixe um usuario de teste ja criado.
4. Separe um EPI para demonstrar, como capacete, colete, oculos ou farda.
5. Teste a camera antes de gravar.
6. Deixe pelo menos um modelo disponivel na aba de modelos.
7. Se for mostrar API, deixe outra aba aberta com `/status`, `/hardware` ou uma consulta da API.
8. Feche abas e programas que nao tenham relacao com o projeto.

Durante a gravacao, mostre a tela e fale como se estivesse guiando a banca. Nao precisa ler palavra por palavra. Use o roteiro como base e fale naturalmente.

## Sequencia recomendada

| Tempo | O que mostrar | Objetivo |
|---|---|---|
| 0:00 a 0:25 | Tela inicial ou login | Apresentar o problema. |
| 0:25 a 0:50 | Dashboard | Mostrar que o sistema e web e tem painel. |
| 0:50 a 1:25 | Aba de EPIs | Mostrar cadastro e fotos. |
| 1:25 a 1:55 | Aba de funcionarios | Mostrar reconhecimento como parte da POC. |
| 1:55 a 2:25 | Aba de modelos | Mostrar modelos, regras e TensorRT. |
| 2:25 a 3:30 | Camera funcionando | Demonstrar seguro/perigo. |
| 3:30 a 4:15 | API, hub ou hardware | Mostrar integracao e arquitetura. |
| 4:15 a 4:40 | Fechamento | Reforcar resultado e limites do prototipo. |

## Roteiro falado

### Cena 1 - Abertura e problema

**Acao na tela:** mostrar a tela inicial, o login ou uma imagem do ambiente de teste.

**Fala sugerida:**

"Este e o prototipo do Vigilancia EPI, projeto desenvolvido para o TCC do curso de Desenvolvimento de Sistemas. A ideia surgiu de um problema comum em ambientes de risco: muitas vezes o uso de EPI depende de fiscalizacao visual constante, mas na pratica nem sempre existe alguem olhando todas as cameras ou todos os setores ao mesmo tempo."

"O objetivo aqui nao e substituir o tecnico de seguranca. O sistema serve como apoio, ajudando a identificar mais rapido quando uma pessoa aparece sem um equipamento obrigatorio."

**Texto opcional na tela:**  
`Vigilancia EPI - apoio a seguranca do trabalho com visao computacional`

### Cena 2 - Login e painel

**Acao na tela:** fazer login ou mostrar o painel ja logado.

**Fala sugerida:**

"Ao entrar no sistema, cada usuario acessa seu proprio ambiente. Os cadastros, modelos, funcionarios e streams ficam separados por conta. No painel principal ficam as cameras ativas e os controles para escolher modelo, tipo de camera e equipamentos exigidos."

"Essa separacao por usuario foi importante porque, numa empresa real, as imagens e os dados dos funcionarios nao podem ficar misturados com os de outra conta."

### Cena 3 - Cadastro de EPIs

**Acao na tela:** abrir a aba de EPIs, mostrar um EPI cadastrado e as fotos.

**Fala sugerida:**

"Nesta parte o usuario cadastra os EPIs que quer acompanhar. Pode ser capacete, colete, oculos, uniforme ou qualquer outro item usado no ambiente. Alem do cadastro simples, o sistema permite enviar fotos para treinar um modelo especifico."

"Para o prototipo, tambem existe a opcao de importar um dataset anotado. Isso melhora o treinamento porque o modelo passa a aprender com marcacoes mais corretas, nao apenas com imagens soltas."

**O que fazer na tela:**

- Mostrar um EPI ja cadastrado.
- Mostrar as fotos enviadas.
- Se tiver tempo, clicar no botao de treinar apenas para mostrar onde fica, sem precisar esperar o treinamento terminar.

### Cena 4 - Cadastro de funcionarios

**Acao na tela:** abrir a aba de funcionarios.

**Fala sugerida:**

"O sistema tambem permite cadastrar funcionarios, com nome, cargo, matricula e fotos de rosto. No prototipo, essas fotos podem ser usadas para treinar um modelo de reconhecimento dos funcionarios."

"Essa parte foi colocada como prova de conceito. Em um ambiente real, o reconhecimento de pessoas precisaria de regras mais fortes de privacidade, autorizacao e seguranca dos dados."

**O que fazer na tela:**

- Mostrar um funcionario cadastrado.
- Mostrar que existem fotos de rosto.
- Mostrar o botao de treinamento de reconhecimento.

### Cena 5 - Modelos e regras

**Acao na tela:** abrir a aba de modelos.

**Fala sugerida:**

"Na aba de modelos ficam os modelos de deteccao disponiveis. O sistema pode usar modelos base, modelos enviados pelo usuario ou modelos treinados dentro do proprio prototipo."

"Um ponto importante e que cada modelo pode ter sua regra. Aqui eu posso dizer quais classes sao obrigatorias, por exemplo capacete e colete. Na hora da leitura da camera, se uma pessoa aparecer e algum item obrigatorio nao for detectado, o painel muda para perigo."

"Em maquinas com GPU NVIDIA, o backend tambem pode converter o modelo para TensorRT. Isso foi pensado para melhorar desempenho quando o sistema rodar com mais cameras ou com mais processamento."

**O que fazer na tela:**

- Mostrar um modelo.
- Mostrar as classes ou EPIs obrigatorios.
- Mostrar a opcao de modelo padrao.
- Se aparecer status TensorRT, mostrar rapidamente.

### Cena 6 - Camera e deteccao em tempo real

**Acao na tela:** voltar para a aba de cameras e iniciar uma camera.

**Fala sugerida:**

"Agora eu vou mostrar a parte principal do prototipo, que e o monitoramento em tempo real. O sistema recebe a imagem da camera, envia para o backend, processa com o modelo de IA e devolve a imagem com as deteccoes."

"No painel aparecem o modelo ativo, os EPIs exigidos, o FPS e o status da leitura. Quando os itens obrigatorios estao presentes, o sistema fica como seguro."

**O que fazer na tela:**

1. Iniciar a camera.
2. Escolher o modelo correto.
3. Mostrar voce ou outra pessoa com o EPI.
4. Apontar para o status seguro, FPS e deteccoes.

### Cena 7 - Alerta de EPI ausente

**Acao na tela:** remover o EPI ou mostrar uma cena sem o item obrigatorio.

**Fala sugerida:**

"Agora eu vou simular uma situacao de risco. Quando o equipamento obrigatorio nao aparece na leitura, o sistema muda o status para perigo e informa qual EPI esta ausente."

"Esse alerta permite que a equipe responsavel perceba o problema mais rapido. A ideia e agir antes que uma falha simples vire acidente ou afastamento."

**O que fazer na tela:**

1. Remover o EPI escolhido.
2. Esperar o painel atualizar.
3. Mostrar o status "FALTA EPI" ou "perigo".
4. Mostrar a lista de itens ausentes.
5. Colocar o EPI de volta, se quiser mostrar o retorno para seguro.

### Cena 8 - Camera remota, API e arquitetura

Escolha uma ou duas opcoes para nao alongar demais.

#### Opcao A - Camera remota

**Acao na tela:** mostrar a pagina `/cam` ou comentar que ela existe.

**Fala sugerida:**

"O prototipo tambem tem uma pagina para camera remota. Com ela, um celular pode funcionar como camera do sistema, enviando os frames para o backend. Isso ajuda em demonstracoes e tambem mostra que o projeto nao depende apenas de uma webcam ligada direto no computador."

#### Opcao B - API

**Acao na tela:** mostrar uma resposta JSON da API ou a rota de status.

**Fala sugerida:**

"Alem do painel, o backend disponibiliza uma API de integracao. Por ela, outro sistema pode consultar streams ativos, deteccoes, funcionarios, EPIs cadastrados, status da leitura, FPS e itens ausentes."

"Isso e importante porque uma empresa poderia integrar o resultado com um dashboard interno, sistema de seguranca ou registro de ocorrencias."

#### Opcao C - Hub e multiplos backends

**Acao na tela:** mostrar o documento de arquitetura ou explicar com o painel/configuracao.

**Fala sugerida:**

"A arquitetura tambem foi pensada para crescer. Existe um hub central onde varios backends podem se registrar. Cada backend informa sua URL, hardware, quantidade de streams e disponibilidade. O frontend consulta o hub e escolhe o melhor servidor disponivel."

"Com isso, o processamento de IA pode rodar em maquinas diferentes, inclusive em uma maquina com GPU, enquanto o frontend fica hospedado separadamente."

### Cena 9 - Fechamento

**Acao na tela:** voltar para o painel com a camera ou para a tela principal.

**Fala sugerida:**

"Como resultado, o prototipo mostra que e possivel usar cameras comuns, backend Python, modelos YOLO e uma interface web para apoiar a vigilancia de EPIs em tempo real."

"O projeto ainda tem pontos para evoluir, principalmente precisao com datasets maiores, historico de alertas, permissoes por perfil e politicas de privacidade. Mesmo assim, a prova de conceito ja demonstra o fluxo principal: cadastrar, treinar ou escolher modelo, monitorar camera, identificar falta de EPI e disponibilizar os dados para integracao."

"Esse foi o Vigilancia EPI, uma proposta de uso da tecnologia para ajudar a prevenir riscos no ambiente de trabalho."

## Versao curta da fala

Use esta versao se o video precisar ficar perto de 2 minutos.

"Este e o Vigilancia EPI, meu prototipo de TCC para apoiar a seguranca do trabalho com visao computacional. O problema que ele tenta resolver e a dificuldade de acompanhar manualmente, o tempo todo, se os trabalhadores estao usando os equipamentos obrigatorios."

"No sistema, o usuario faz login, cadastra EPIs, cadastra funcionarios e escolhe modelos de deteccao. Tambem e possivel enviar fotos, importar dataset e treinar modelos personalizados para o ambiente da empresa."

"Aqui na aba de cameras, eu inicio o monitoramento. O backend recebe os frames, processa com YOLO e devolve a imagem com as deteccoes. O painel mostra o modelo ativo, FPS, EPIs exigidos e o status da leitura."

"Quando o EPI obrigatorio aparece, o ambiente fica como seguro. Quando eu retiro o equipamento, o sistema muda para perigo e informa qual item esta ausente. Isso ajuda a equipe de seguranca a perceber a situacao mais rapido."

"O prototipo tambem possui camera remota por celular, API de integracao para empresas, suporte a modelos enviados pelo usuario e uma arquitetura com hub para escolher entre varios backends. Em maquinas NVIDIA, ele ainda pode usar TensorRT para melhorar desempenho."

"Como prova de conceito, o projeto demonstra o fluxo principal: cadastrar, configurar, monitorar, alertar e integrar. Para uma versao de producao, os proximos passos seriam melhorar datasets, historico de alertas, permissoes e politicas de privacidade."

## Checklist do que mostrar no video

- Login ou painel ja autenticado.
- Aba de EPIs com pelo menos um EPI cadastrado.
- Fotos do EPI ou dataset importado.
- Aba de funcionarios com pelo menos um funcionario.
- Aba de modelos com modelo selecionavel.
- Camera funcionando.
- Status seguro.
- Status perigo com EPI ausente.
- FPS aparecendo.
- Uma resposta de API, status do backend ou explicacao do hub.

## Cuidados na apresentacao

- Nao diga que o sistema esta pronto para substituir fiscalizacao humana.
- Nao prometa 100% de precisao.
- Explique que a qualidade depende do treinamento e das imagens.
- Se o reconhecimento de funcionario falhar, apresente como POC e foque na deteccao de EPI.
- Se a camera travar, use um video de teste ou uma imagem/cena ja preparada.
- Evite gastar muito tempo em cadastro. Mostre dados ja cadastrados e explique o fluxo.
