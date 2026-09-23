# Treinamento do detector de EPIs

Monta um dataset unificado a partir de datasets públicos de EPI e treina o modelo YOLO26 usado pelo Argos EPI. O resultado vai para `models/argos_epi_v1.pt`, que o servidor passa a usar como padrão automaticamente.

## Requisitos

- GPU NVIDIA com pelo menos 8 GB (testado numa RTX 4060 Ti).
- Uns 40 GB livres **fora do OneDrive**. Os dados ficam em `D:\ArgosEPI` quando existe um disco D:, senão em `~/ArgosEPI-treino`. Dá para mudar com a variável `ARGOS_TREINO_DIR`.
- Se a base fica num HD mecânico, deixe o dataset montado (~3 GB) num SSD. Com o dataset no HD, a GPU fica ociosa esperando as imagens. Os downloads brutos podem continuar no HD. Use `treinar_tudo.bat --pasta-dataset C:\ArgosEPI\datasets\argos_epi_v1`. O caminho fica salvo no estado, então retomar depois não precisa repetir a opção.
- [Git](https://git-scm.com/) com Git LFS (vem junto no Git para Windows). É opcional, mas acelera muito: sem ele, os datasets do Hugging Face vão pela API, que limita cada IP a 3.000 requisições a cada 5 minutos e fica parada nesse limite quando o dataset tem dezenas de milhares de imagens. Com o git, cada fonte vem por `git clone`, e todas baixam em paralelo.
- Python 3.12 com PyTorch CUDA e as dependências desta pasta:

```bat
py -3.12 -m venv D:\ArgosEPI\venv
D:\ArgosEPI\venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
D:\ArgosEPI\venv\Scripts\python.exe -m pip install -r treinamento\requirements.txt
```

## Como rodar

```bat
treinamento\treinar_tudo.bat                          :: tudo, ou continua de onde parou
treinamento\treinar_tudo.bat --inicio professor       :: dataset já montado: só treina
treinamento\treinar_tudo.bat --nome teste --rapido    :: teste de fumaça (poucos minutos)
```

### Acompanhar em tempo real

`treinamento\acompanhar_treinamento.bat` abre um painel em http://127.0.0.1:8765. O painel mostra:
- o progresso geral e a previsão de término;
- a etapa e a época atuais;
- GPU e RAM;
- os gráficos de mAP e perdas por época;
- o resultado no teste, os pseudo-rótulos, os checkpoints e o log.

O servidor (`acompanhar_treinamento\servidor.py`) só lê os arquivos do treino, então abrir ou fechar o painel não afeta o treino.

### Checkpoints e quedas de energia

- O `pipeline.py` guarda as etapas concluídas e a etapa em andamento em `<base>\estado_<nome>.json`.
- Durante o treino, `runs\<nome>\weights\last.pt` é gravado a cada época, e uma cópia `epochN.pt` a cada 5 épocas (`--salvar-a-cada` no `treinar.py`).
- Se o PC desligar, rode `treinar_tudo.bat` **sem argumentos**. Ele pula as etapas prontas e retoma o treino da última época salva. Perde-se no máximo a época que estava em andamento.
- Se a queda aconteceu enquanto o `last.pt` era gravado e ele corrompeu, o treino retoma do `epochN.pt` mais recente.
- Se o treino já tinha terminado e a queda foi na avaliação, ele não treina de novo.
- Enquanto treina, o Windows não entra em suspensão por inatividade.
- `--inicio <etapa>` refaz a partir daquela etapa. Um treino já concluído é guardado como `runs\<nome>_anterior_<data>` antes de começar do zero; se a etapa foi interrompida no meio, ela é retomada.

| Etapa | O que faz | Tempo (RTX 4060 Ti) |
|---|---|---|
| `baixar` | Baixa as fontes de `fontes.py` | 1 a 2 h (depende da internet) |
| `montar` | Converte classes, limpa caixas, remove duplicatas e separa treino/validação/teste | 5 a 15 min |
| `professor` | Treina um primeiro modelo (40 épocas) | ~6 h (lote 16, ~8,5 min por época com 27 mil imagens de treino) |
| `pseudo` | O professor completa os EPIs que cada fonte não anotou | ~10 min |
| `final` | Treina o modelo final (120 épocas) e copia para `models/` | ~17 h (para antes se não melhorar por 40 épocas) |
| `avaliar` | Gera `avaliacao_<modelo>.md` com mAP comum e mAP justo por classe | ~5 min |

## Classes

A taxonomia fica em `backend/ppe_taxonomy.py` e é a mesma usada pelo servidor na inferência.

| EPI | Classe "tem" | Classe "não tem" |
|---|---|---|
| Capacete | `capacete` | `sem_capacete` |
| Colete refletivo | `colete` | `sem_colete` |
| Luvas | `luvas` | `sem_luvas` |
| Calçado de segurança | `botas` | `sem_botas` |
| Óculos de proteção | `oculos` | `sem_oculos` |
| Máscara / respirador | `mascara` | `sem_mascara` |
| Protetor auricular | `protetor_auricular` | — |
| Protetor facial | `protetor_facial` | — |
| Vestimenta de proteção | `vestimenta` | — |

"Pessoa" não é classe do detector: quem acha as pessoas é o modelo de pose (YOLO26-pose), que também dá os pontos do corpo usados para ligar cada EPI à pessoa certa.

## Fontes e licenças

| Fonte | Licença | O que traz |
|---|---|---|
| [Ultralytics Construction-PPE](https://docs.ultralytics.com/datasets/detect/construction-ppe/) | AGPL-3.0 | capacete, colete, luvas, botas, óculos e as classes "sem" |
| [PPE Detection (51ddhesh)](https://huggingface.co/datasets/51ddhesh/PPE_Detection) | CC BY 4.0 | colete, calçado, máscara, capacete, óculos, luvas |
| [PPE Dataset 3-Class (jhboyo)](https://huggingface.co/datasets/jhboyo/ppe-dataset) | MIT | 15,5 mil imagens de capacete, cabeça sem capacete e colete |
| [PPE Detection 1 (VincentGOURBIN)](https://huggingface.co/datasets/VincentGOURBIN/ppe-detection) | CC BY 4.0 | capacete, sem capacete, colete, sem colete |
| [Protective Equipment (keremberke)](https://huggingface.co/datasets/keremberke/protective-equipment-detection) | CC BY 4.0 | luvas, óculos, capacete, máscara, calçado, vestimenta e classes "sem" |
| [Construction Safety (keremberke)](https://huggingface.co/datasets/keremberke/construction-safety-object-detection) | CC BY 4.0 | capacete, colete, máscara, luvas, calçado |
| [SH17](https://github.com/ahmadmughees/SH17dataset) | **CC BY-NC-SA 4.0** | protetor auricular, protetor facial, máscara, capacete, colete, luvas, vestimenta (calçado e óculos ficam de fora: são de uso comum) |
| [PPEs 14 classes](https://universe.roboflow.com/object-detection/ppes-kaxsi) (espelho BIDJOE no HF) | CC BY 4.0 | só macacão; as outras classes têm anotação parcial |
| [PPE Detection 9p0pu](https://universe.roboflow.com/developing-automatic-surveillance-system-for-personal-protective-equipment-compliance-in-real-construction-sites-using-deep-neural-network-models/ppe-detection-9p0pu) (espelho Chapian no HF) | CC BY 4.0 | botas, sem botas, luvas, sem luvas, capacete, sem capacete, colete, sem colete |
| [CPPE-5](https://github.com/Rishit-dagli/CPPE-Dataset) | Apache-2.0 | macacão, protetor facial, luvas, óculos, máscara (EPI hospitalar) |
| [PPE (Amalia123)](https://universe.roboflow.com/amalia123/ppe.) · Roboflow | domínio público | abafador, calçado de segurança, colete, máscara, óculos, capacete, luvas |
| [Boots](https://universe.roboflow.com/construction-resources/boots-uzihq) · Roboflow | CC BY 4.0 | botas |
| [Safety shoe](https://universe.roboflow.com/new-workspace-xszud/safety-shoe) · Roboflow | CC BY 4.0 | botas e sem botas (sandálias, chinelos, tênis) |
| [PPE Dataset for Workplace Safety](https://universe.roboflow.com/russellwenner/ppe-dataset-for-workplace-safety-62mmn-a016k-lsqf6) · Roboflow | CC BY 4.0 | botas, capacete, colete |
| [Earplug use](https://universe.roboflow.com/lia-casco/combine-ypw05) · Roboflow | CC BY 4.0 | protetor auricular tipo plug |

As fontes marcadas com Roboflow precisam de uma chave de API gratuita (roboflow.com > Settings > API Keys). Defina a variável `ROBOFLOW_API_KEY` antes de rodar. Sem ela, essas fontes falham e o dataset é montado sem elas.

O SH17 é **não comercial**: modelos treinados com ele servem para pesquisa e para o TCC. Para uso comercial, rode com `--sem-nao-comercial` (sem ele, somem as classes de protetor auricular e protetor facial).

## Por que existem pseudo-rótulos

Cada dataset anota só alguns EPIs. Um marca capacete e colete, mas ignora as luvas que aparecem na foto. Se o treino juntar tudo direto, o modelo aprende que aquelas luvas são "fundo" e passa a errar luvas. A etapa `pseudo` usa o modelo professor para acrescentar, em cada imagem, só as classes que a fonte dela não anota, e só com confiança alta (0,5; 0,6 nas classes "sem"). Os rótulos originais ficam guardados em `labels_originais/` e o conjunto de teste não é alterado.

## mAP justo

Pelo mesmo motivo, o mAP comum pune o modelo por acertar luvas numa foto em que ninguém marcou luvas. O `avaliar.py` também calcula o **mAP justo**: cada classe é medida só nas imagens de teste de fontes que anotam aquela classe. Esse é o número a apresentar.

## Somando vídeos da própria empresa

O modo **super detalhado** guarda quadros duvidosos com pré-rótulos YOLO em `dados/users/<uid>/analises/<id>/revisao/`. Para usá-los no treino:

1. Corrija os rótulos (LabelImg, CVAT ou Roboflow, formato YOLO, classes na ordem de `DATASET_CLASSES`).
2. Coloque em `D:\ArgosEPI\datasets\raw\proprio\images\` e `...\labels\`.
3. Acrescente a fonte em `fontes.py`:

```python
{'id': 'proprio', 'nome': 'Vídeos da empresa', 'licenca': 'própria', 'info': '', 'tipo': 'local',
 'formato': 'yolo', 'nomes': __import__('ppe_taxonomy').DATASET_CLASSES},
```

4. Rode `treinar_tudo.bat --nome argos_epi_v2`.

## Arquivos

| Arquivo | Função |
|---|---|
| `pipeline.py` | Orquestra as etapas e retoma de onde parou |
| `fontes.py` | Lista de datasets, licenças e particularidades de cada um |
| `baixar_datasets.py` | Download paralelo das fontes (`git clone` no Hugging Face, HTTP com retomada nos zips) e extração |
| `montar_dataset.py` | Conversão de classes, limpeza, duplicatas (dHash) e divisão sem vazamento |
| `pseudo_rotular.py` | Completa rótulos faltantes com o modelo professor |
| `treinar.py` | Treino YOLO26 ou RT-DETR (`--arquitetura detr`) com retomada e cópia para `models/` |
| `avaliar.py` | mAP comum e justo por classe |
