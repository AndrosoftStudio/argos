# Relatorio do que cada parte faz

## Frontend

Local: `frontend/index.html`.

Responsabilidades:
- tela de login e cadastro;
- dashboard de cameras;
- cadastro de EPIs e funcionarios;
- upload de modelos `.pt`;
- escolha de modelo por camera;
- exibicao de status seguro/perigo;
- configuracao de backend;
- consulta ao hub quando configurado.

## Pagina de camera remota

Local: `frontend/cam.html`.

Responsabilidades:
- abrir camera do celular;
- enviar frames para o backend;
- receber imagem processada;
- mostrar FPS e alertas;
- permitir desligar a camera.

## Configuracao do frontend

Local: `frontend/api/config.js`.

Responsabilidades:
- definir backend fixo, quando usado;
- definir hub do Render, quando usado;
- permitir que o frontend escolha automaticamente um backend disponivel.

## Backend principal

Local: `backend/app.py`.

Responsabilidades:
- autenticar usuarios;
- gerenciar modelos;
- receber frames;
- criar e remover streams;
- cadastrar EPIs e funcionarios;
- treinar modelos;
- exportar TensorRT;
- expor metricas de hardware;
- registrar o backend no hub;
- disponibilizar APIs de integracao.

## Motor de processamento de video

Local: `backend/processor.py`.

Responsabilidades:
- carregar modelo YOLO ou TensorRT;
- escolher hardware CPU, DirectML ou NVIDIA;
- processar frames;
- detectar classes;
- verificar EPIs obrigatorios;
- reconhecer funcionarios com modelo extra;
- gerar frame anotado;
- informar FPS, deteccoes e atividade.

## Buffer de frames

Local: `backend/multistream.py`.

Responsabilidades:
- receber frames em dois slots paralelos;
- manter o frame mais recente;
- reduzir atraso na transmissao do navegador para o backend.

## Usuarios

Local: `backend/users.py`.

Responsabilidades:
- cadastrar usuarios;
- autenticar login;
- gerenciar sessoes;
- armazenar perfis em SQLite;
- criar tokens para cameras remotas.

## Backend Hub

Local: `backend/hub.py`.

Responsabilidades:
- receber registro dos backends;
- receber heartbeat;
- armazenar URL publica, hardware, carga e disponibilidade;
- retornar lista de backends online;
- indicar o melhor backend para o frontend.

## Dados

Local: `dados/`.

Responsabilidades:
- guardar usuarios;
- guardar modelos por usuario;
- guardar fotos de EPIs;
- guardar fotos de funcionarios;
- guardar metadados de treinamento.

## Scripts

Arquivos:
- `run.py`: inicia o backend.
- `iniciar.bat`: inicia o sistema no Windows.
- `configurar.bat`: prepara ambiente.
- `atualizar.bat`: atualizacao local.

## Cloudflare Tunnel

Arquivo:
- `cloudflared.exe`.

Responsabilidade:
- expor o backend local com uma URL publica temporaria para testes e uso remoto.
