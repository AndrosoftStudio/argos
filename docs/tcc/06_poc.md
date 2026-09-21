# Prova de conceito (POC)

## Hipotese

E possivel usar cameras comuns e um backend Python com IA para identificar, em tempo real, se uma pessoa esta usando os EPIs obrigatorios, com custo acessivel e arquitetura expansivel.

## O que a POC precisa provar

1. O sistema consegue receber frames de camera.
2. O modelo consegue detectar objetos/EPIs.
3. O painel consegue exibir o resultado em tempo real.
4. O usuario consegue cadastrar EPIs e funcionarios.
5. O sistema consegue treinar ou receber modelos customizados.
6. O backend NVIDIA pode converter modelo para TensorRT.
7. Varios backends podem se registrar em um hub.
8. O frontend consegue escolher um backend funcional.
9. Empresas conseguem consultar deteccoes por API.

## Cenario de demonstracao

Ambiente:
- notebook ou PC rodando backend;
- camera local ou celular;
- frontend no navegador;
- um EPI de teste, como colete, capacete ou farda;
- um funcionario cadastrado com fotos.

Passos:
1. Abrir o sistema.
2. Fazer login.
3. Cadastrar um EPI.
4. Enviar imagens ou usar modelo existente.
5. Iniciar camera.
6. Mostrar status seguro com EPI.
7. Remover EPI e mostrar status de perigo.
8. Mostrar o endpoint de deteccao da API.
9. Mostrar `/hardware` e `/status`.
10. Explicar que backends extras aparecem no hub.

## Criterios de sucesso

| Criterio | Sucesso esperado |
|---|---|
| Camera funcionando | frame aparece no painel |
| Deteccao | sistema identifica classe do modelo |
| Alerta | sistema mostra EPI ausente |
| FPS | status mostra taxa de frames |
| Reconhecimento | funcionario aparece em `employees` quando treinado |
| API | endpoint retorna JSON com deteccoes |
| Hub | backend registra URL publica |
| TensorRT | backend NVIDIA gera `.engine` |

## Limites da POC

- A precisao depende da qualidade do dataset.
- Reconhecimento facial via YOLO e uma simplificacao para TCC; em producao, seria melhor combinar detector de rosto, embeddings faciais e regras de privacidade.
- O Cloudflare Tunnel usado no prototipo pode mudar de URL.
- O plano gratuito de hospedagem pode dormir ou limitar desempenho.

## Resultado esperado

A POC deve demonstrar que a ideia e tecnicamente possivel, util para seguranca do trabalho e pronta para evoluir para uma versao mais robusta.
