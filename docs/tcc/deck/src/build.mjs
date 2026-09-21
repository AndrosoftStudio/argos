import { Canvas } from "skia-canvas";
import {
  Presentation,
  PresentationFile,
  layers,
  column,
  row,
  grid,
  text,
  shape,
  rule,
  fill,
  hug,
  fixed,
  wrap,
  fr,
  auto,
  drawSlideToCtx,
} from "@oai/artifact-tool";

const OUT = "output/output.pptx";
const W = 1920;
const H = 1080;

const C = {
  ink: "#101820",
  bg: "#F6F2EA",
  paper: "#FFFDF7",
  muted: "#59646E",
  line: "#C9D0D6",
  amber: "#FFB020",
  green: "#19A974",
  red: "#D94B4B",
  blue: "#2E6F95",
};

const titleStyle = { fontSize: 70, bold: true, color: C.ink, fontFace: "Aptos Display" };
const subtitleStyle = { fontSize: 30, color: C.muted, fontFace: "Aptos" };
const bodyStyle = { fontSize: 25, color: C.ink, fontFace: "Aptos" };
const smallStyle = { fontSize: 18, color: C.muted, fontFace: "Aptos" };

const presentation = Presentation.create({ slideSize: { width: W, height: H } });

function addSlide(bg = C.bg) {
  const slide = presentation.slides.add();
  slide._bgColor = bg;
  return slide;
}

function compose(slide, root) {
  slide.compose(
    layers({ width: fill, height: fill }, [
      shape({ name: "slide-bg", width: fill, height: fill, fill: slide._bgColor || C.bg }),
      root,
    ]),
    { frame: { left: 0, top: 0, width: W, height: H }, baseUnit: 8 },
  );
}

function eyebrow(value, color = C.blue) {
  return row({ width: fill, height: hug, gap: 12 }, [
    shape({ width: fixed(70), height: fixed(8), fill: color }),
    text(value, { width: wrap(900), height: hug, style: { ...smallStyle, bold: true, color } }),
  ]);
}

function titleBlock(kicker, title, subtitle, accent = C.blue) {
  return column({ width: fill, height: hug, gap: 22 }, [
    eyebrow(kicker, accent),
    text(title, { name: "slide-title", width: wrap(1420), height: hug, style: titleStyle }),
    subtitle
      ? text(subtitle, { name: "slide-subtitle", width: wrap(1180), height: hug, style: subtitleStyle })
      : null,
  ].filter(Boolean));
}

function bullets(items, opts = {}) {
  return column({ width: fill, height: hug, gap: opts.gap || 18 }, items.map((item, i) =>
    row({ width: fill, height: hug, gap: 16 }, [
      shape({ width: fixed(12), height: fixed(12), fill: opts.color || C.amber }),
      text(item, { width: wrap(opts.width || 760), height: hug, style: { ...bodyStyle, fontSize: opts.fontSize || 25 } }),
    ])
  ));
}

function metric(value, label, color) {
  return column({ width: fill, height: hug, gap: 4 }, [
    text(value, { width: fill, height: hug, style: { fontSize: 54, bold: true, color, fontFace: "Aptos Display" } }),
    text(label, { width: fill, height: hug, style: { ...smallStyle, color: C.ink } }),
  ]);
}

function step(number, label, color) {
  return row({ width: fill, height: hug, gap: 18 }, [
    text(number, { width: fixed(72), height: hug, style: { fontSize: 46, bold: true, color, fontFace: "Aptos Display" } }),
    text(label, { width: fill, height: hug, style: { ...bodyStyle, fontSize: 28, bold: true } }),
  ]);
}

// 1
{
  const slide = addSlide(C.ink);
  compose(slide,
    column({ name: "cover-root", width: fill, height: fill, padding: { x: 120, y: 92 }, gap: 34 }, [
      text("SENAI Lauro de Freitas - TCC em Desenvolvimento de Sistemas", {
        width: fill, height: hug, style: { fontSize: 23, color: "#D7E0E6", fontFace: "Aptos" },
      }),
      shape({ width: fixed(240), height: fixed(10), fill: C.amber }),
      text("Vigilancia EPI", {
        width: wrap(1320), height: hug, style: { fontSize: 128, bold: true, color: "#FFF7EA", fontFace: "Aptos Display" },
      }),
      text("Cameras comuns ajudando a prevenir acidentes antes que eles acontecam.", {
        width: wrap(1160), height: hug, style: { fontSize: 40, color: "#C7D5DD", fontFace: "Aptos" },
      }),
      rule({ width: fixed(620), stroke: "#365466", weight: 2 }),
      row({ width: fill, height: hug, gap: 72 }, [
        metric("tempo real", "alerta durante a operacao", C.amber),
        metric("multi-backend", "varios servidores registrados", C.green),
        metric("API", "integracao com empresas", "#7FC8F8"),
      ]),
    ])
  );
}

// 2
{
  const slide = addSlide();
  compose(slide,
    column({ width: fill, height: fill, padding: { x: 96, y: 78 }, gap: 52 }, [
      titleBlock("O problema", "Fiscalizar EPI o tempo inteiro nao escala.", "A falha costuma ser simples; a consequencia pode ser grave.", C.red),
      row({ width: fill, height: fill, gap: 78 }, [
        bullets([
          "A observacao manual depende de alguem estar olhando no momento certo.",
          "Cameras ja existem em muitas empresas, mas geralmente so gravam.",
          "A equipe de seguranca precisa de apoio, nao de mais uma tarefa manual.",
        ], { width: 860, color: C.red }),
        column({ width: fill, height: hug, gap: 30 }, [
          metric("1 descuido", "pode virar acidente, afastamento e custo", C.red),
          metric("varias areas", "exigem atencao simultanea", C.blue),
          metric("pouco registro", "dificulta analise posterior", C.amber),
        ]),
      ]),
    ])
  );
}

// 3
{
  const slide = addSlide();
  compose(slide,
    column({ width: fill, height: fill, padding: { x: 96, y: 78 }, gap: 56 }, [
      titleBlock("A solucao", "O sistema olha junto com a equipe.", "O Vigilancia EPI transforma camera em alerta operacional.", C.green),
      grid({ width: fill, height: fill, columns: [fr(1), fr(1), fr(1)], columnGap: 42 }, [
        column({ width: fill, height: hug, gap: 16 }, [
          metric("Detecta", "pessoa e EPI ausente", C.green),
          text("O painel muda para perigo e informa o item faltante.", { width: fill, height: hug, style: bodyStyle }),
        ]),
        column({ width: fill, height: hug, gap: 16 }, [
          metric("Reconhece", "funcionario cadastrado", C.blue),
          text("O modelo de funcionarios aparece como camada extra de identificacao.", { width: fill, height: hug, style: bodyStyle }),
        ]),
        column({ width: fill, height: hug, gap: 16 }, [
          metric("Integra", "dados por API", C.amber),
          text("Empresas podem consultar status, funcionarios e EPIs ausentes.", { width: fill, height: hug, style: bodyStyle }),
        ]),
      ]),
    ])
  );
}

// 4
{
  const slide = addSlide(C.paper);
  compose(slide,
    column({ width: fill, height: fill, padding: { x: 96, y: 78 }, gap: 76 }, [
      titleBlock("Como funciona", "Camera, IA, dashboard e API.", "Quatro etapas ate o alerta.", C.blue),
      row({ width: fill, height: hug, gap: 34 }, [
        step("01", "Camera envia frames", C.blue),
        step("02", "Backend processa com IA", C.green),
        step("03", "Dashboard recebe status", C.amber),
        step("04", "API libera integracao", C.red),
      ]),
      rule({ width: fill, stroke: C.line, weight: 2 }),
      text("No prototipo, o navegador envia frames em duas rotas paralelas. O backend consome o frame mais recente, roda o modelo YOLO ou TensorRT e retorna imagem anotada, FPS, alertas e deteccoes.", {
        width: wrap(1460), height: hug, style: { ...bodyStyle, fontSize: 31 },
      }),
    ])
  );
}

// 5
{
  const slide = addSlide();
  compose(slide,
    column({ width: fill, height: fill, padding: { x: 96, y: 78 }, gap: 52 }, [
      titleBlock("Prototipo", "O TCC entrega um fluxo demonstravel.", "Login, cadastro, camera, IA, alerta e relatorio.", C.amber),
      row({ width: fill, height: fill, gap: 70 }, [
        bullets([
          "Cadastro de EPIs e fotos.",
          "Treino de modelo por EPI.",
          "Funcionarios com fotos.",
          "Camera local, remota ou video.",
          "Status por stream.",
        ], { width: 820, color: C.amber, fontSize: 24, gap: 26 }),
        column({ width: fill, height: hug, gap: 24 }, [
          metric("POC validada", "camera + inferencia + dashboard", C.green),
          metric("Dados por usuario", "modelos, fotos e metadados separados", C.blue),
          metric("Pronto para banca", "video, slides, canvases e requisitos", C.red),
        ]),
      ]),
    ])
  );
}

// 6
{
  const slide = addSlide(C.ink);
  compose(slide,
    column({ width: fill, height: fill, padding: { x: 96, y: 78 }, gap: 42 }, [
      column({ width: fill, height: hug, gap: 22 }, [
        row({ width: fill, height: hug, gap: 12 }, [
          shape({ width: fixed(70), height: fixed(8), fill: C.amber }),
          text("Arquitetura", { width: fixed(260), height: hug, style: { ...smallStyle, bold: true, color: C.amber } }),
        ]),
        text("Hub central para backends.", {
          width: wrap(1400), height: hug, style: { fontSize: 60, bold: true, color: "#FFF7EA", fontFace: "Aptos Display" },
        }),
        text("O frontend consulta o Render e usa o backend mais disponivel.", {
          width: wrap(1180), height: hug, style: { fontSize: 30, color: "#D7E0E6", fontFace: "Aptos" },
        }),
      ]),
      grid({ width: fill, height: fill, columns: [fr(1), fr(1), fr(1)], columnGap: 48 }, [
        column({ width: fill, height: hug, gap: 14 }, [
          text("Frontend", { width: fill, height: hug, style: { fontSize: 48, bold: true, color: "#FFF7EA" } }),
          text("Vercel ou navegador. Pede a lista ao hub e conversa com o melhor backend.", { width: fill, height: hug, style: { ...bodyStyle, color: "#D7E0E6" } }),
        ]),
        column({ width: fill, height: hug, gap: 14 }, [
          text("Hub", { width: fill, height: hug, style: { fontSize: 48, bold: true, color: C.amber } }),
          text("Render. Guarda URL, ping, hardware, carga e disponibilidade dos backends.", { width: fill, height: hug, style: { ...bodyStyle, color: "#D7E0E6" } }),
        ]),
        column({ width: fill, height: hug, gap: 14 }, [
          text("Backends", { width: fill, height: hug, style: { fontSize: 48, bold: true, color: C.green } }),
          text("Maquinas locais ou remotas via Cloudflare. Podem ser CPU, DML ou NVIDIA.", { width: fill, height: hug, style: { ...bodyStyle, color: "#D7E0E6" } }),
        ]),
      ]),
    ])
  );
}

// 7
{
  const slide = addSlide();
  compose(slide,
    column({ width: fill, height: fill, padding: { x: 96, y: 78 }, gap: 46 }, [
      titleBlock("Desempenho", "Quando houver NVIDIA, o modelo vira TensorRT.", "A opcao fica automatica no backend compativel.", C.green),
      row({ width: fill, height: hug, gap: 80 }, [
        metric(".pt", "modelo treinado ou enviado", C.blue),
        text("->", { width: fixed(80), height: hug, style: { fontSize: 58, bold: true, color: C.muted } }),
        metric(".engine", "otimizado para NVIDIA", C.green),
        text("->", { width: fixed(80), height: hug, style: { fontSize: 58, bold: true, color: C.muted } }),
        metric("FPS", "menor latencia na inferencia", C.amber),
      ]),
      bullets([
        "Upload ou treino de modelo pode iniciar conversao em segundo plano.",
        "Na inferencia, o backend NVIDIA prefere o arquivo `.engine`.",
        "Se a engine falhar, o sistema volta para o `.pt` original.",
      ], { width: 1300, color: C.green, fontSize: 28 }),
    ])
  );
}

// 8
{
  const slide = addSlide(C.paper);
  compose(slide,
    column({ width: fill, height: fill, padding: { x: 96, y: 78 }, gap: 48 }, [
      titleBlock("Integracao", "A empresa pode consumir as deteccoes por API.", "O alerta vira dado para outros sistemas.", C.blue),
      row({ width: fill, height: fill, gap: 76 }, [
        column({ width: fill, height: hug, gap: 18 }, [
          text("Endpoints principais", { width: fill, height: hug, style: { fontSize: 38, bold: true, color: C.ink } }),
          bullets([
            "GET /api/v1/streams",
            "GET /api/v1/streams/{id}/detections",
            "GET /api/v1/funcionarios",
            "GET /api/v1/epis",
          ], { width: 760, color: C.blue, fontSize: 24 }),
        ]),
        column({ width: fill, height: hug, gap: 18 }, [
          text("Exemplo de retorno", { width: fill, height: hug, style: { fontSize: 38, bold: true, color: C.ink } }),
          text("funcionario identificado\nEPI ausente: capacete\nstatus: perigo\nruntime: tensorrt", {
            width: fill, height: hug, style: { fontSize: 31, bold: true, color: C.red, fontFace: "Aptos Mono" },
          }),
        ]),
      ]),
    ])
  );
}

// 9
{
  const slide = addSlide();
  compose(slide,
    column({ width: fill, height: fill, padding: { x: 96, y: 78 }, gap: 44 }, [
      titleBlock("Viabilidade", "A POC comeca barata e cresce por necessidade.", "O custo depende de cameras, hardware e suporte.", C.amber),
      grid({ width: fill, height: fill, columns: [fr(1), fr(1), fr(1)], columnGap: 44 }, [
        column({ width: fill, height: hug, gap: 18 }, [
          metric("Basico", "ate 2 cameras", C.green),
          text("Frontend e hub em planos gratuitos; backend local com camera existente.", { width: fill, height: hug, style: bodyStyle }),
        ]),
        column({ width: fill, height: hug, gap: 18 }, [
          metric("Profissional", "mais cameras", C.blue),
          text("PC dedicado, cameras IP e assinatura por quantidade de streams.", { width: fill, height: hug, style: bodyStyle }),
        ]),
        column({ width: fill, height: hug, gap: 18 }, [
          metric("API", "integracao", C.amber),
          text("Plano para empresas que querem enviar alertas a sistemas internos.", { width: fill, height: hug, style: bodyStyle }),
        ]),
      ]),
    ])
  );
}

// 10
{
  const slide = addSlide(C.ink);
  compose(slide,
    column({ width: fill, height: fill, padding: { x: 120, y: 90 }, gap: 40 }, [
      text("Seguranca do trabalho nao deve depender da sorte de alguem estar olhando no momento certo.", {
        width: wrap(1500), height: hug, style: { fontSize: 76, bold: true, color: "#FFF7EA", fontFace: "Aptos Display" },
      }),
      shape({ width: fixed(320), height: fixed(10), fill: C.amber }),
      text("O Vigilancia EPI coloca a tecnologia para olhar junto: detectando risco, apoiando a equipe e criando registro para decisao.", {
        width: wrap(1320), height: hug, style: { fontSize: 34, color: "#D7E0E6", fontFace: "Aptos" },
      }),
      row({ width: fill, height: hug, gap: 80 }, [
        metric("proximo passo", "testes com dataset real", C.amber),
        metric("melhoria", "privacidade e auditoria", C.green),
        metric("escala", "piloto em ambiente real", "#7FC8F8"),
      ]),
    ])
  );
}

const pptxBlob = await PresentationFile.exportPptx(presentation);
await pptxBlob.save(OUT);

for (let i = 0; i < presentation.slides.items.length; i++) {
  const slide = presentation.slides.items[i];
  const canvas = new Canvas(W, H);
  const ctx = canvas.getContext("2d");
  await drawSlideToCtx(slide, presentation, ctx);
  const path = `scratch/slide-${String(i + 1).padStart(2, "0")}.png`;
  await canvas.toFile(path);
}

console.log(JSON.stringify({
  pptx: OUT,
  previews: presentation.slides.items.length,
}, null, 2));
