# Parte financeira

## Objetivo financeiro

Validar se o Vigilancia EPI pode ser oferecido como uma solucao acessivel para pequenas e medias empresas, com custo menor que solucoes industriais proprietarias e com possibilidade de crescimento por assinatura.

## Custos do prototipo

| Item | Tipo | Custo estimado |
|---|---:|---:|
| Notebook/PC de desenvolvimento | existente | R$ 0,00 |
| Camera webcam/celular | existente | R$ 0,00 a R$ 150,00 |
| Hospedagem frontend na Vercel | mensal | R$ 0,00 no plano inicial |
| Hub no Render | mensal | R$ 0,00 no plano inicial |
| Backend local com Cloudflare Tunnel | mensal | R$ 0,00 |
| Energia/internet para testes | mensal | variavel |
| Domínio proprio, se usado | anual | R$ 40,00 a R$ 80,00 |

## Custos em producao

| Cenário | Infraestrutura | Estimativa |
|---|---|---:|
| Pequena empresa | 1 PC comum + cameras existentes | baixo |
| Empresa media | 1 PC com GPU NVIDIA + cameras IP | medio |
| Multiplas unidades | varios backends + hub central | medio/alto |

## Modelo de receita

Opcoes:
- Assinatura mensal por empresa.
- Assinatura por quantidade de cameras.
- Implantacao inicial + mensalidade de suporte.
- Licenca local para empresas que nao podem enviar dados para nuvem.

Sugestao inicial:
- Plano basico: ate 2 cameras.
- Plano profissional: ate 10 cameras.
- Plano personalizado: multiplas unidades e integracao por API.

## Proposta de valor economica

O sistema pode reduzir:
- tempo de fiscalizacao manual;
- reincidencia de uso incorreto de EPI;
- custos indiretos com acidentes;
- tempo de resposta da equipe de seguranca;
- perda de produtividade por afastamentos.

## Indicadores para demonstrar na banca

| Indicador | Como medir no prototipo |
|---|---|
| Tempo de deteccao | diferenca entre frame e alerta |
| FPS | status de cada stream |
| Disponibilidade do backend | endpoint `/status` e hub |
| Uso de hardware | endpoint `/hardware` |
| Escalabilidade | quantidade de backends registrados no hub |

## Viabilidade

Para um TCC de 3o semestre, a viabilidade e forte porque o prototipo ja demonstra a funcao principal com recursos acessiveis: camera, navegador, backend Python, IA YOLO, cadastro e dashboard. A parte financeira mostra que a solucao pode comecar barata e crescer conforme a necessidade da empresa.
