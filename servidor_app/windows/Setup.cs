// Argos EPI Servidor - instalador (ArgosEPI-Servidor-Setup.exe). Nao precisa de administrador.
// Pequeno: le o latest.json da pasta de downloads (R2) e baixa so o que a maquina precisa, tudo ja compilado
// (so .exe, .dll/.pyd e arquivos de modelo; nenhum codigo-fonte e nada para baixar depois de instalado):
//   win/argos-programa-<versao>-<placa>.zip   janela (ArgosEPI.exe), codigo.pak (o Argos compilado) e recursos.pak: ~3 MB
//   win/argos-nucleo-<placa>-<id>.zip         motor\ArgosMotor.exe: o Python e as bibliotecas em Python, sem o codigo do Argos
//   win/argos-motor-<placa>-<id>.zip          bibliotecas do motor (PyTorch, OpenCV... em .dll), por placa:
//                                             nvidia (CUDA + TensorRT), dml (AMD/Intel com DirectML) ou cpu
//   win/argos-base-<id>.zip                   modelos (YOLO e rosto), PostgreSQL e cloudflared
//   win/argos-midia-<id>.zip                  midia\: video direto (MediaMTX) e voz dos avisos (Piper + voz em portugues)
// O nucleo, o motor e a base so sao baixados de novo quando mudam (id): atualizar baixa so o programa (~3 MB).
// No latest.json o programa fica em windows.programa2: a chave antiga (programa) trazia o ArgosMotor.exe
// dentro, e um instalador antigo lendo o formato novo instalaria pela metade; sem ela, ele pede o instalador novo.
// Download rapido: cada arquivo vem por 8 conexoes ao mesmo tempo (a que fica lenta perde o resto do
// trecho para as rapidas) e continua de onde parou. Sem internet boa: copie o instalador, o latest.json e a
// pasta win\ para um pendrive (montar_pendrive.ps1) e abra o instalador de la: ele instala dos arquivos ao lado.
// Atualizar = rodar de novo (ou o botao "Atualizar" do programa, que chama com --atualizar <pasta>).
// Instalar por cima limpa a pasta: so ficam dados\ (banco, rostos, gravacoes), .env, models\ e uploads\;
// o resto (inclusive o Python solto e o codigo das versoes antigas) e apagado.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

namespace ArgosEPI.Instalador
{
    static class Principal
    {
        public const string Nome = "Argos EPI Servidor";
        public const string FontePadrao = "https://pub-6b5befc214654d93bdc0345875151ea0.r2.dev/argosepi-discovery/";

        [STAThread]
        static void Main(string[] args)
        {
            ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12 | (SecurityProtocolType)12288; // TLS 1.2 e 1.3
            ServicePointManager.DefaultConnectionLimit = 32;   // o padrao (2) anularia o download em paralelo
            ServicePointManager.Expect100Continue = false;
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            string fonte = Arg(args, "--fonte");
            if (fonte == null)
            {
                // pendrive ou pasta da rede: com o latest.json e a pasta win\ ao lado, instala dali (sem internet)
                var aqui = Path.GetDirectoryName(Application.ExecutablePath);
                fonte = File.Exists(Path.Combine(aqui, "latest.json")) && Directory.Exists(Path.Combine(aqui, "win")) ? aqui : FontePadrao;
            }
            if (fonte.Contains("://")) { if (!fonte.EndsWith("/")) fonte += "/"; }
            else fonte = Path.GetFullPath(fonte).TrimEnd('\\') + "\\";
            // --portatil: so poe os arquivos na pasta (sem atalhos, sem registro no Windows e sem abrir no fim)
            Application.Run(new Tela(fonte, Arg(args, "--atualizar"), Arg(args, "--variante")) { Portatil = args.Contains("--portatil") });
        }

        static string Arg(string[] a, string nome)
        {
            int i = Array.IndexOf(a, nome);
            return i >= 0 && i + 1 < a.Length ? a[i + 1] : null;
        }
    }

    // ---------------------------------------------------------------- tela
    public class Tela : Form
    {
        static readonly Color Fundo = Color.FromArgb(8, 10, 29), Cartao = Color.FromArgb(22, 26, 56), Borda = Color.FromArgb(52, 59, 112),
            Texto = Color.FromArgb(233, 235, 251), Suave = Color.FromArgb(167, 176, 228), Ouro = Color.FromArgb(251, 195, 67),
            Marinho = Color.FromArgb(5, 7, 90), Azul = Color.FromArgb(77, 88, 216), Perigo = Color.FromArgb(255, 107, 107);

        readonly string fonte, pastaAtualizar, varianteArg;
        public bool Portatil;
        Dictionary<string, object> manifesto;
        readonly Placa placa = Placa.Detectar();
        string variante = "cpu";

        Panel pgOpcoes, pgProgresso;
        Label lblVersao, lblPlaca, lblEspaco, lblEtapa, lblDetalhe, lblFim;
        readonly Dictionary<string, RadioButton> radios = new Dictionary<string, RadioButton>();
        TextBox txtPasta, txtLog;
        CheckBox chkIniciar, chkAtalho, chkAbrir;
        Button btnInstalar, btnConcluir, btnTentar;
        ProgressBar barra;
        LinkLabel lnkDetalhes;
        readonly CancellationTokenSource cancelar = new CancellationTokenSource();
        // Atualizacao pedida pelo programa: so aparece uma janelinha com o andamento; esta tela grande
        // fica invisivel e so volta se algo der errado (para mostrar o erro e o botao de tentar de novo).
        JanelaAtualizando mini;

        public Tela(string fonte, string pastaAtualizar, string varianteArg)
        {
            this.fonte = fonte;
            this.pastaAtualizar = pastaAtualizar;
            this.varianteArg = varianteArg;
            Text = "Instalar o " + Principal.Nome;
            Font = new Font("Segoe UI", 10f);
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Fundo; ForeColor = Texto;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
            ClientSize = new Size(760, 600);
            MontarOpcoes();
            MontarProgresso();
            Controls.Add(pgProgresso);
            Controls.Add(pgOpcoes);
            AutoScaleDimensions = new SizeF(96F, 96F);
            AutoScaleMode = AutoScaleMode.Dpi;
            Shown += async (s, e) => await CarregarManifesto();
            FormClosing += (s, e) => cancelar.Cancel();
            FormClosed += (s, e) => { if (mini != null) mini.Fechar(); };
            if (pastaAtualizar != null)
            {
                Opacity = 0;
                ShowInTaskbar = false;
                FormBorderStyle = FormBorderStyle.FixedToolWindow;      // fora do Alt+Tab enquanto invisivel
                mini = new JanelaAtualizando(Icon);
                mini.Show();
            }
        }

        // algo deu errado na atualizacao: fecha a janelinha e mostra a tela grande, com os detalhes
        void Revelar()
        {
            if (mini == null) return;
            mini.Fechar();
            mini = null;
            FormBorderStyle = FormBorderStyle.FixedSingle;
            ShowInTaskbar = true;
            Opacity = 1;
            CenterToScreen();
            Activate();
        }

        // espera a janela do programa aparecer (ate 15 s) para a janelinha nao sumir antes dela
        async Task EsperarJanela(string pasta)
        {
            var exe = Path.Combine(pasta, "ArgosEPI.exe");
            for (int i = 0; i < 60; i++)
            {
                await Task.Delay(250);
                try
                {
                    foreach (var p in Process.GetProcessesByName("ArgosEPI"))
                    {
                        string caminho = null;
                        try { caminho = p.MainModule.FileName; } catch { }
                        if (caminho != null && string.Equals(caminho, exe, StringComparison.OrdinalIgnoreCase) && p.MainWindowHandle != IntPtr.Zero) return;
                    }
                }
                catch { }
            }
        }

        // ---------- pagina 1: escolhas ----------
        void MontarOpcoes()
        {
            pgOpcoes = new Panel { Dock = DockStyle.Fill };
            var logo = new PictureBox { Size = new Size(64, 64), Location = new Point(30, 24), SizeMode = PictureBoxSizeMode.Zoom };
            try { logo.Image = new Icon(Icon, 64, 64).ToBitmap(); } catch { }
            var titulo = new Label { Text = "ARGOS", Font = new Font(FonteTitulo(), 30f, FontStyle.Bold), AutoSize = true, Location = new Point(104, 20), ForeColor = Color.White };
            var selo = new Label { Text = "SERVIDOR", Font = new Font(FonteTitulo(), 10f, FontStyle.Bold), AutoSize = true, BackColor = Ouro, ForeColor = Marinho,
                Padding = new Padding(5, 2, 5, 2), Location = new Point(250, 36) };
            lblVersao = new Label { Text = "Procurando a versão mais nova...", ForeColor = Suave, AutoSize = true, Location = new Point(107, 70) };

            var cab = Titulo("Placa de vídeo deste computador", 112);
            lblPlaca = new Label { Text = placa.Resumo, ForeColor = Suave, AutoSize = false, Size = new Size(700, 22), Location = new Point(30, 138), Font = new Font("Segoe UI", 9f) };
            int y = 166;
            foreach (var v in new[] {
                new[] { "nvidia", "NVIDIA (CUDA)", "O mais rápido. Para placas GeForce, RTX e Quadro." },
                new[] { "dml", "AMD ou Intel (DirectML)", "Usa a placa de vídeo AMD Radeon ou Intel Arc/Iris pelo DirectX 12." },
                new[] { "cpu", "Só o processador (CPU)", "Funciona em qualquer computador; mais lento com várias câmeras." } })
            {
                var r = new RadioButton { Text = v[1], Tag = v[0], Location = new Point(42, y), AutoSize = true, Font = new Font("Segoe UI Semibold", 10.5f), ForeColor = Texto };
                var d = new Label { Text = v[2], ForeColor = Suave, AutoSize = true, Location = new Point(62, y + 25), Font = new Font("Segoe UI", 9f) };
                r.CheckedChanged += (s, e) => { if (((RadioButton)s).Checked) { variante = (string)((RadioButton)s).Tag; AtualizarEspaco(); } };
                radios[v[0]] = r;
                pgOpcoes.Controls.Add(r); pgOpcoes.Controls.Add(d);
                y += 56;
            }
            var cabPasta = Titulo("Onde instalar", y + 6);
            txtPasta = new TextBox { Location = new Point(30, y + 34), Width = 572, BackColor = Cartao, ForeColor = Texto, BorderStyle = BorderStyle.FixedSingle,
                Text = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", Principal.Nome) };
            txtPasta.TextChanged += (s, e) => AtualizarEspaco();
            var btnPasta = Botao("Procurar...", new Point(612, y + 32), 118, false);
            btnPasta.Click += (s, e) =>
            {
                using (var f = new FolderBrowserDialog { Description = "Pasta onde o " + Principal.Nome + " vai ficar" })
                    if (f.ShowDialog(this) == DialogResult.OK) txtPasta.Text = f.SelectedPath.EndsWith(Principal.Nome) ? f.SelectedPath : Path.Combine(f.SelectedPath, Principal.Nome);
            };
            lblEspaco = new Label { ForeColor = Suave, AutoSize = false, Size = new Size(700, 20), Location = new Point(30, y + 64), Font = new Font("Segoe UI", 9f) };
            chkIniciar = Marcar("Iniciar junto com o Windows, em segundo plano (as câmeras voltam sozinhas)", new Point(30, y + 94), true);
            chkAtalho = Marcar("Atalho na Área de Trabalho", new Point(30, y + 122), true);

            btnInstalar = Botao("Instalar", new Point(600, 540), 130, true);
            btnInstalar.Enabled = false;
            btnInstalar.Click += async (s, e) => await Instalar();
            var btnCancelar = Botao("Cancelar", new Point(480, 540), 110, false);
            btnCancelar.Click += (s, e) => Close();
            var aviso = new Label { Text = "Não precisa de administrador. A pasta dados (banco e fotos) nunca é apagada ao atualizar.", ForeColor = Suave,
                AutoSize = false, Size = new Size(440, 40), Location = new Point(30, 538), Font = new Font("Segoe UI", 8.5f) };
            pgOpcoes.Controls.AddRange(new Control[] { logo, titulo, selo, lblVersao, cab, lblPlaca, cabPasta, txtPasta, btnPasta, lblEspaco, chkIniciar, chkAtalho, btnInstalar, btnCancelar, aviso });

            var pre = varianteArg ?? DoNome() ?? placa.Modo;
            if (!radios.ContainsKey(pre)) pre = "cpu";
            radios[pre].Checked = true;
            variante = pre;
        }

        // o site baixa o mesmo instalador com nomes diferentes: ...-nvidia.exe, ...-amd-intel.exe, ...-cpu.exe
        static string DoNome()
        {
            var n = Path.GetFileNameWithoutExtension(Application.ExecutablePath).ToLowerInvariant();
            if (n.Contains("nvidia")) return "nvidia";
            if (n.Contains("amd") || n.Contains("intel") || n.Contains("dml")) return "dml";
            if (n.Contains("cpu")) return "cpu";
            return null;
        }

        // ---------- pagina 2: progresso ----------
        void MontarProgresso()
        {
            pgProgresso = new Panel { Dock = DockStyle.Fill, Visible = false };
            lblEtapa = new Label { Text = "Instalando...", Font = new Font("Segoe UI Semibold", 14f), AutoSize = true, Location = new Point(30, 30) };
            barra = new ProgressBar { Location = new Point(30, 74), Size = new Size(700, 10), Maximum = 1000, Style = ProgressBarStyle.Continuous };
            lblDetalhe = new Label { Text = "", ForeColor = Suave, AutoSize = false, Size = new Size(700, 40), Location = new Point(30, 94) };
            lnkDetalhes = new LinkLabel { Text = "Mostrar detalhes", AutoSize = true, Location = new Point(30, 140), LinkColor = Suave, ActiveLinkColor = Texto };
            txtLog = new TextBox { Location = new Point(30, 166), Size = new Size(700, 330), Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical,
                BackColor = Color.FromArgb(10, 12, 34), ForeColor = Color.Gainsboro, BorderStyle = BorderStyle.None, Font = new Font("Consolas", 9f), Visible = false };
            lnkDetalhes.LinkClicked += (s, e) => { txtLog.Visible = !txtLog.Visible; lnkDetalhes.Text = txtLog.Visible ? "Esconder detalhes" : "Mostrar detalhes"; };
            lblFim = new Label { Text = "", ForeColor = Texto, AutoSize = false, Size = new Size(700, 60), Location = new Point(30, 166), Visible = false };
            chkAbrir = Marcar("Abrir o " + Principal.Nome + " agora", new Point(30, 546), true);
            chkAbrir.Visible = false;
            btnConcluir = Botao("Concluir", new Point(600, 540), 130, true);
            btnConcluir.Visible = false;
            btnConcluir.Click += (s, e) =>
            {
                if (chkAbrir.Checked && chkAbrir.Visible) Abrir(txtPasta.Text.Trim());
                Close();
            };
            btnTentar = Botao("Tentar de novo", new Point(450, 540), 140, false);
            btnTentar.Visible = false;
            btnTentar.Click += async (s, e) => { btnTentar.Visible = false; btnConcluir.Visible = false; await Instalar(); };
            pgProgresso.Controls.AddRange(new Control[] { lblEtapa, barra, lblDetalhe, lnkDetalhes, lblFim, txtLog, chkAbrir, btnConcluir, btnTentar });
        }

        static string FonteTitulo()
        {
            using (var f = new System.Drawing.Text.InstalledFontCollection())
                return f.Families.Any(x => x.Name == "Bahnschrift") ? "Bahnschrift" : "Segoe UI";
        }

        Label Titulo(string t, int y)
        {
            return new Label { Text = t, Font = new Font("Segoe UI Semibold", 11f), ForeColor = Ouro, AutoSize = true, Location = new Point(30, y) };
        }

        Button Botao(string t, Point p, int w, bool principal)
        {
            var b = new Button { Text = t, Location = p, Size = new Size(w, 38), FlatStyle = FlatStyle.Flat, Cursor = Cursors.Hand,
                BackColor = principal ? Ouro : Cartao, ForeColor = principal ? Marinho : Texto, Font = new Font("Segoe UI Semibold", 10f) };
            b.FlatAppearance.BorderColor = principal ? Ouro : Borda;
            return b;
        }

        CheckBox Marcar(string t, Point p, bool v)
        {
            return new CheckBox { Text = t, Location = p, AutoSize = true, Checked = v, ForeColor = Texto };
        }

        // ---------- manifesto ----------
        async Task CarregarManifesto()
        {
            try
            {
                var txt = await Task.Run(() =>
                {
                    if (FonteLocal) return File.ReadAllText(fonte + "latest.json", Encoding.UTF8);
                    using (var wc = new WebClient { Encoding = Encoding.UTF8 }) return wc.DownloadString(fonte + "latest.json?n=" + DateTime.UtcNow.Ticks);
                });
                manifesto = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(txt);
                lblVersao.Text = "Versão " + Str(manifesto, "versao") + " · servidor de câmeras com IA para EPIs";
                if (mini != null) mini.Versao("versão " + Str(manifesto, "versao"));
                var w = Obj(manifesto, "windows");
                if (Obj(w, "programa2") == null || Obj(w, "nucleo") == null || Obj(w, "base") == null)
                    throw new Exception("este instalador é de outra versão: baixe o instalador de novo pelo site");
                foreach (var kv in radios)
                {
                    if (Obj(Obj(w, "programa2"), kv.Key) == null || Obj(Obj(w, "nucleo"), kv.Key) == null || Obj(Obj(w, "motor"), kv.Key) == null) { kv.Value.Enabled = false; continue; }
                    kv.Value.Text += "  ·  " + Tam(Soma(kv.Key, "tamanho"));
                }
                if (!radios[variante].Enabled) radios.Values.First(r => r.Enabled).Checked = true;
                btnInstalar.Enabled = true;
                AtualizarEspaco();
                if (pastaAtualizar != null) { txtPasta.Text = pastaAtualizar; LerInstalacao(pastaAtualizar); await Instalar(); }
            }
            catch (Exception ex)
            {
                lblVersao.Text = FonteLocal ? "Não consegui ler os arquivos de instalação desta pasta (latest.json)."
                                            : "Sem acesso aos arquivos de instalação. Confira a internet e abra de novo.";
                lblVersao.ForeColor = Perigo;
                Log("latest.json: " + ex.Message);
                Revelar();
            }
        }

        void LerInstalacao(string pasta)
        {
            try
            {
                var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(pasta, "instalacao.json")));
                var v = Str(j, "variante");
                if (v != null && radios.ContainsKey(v) && radios[v].Enabled) radios[v].Checked = true;
                chkAtalho.Checked = File.Exists(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), Principal.Nome + ".lnk"));
                chkIniciar.Checked = false;   // na atualizacao o ajuste do programa continua valendo
            }
            catch { }
        }

        void AtualizarEspaco()
        {
            if (manifesto == null || lblEspaco == null) return;
            long baixar = Soma(variante, "tamanho"), ocupa = Soma(variante, "descompactado");
            string livre = "";
            try { livre = " · livre no disco: " + Tam(new DriveInfo(Path.GetPathRoot(Path.GetFullPath(txtPasta.Text))).AvailableFreeSpace); } catch { }
            lblEspaco.Text = "Baixa " + Tam(baixar) + " e ocupa cerca de " + Tam(ocupa) + livre;
        }

        // programa + nucleo + bibliotecas do motor + base + midia, para a placa escolhida
        long Soma(string placa, string campo)
        {
            var w = Obj(manifesto, "windows");
            return Num(Obj(Obj(w, "programa2"), placa), campo) + Num(Obj(Obj(w, "nucleo"), placa), campo)
                 + Num(Obj(Obj(w, "motor"), placa), campo) + Num(Obj(w, "base"), campo) + Num(Obj(w, "midia"), campo);
        }

        // so instala em pasta vazia ou onde o Argos ja esta (a limpeza apagaria arquivos de outra coisa)
        static bool PastaServe(string pasta)
        {
            if (!Directory.Exists(pasta) || !Directory.EnumerateFileSystemEntries(pasta).Any()) return true;
            return File.Exists(Path.Combine(pasta, "ArgosEPI.exe")) || File.Exists(Path.Combine(pasta, "instalacao.json"));
        }

        // ---------- instalacao ----------
        async Task Instalar()
        {
            var pasta = txtPasta.Text.Trim().TrimEnd('\\');
            if (pasta.Length < 4) { MessageBox.Show(this, "Escolha uma pasta de instalação."); return; }
            if (!PastaServe(pasta))
            {
                MessageBox.Show(this, "Essa pasta já tem outros arquivos.\n\nEscolha uma pasta vazia (ou a pasta onde o " + Principal.Nome + " já está instalado).",
                    Principal.Nome, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            if (variante == "nvidia" && !placa.Nvidia && pastaAtualizar == null &&
                MessageBox.Show(this, "Não encontrei placa NVIDIA neste computador. Instalar a versão NVIDIA mesmo assim?\n\n(Sem a placa, ela usa o processador.)",
                    Principal.Nome, MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) return;
            pgOpcoes.Visible = false;
            pgProgresso.Visible = true;
            lblFim.Visible = false;
            bool ok = false;
            string erro = null;
            bool iniciar = chkIniciar.Checked, atalho = chkAtalho.Checked;
            try { await Task.Run(() => Executar(pasta, iniciar, atalho, cancelar.Token)); ok = true; }
            catch (OperationCanceledException) { return; }
            catch (Exception ex) { erro = ex.Message; Log("ERRO: " + ex); }
            barra.Value = ok ? 1000 : barra.Value;
            lblEtapa.Text = ok ? (pastaAtualizar != null ? "Atualizado!" : "Pronto! O " + Principal.Nome + " está instalado.") : "A instalação não terminou";
            lblDetalhe.Text = ok ? "Tudo já veio instalado (programa, bibliotecas e modelos): nada mais é baixado. Agora é só abrir e vincular à sua conta pela janela do programa."
                                 : erro + "\nO que já foi baixado fica guardado: tentar de novo continua de onde parou.";
            lblDetalhe.ForeColor = ok ? Suave : Perigo;
            if (!ok) { txtLog.Visible = true; lnkDetalhes.Text = "Esconder detalhes"; btnTentar.Visible = true; Revelar(); }
            chkAbrir.Visible = ok;
            btnConcluir.Visible = true;
            btnConcluir.Text = ok ? "Concluir" : "Fechar";
            if (Portatil) chkAbrir.Checked = false;
            if (ok && pastaAtualizar != null)
            {
                if (!Portatil)
                {
                    if (mini != null) mini.Definir("Abrindo o " + Principal.Nome + "...", 100, "Atualizado para a versão " + Str(manifesto, "versao"));
                    Abrir(pasta);
                    await EsperarJanela(pasta);
                }
                Close();
            }
        }

        void Executar(string pasta, bool iniciar, bool atalho, CancellationToken ct)
        {
            var w = Obj(manifesto, "windows");
            var prog = Obj(Obj(w, "programa2"), variante);
            var nucleo = Obj(Obj(w, "nucleo"), variante);
            var motor = Obj(Obj(w, "motor"), variante);
            var basePac = Obj(w, "base");
            var midia = Obj(w, "midia");      // video direto e voz dos avisos (manifesto antigo nao tem)
            var cache = Path.Combine(Path.GetTempPath(), "ArgosEPI-Setup");
            Directory.CreateDirectory(cache);
            Log("Pasta: " + pasta + " | placa: " + variante + " | fonte: " + fonte + (FonteLocal ? " (arquivos locais)" : ""));
            if (!PastaServe(pasta)) throw new Exception("A pasta escolhida já tem outros arquivos: escolha uma pasta vazia.");

            // 1. o que baixar (nucleo, bibliotecas do motor e base so quando mudam: as atualizacoes ficam pequenas)
            bool precisaMotor = Marca(Path.Combine(pasta, "motor", "argos-motor-id.txt")) != Str(motor, "id");
            bool precisaBase = Marca(Path.Combine(pasta, "bin", "argos-base-id.txt")) != Str(basePac, "id");
            // o nucleo mora dentro de motor\: volta junto quando o motor e trocado
            bool precisaNucleo = precisaMotor || Marca(Path.Combine(pasta, "motor", "argos-nucleo-id.txt")) != Str(nucleo, "id");
            var arquivos = new List<Dictionary<string, object>>();
            var nomes = new Dictionary<Dictionary<string, object>, string>();
            bool precisaMidia = midia != null && Marca(Path.Combine(pasta, "midia", "argos-midia-id.txt")) != Str(midia, "id");
            if (precisaBase) { arquivos.Add(basePac); nomes[basePac] = "os modelos e o banco de dados"; } else Log("Modelos e banco já instalados: não precisa baixar de novo.");
            if (precisaMidia) { arquivos.Add(midia); nomes[midia] = "o vídeo direto e a voz dos avisos"; } else if (midia != null) Log("Vídeo direto e voz já instalados: não precisa baixar de novo.");
            if (precisaMotor) { arquivos.Add(motor); nomes[motor] = "as bibliotecas (" + Rotulo(variante) + ")"; } else Log("Bibliotecas " + Str(motor, "id") + " já instaladas: não precisa baixar de novo.");
            // pacote opcional do TensorRT (instalado pelo painel): mora dentro de motor\, entao volta junto quando o motor e trocado
            var trt = Obj(Obj(Obj(w, "extras"), "tensorrt"), variante);
            bool refazTrt = precisaMotor && trt != null && File.Exists(Path.Combine(pasta, "motor", "argos-trt-id.txt"));
            if (refazTrt) { arquivos.Add(trt); nomes[trt] = "o TensorRT (já estava instalado)"; }
            if (precisaNucleo) { arquivos.Add(nucleo); nomes[nucleo] = "o núcleo do motor"; } else Log("Núcleo " + Str(nucleo, "id") + " já instalado: não precisa baixar de novo.");
            arquivos.Add(prog); nomes[prog] = "o programa";
            long total = arquivos.Sum(a => Num(a, "tamanho")), feitoAntes = 0;
            var baixados = new Dictionary<Dictionary<string, object>, string>();
            var temporarios = new List<string>();   // so o que foi baixado e apagado no fim (o pendrive fica intacto)
            foreach (var a in arquivos)
            {
                var nome = Path.GetFileName(Str(a, "url"));
                long antes = feitoAntes;
                feitoAntes += Num(a, "tamanho");
                var local = FonteLocal ? fonte + Str(a, "url").Replace('/', '\\') : null;
                if (local != null && File.Exists(local) && new FileInfo(local).Length == Num(a, "tamanho"))
                {
                    Etapa("Lendo " + nomes[a], (int)(700.0 * feitoAntes / Math.Max(1, total)), "da pasta do instalador");
                    Log("arquivo local: " + local);
                    baixados[a] = local;
                    continue;
                }
                // pacote que nao esta na pasta (outra placa, por exemplo): vem da internet
                if (FonteLocal) Log(nome + " não está na pasta do instalador: baixando da internet");
                Etapa("Baixando " + nomes[a], -1);
                var dest = Path.Combine(cache, nome);
                Baixar(FonteLocal ? Principal.FontePadrao + Str(a, "url") : Url(Str(a, "url")), dest, Num(a, "tamanho"), Str(a, "sha256"), (feito, vel) =>
                    Etapa(null, (int)(700.0 * (antes + feito) / Math.Max(1, total)), Tam(antes + feito) + " de " + Tam(total) + (vel > 0 ? " · " + Tam((long)vel) + "/s" + Falta(total - antes - feito, vel) : "")), ct);
                baixados[a] = dest;
                temporarios.Add(dest);
            }

            // 2. fecha o programa aberto (e o banco dele) antes de trocar os arquivos
            Etapa("Fechando o programa aberto", 710, "");
            Fechar(pasta);

            // 3. limpa a pasta: so fica o que e do usuario (e o motor/base que nao mudaram)
            Directory.CreateDirectory(pasta);
            Etapa("Removendo os arquivos da versão anterior", 720, "");
            Limpar(pasta, !precisaMotor, !precisaBase, !precisaMidia);

            // 4. extrai (tudo ja compilado: nada e instalado ou baixado depois)
            long totalExt = Math.Max(1, arquivos.Sum(a => Num(a, "descompactado"))), feitoExt = 0;
            Func<Dictionary<string, object>, int[]> faixa = a =>
            {
                int de = 730 + (int)(220.0 * feitoExt / totalExt);
                feitoExt += Num(a, "descompactado");
                return new[] { de, 730 + (int)(220.0 * feitoExt / totalExt) };
            };
            if (precisaBase)
            {
                Etapa("Instalando os modelos e o banco de dados", -1, "");
                var f = faixa(basePac);
                Extrair(baixados[basePac], pasta, f[0], f[1], ct);
            }
            if (precisaMidia)
            {
                Etapa("Instalando o vídeo direto e a voz dos avisos", -1, "");
                var f = faixa(midia);
                Extrair(baixados[midia], pasta, f[0], f[1], ct);
            }
            if (precisaMotor)
            {
                Etapa("Instalando as bibliotecas (" + Rotulo(variante) + ")", -1, "");
                var f = faixa(motor);
                var novo = Path.Combine(pasta, "motor.novo");
                Extrair(baixados[motor], novo, f[0], f[1], ct);
                Directory.Move(Path.Combine(novo, "motor"), Path.Combine(pasta, "motor"));
                ApagarPasta(novo);
            }
            if (refazTrt)
            {
                Etapa("Instalando o TensorRT", -1, "");
                var f = faixa(trt);
                Extrair(baixados[trt], pasta, f[0], f[1], ct);
            }
            if (precisaNucleo)
            {
                Etapa("Instalando o núcleo do motor", -1, "");
                var f = faixa(nucleo);
                Extrair(baixados[nucleo], pasta, f[0], f[1], ct);
            }
            Etapa("Instalando o programa", -1, "");
            var fp = faixa(prog);
            Extrair(baixados[prog], pasta, fp[0], fp[1], ct);

            // 5. Windows: WebView2, atalhos, desinstalar, iniciar com o Windows
            Etapa("Preparando o Windows", 960, "");
            try { GarantirWebView2(cache); }
            catch (Exception ex) { Log("! Não consegui instalar o WebView2 (" + ex.Message + "). Sem internet? Instale depois o \"Microsoft Edge WebView2 Runtime\": a janela do programa precisa dele."); }
            var exe = Path.Combine(pasta, "ArgosEPI.exe");
            if (!Portatil)
            {
                Atalho(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), Principal.Nome + ".lnk"), exe, pasta);
                var lnkMesa = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), Principal.Nome + ".lnk");
                if (atalho) Atalho(lnkMesa, exe, pasta); else if (pastaAtualizar == null && File.Exists(lnkMesa)) File.Delete(lnkMesa);
                RegistrarDesinstalar(pasta, exe, Str(manifesto, "versao"));
                if (iniciar) IniciarComWindows(pasta, exe);
            }
            File.WriteAllText(Path.Combine(pasta, "instalacao.json"), new JavaScriptSerializer().Serialize(new Dictionary<string, object> {
                { "versao", Str(manifesto, "versao") }, { "variante", variante }, { "motor_id", Str(motor, "id") }, { "nucleo_id", Str(nucleo, "id") }, { "base_id", Str(basePac, "id") }, { "midia_id", Str(midia, "id") },
                { "instalado_em", DateTime.Now.ToString("s") } }));
            foreach (var b in temporarios) try { File.Delete(b); } catch { }
            Log("OK");
        }

        static string Marca(string arquivo)
        {
            try { return File.ReadAllText(arquivo).Trim(); } catch { return null; }
        }

        // Apaga tudo o que nao e do usuario: versoes antigas deixavam Python solto, codigo-fonte e outros arquivos.
        // Ficam: dados\ (banco, rostos, gravacoes), .env, models\ (modelos enviados pelo painel) e uploads\.
        void Limpar(string pasta, bool manterMotor, bool manterBase, bool manterMidia)
        {
            var ficam = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "dados", ".env", "models", "uploads" };
            if (manterMotor) ficam.Add("motor");
            if (manterBase) ficam.Add("bin");
            if (manterMidia) ficam.Add("midia");
            int n = 0;
            foreach (var item in Directory.GetFileSystemEntries(pasta))
            {
                var nome = Path.GetFileName(item);
                if (ficam.Contains(nome)) continue;
                if (Directory.Exists(item)) { Etapa(null, -1, "Removendo " + nome + "..."); ApagarPasta(item); }
                else ApagarArquivo(item);
                n++;
            }
            if (n > 0) Log("pasta limpa: " + n + " itens da versão anterior removidos");
        }

        void ApagarArquivo(string arq)
        {
            for (int t = 0; ; t++)
            {
                try { File.SetAttributes(arq, FileAttributes.Normal); File.Delete(arq); return; }
                catch (Exception) { if (t >= 10) throw; Thread.Sleep(500); }
            }
        }

        bool FonteLocal { get { return !fonte.Contains("://"); } }

        string Url(string u) { return u.Contains("://") ? u : fonte + u; }

        static string Falta(long bytes, double vel)
        {
            if (vel < 1024 || bytes <= 0) return "";
            double s = bytes / vel;
            return " · falta " + (s < 90 ? Math.Max(1, (int)s) + " s" : s < 5400 ? (int)Math.Round(s / 60) + " min" : (s / 3600).ToString("0.0") + " h");
        }

        const int Conexoes = 8;
        const long MinDividir = 256L << 10;   // um trecho com menos de 512 KB pela frente nao e dividido

        // trecho do arquivo que uma conexao esta baixando: [pos, fim)
        sealed class Trecho { public long pos, fim; public bool emUso; }

        // Download em paralelo, como um gerenciador de downloads: o arquivo comeca dividido em 8 trechos,
        // um por conexao; quem termina o seu pega a metade final do maior trecho que ainda falta. Redes que
        // limitam a velocidade por conexao (escola, empresa) somam as 8, e uma conexao que ficou lenta vai
        // perdendo o que falta para as rapidas e e trocada por uma conexao nova (o endereco publico do R2 as
        // vezes entrega uma conexao a 100 KB/s ao lado de outras a 8 MB/s). O arquivo .mapa guarda o que
        // falta: fechar e abrir continua.
        void BaixarEmBlocos(string url, string parte, long tamanho, Action<long, double> progresso, CancellationToken ct)
        {
            var mapaArq = parte + ".mapa";
            var trechos = new List<Trecho>();
            long antigo = File.Exists(parte) ? new FileInfo(parte).Length : -1;
            bool retomou = false;
            if (antigo == tamanho && File.Exists(mapaArq))
            {
                try
                {
                    var linhas = File.ReadAllLines(mapaArq);
                    if (linhas.Length > 0 && linhas[0] == "tamanho " + tamanho)
                    {
                        foreach (var l in linhas.Skip(1))
                        {
                            var p = l.Split(' ');
                            long a = long.Parse(p[0]), b = long.Parse(p[1]);
                            if (a < 0 || b > tamanho || a >= b) throw new FormatException();
                            trechos.Add(new Trecho { pos = a, fim = b });
                        }
                        retomou = true;
                    }
                }
                catch { trechos.Clear(); retomou = false; }
            }
            if (!retomou)
            {
                // sobra de um instalador antigo (baixava em sequencia, sem mapa): o comeco ja baixado vale
                long de = antigo > 0 && antigo < tamanho && !File.Exists(mapaArq) ? antigo : 0;
                long cada = Math.Max(MinDividir, (tamanho - de + Conexoes - 1) / Conexoes);
                for (long a = de; a < tamanho; a += cada) trechos.Add(new Trecho { pos = a, fim = Math.Min(tamanho, a + cada) });
            }
            using (var f = new FileStream(parte, FileMode.OpenOrCreate, FileAccess.Write, FileShare.ReadWrite)) f.SetLength(tamanho);

            var trava = new object();
            int ativos = 0;
            long feito = tamanho - trechos.Sum(t => t.fim - t.pos);
            long inicio = feito;
            Exception falha = null;
            var parar = CancellationTokenSource.CreateLinkedTokenSource(ct);
            var relogio = Stopwatch.StartNew();
            var recebido = new long[Conexoes];     // bytes por conexao, para achar a que ficou lenta
            var pedidoDesde = new long[Conexoes];  // quando o pedido atual da conexao comecou (0 = parada)
            var trocar = new bool[Conexoes];       // conexao lenta demais: fecha e abre outra
            int trocas = 0;
            Action salvarMapa = () =>
            {
                try
                {
                    string txt;
                    lock (trava) txt = "tamanho " + tamanho + "\n" + string.Join("\n", trechos.Where(t => t.pos < t.fim).Select(t => t.pos + " " + t.fim));
                    File.WriteAllText(mapaArq, txt);
                }
                catch { }
            };
            // proximo trecho para uma conexao livre: um que ninguem pegou ou a metade final do maior em andamento
            Func<Trecho> pegar = () =>
            {
                lock (trava)
                {
                    trechos.RemoveAll(t => t.pos >= t.fim && !t.emUso);
                    var livre = trechos.FirstOrDefault(t => !t.emUso && t.pos < t.fim);
                    if (livre != null) { livre.emUso = true; return livre; }
                    var maior = trechos.Where(t => t.emUso).OrderByDescending(t => t.fim - t.pos).FirstOrDefault();
                    if (maior == null || maior.fim - maior.pos < 2 * MinDividir) return null;
                    long meio = maior.pos + (maior.fim - maior.pos) / 2;
                    var novo = new Trecho { pos = meio, fim = maior.fim, emUso = true };
                    maior.fim = meio;
                    trechos.Add(novo);
                    return novo;
                }
            };

            ParameterizedThreadStart trabalho = (indice) =>
            {
                int k = (int)indice;
                var buf = new byte[128 << 10];
                try
                {
                    using (var fs = new FileStream(parte, FileMode.Open, FileAccess.Write, FileShare.ReadWrite))
                    {
                        Trecho t;
                        while (!parar.IsCancellationRequested && (t = pegar()) != null)
                        {
                            int erros = 0;
                            while (true)
                            {
                                long de, ate;
                                lock (trava) { de = t.pos; ate = t.fim; }
                                if (de >= ate) break;
                                bool andou = false, trocada = false;
                                try
                                {
                                    Volatile.Write(ref trocar[k], false);
                                    Interlocked.Exchange(ref pedidoDesde[k], Math.Max(1, relogio.ElapsedMilliseconds));
                                    var req = (HttpWebRequest)WebRequest.Create(url);
                                    req.UserAgent = "ArgosEPI-Setup";
                                    req.Timeout = 30000; req.ReadWriteTimeout = 20000;
                                    req.AddRange(de, ate - 1);
                                    using (var resp = (HttpWebResponse)req.GetResponse())
                                    {
                                        if (resp.StatusCode != HttpStatusCode.PartialContent && !(de == 0 && ate == tamanho))
                                            throw new NotSupportedException("o servidor não aceita download em partes");
                                        using (var rs = resp.GetResponseStream())
                                        {
                                            fs.Position = de;
                                            while (true)
                                            {
                                                parar.Token.ThrowIfCancellationRequested();
                                                if (Volatile.Read(ref trocar[k])) { trocada = true; break; }
                                                int max;
                                                lock (trava) max = (int)Math.Min(buf.Length, t.fim - t.pos);   // o fim encolhe quando outra conexao leva a metade
                                                if (max <= 0) break;
                                                int lido = rs.Read(buf, 0, max);
                                                if (lido <= 0) break;
                                                lock (trava) lido = (int)Math.Min(lido, t.fim - t.pos);
                                                if (lido <= 0) break;
                                                // grava e so depois avanca a posicao: o mapa nunca diz "pronto" para o que nao foi
                                                // para o disco. (Quem divide o trecho deixa pelo menos MinDividir, mais que este
                                                // bloco, entao o fim nao passa para tras do que acabou de ser gravado.)
                                                fs.Write(buf, 0, lido);
                                                fs.Flush();
                                                lock (trava) t.pos += lido;
                                                Interlocked.Add(ref feito, lido);
                                                Interlocked.Add(ref recebido[k], lido);
                                                andou = true;
                                            }
                                            // saiu antes do fim da resposta (outra conexao levou a metade, ou esta ficou lenta): fecha e abre outra
                                            bool cortado;
                                            lock (trava) cortado = t.fim < ate;
                                            if (cortado || trocada) try { req.Abort(); } catch { }
                                        }
                                    }
                                    fs.Flush();
                                }
                                catch (OperationCanceledException) { return; }
                                catch (Exception ex)
                                {
                                    if (parar.IsCancellationRequested) return;
                                    bool acabou;
                                    lock (trava) acabou = t.pos >= t.fim;
                                    if (acabou) break;   // o erro veio de fechar a resposta no meio: o trecho esta completo
                                    if (trocada) continue;   // fechada de proposito por estar lenta: pede de novo ja
                                    erros = andou ? 1 : erros + 1;
                                    if (ex is NotSupportedException || erros >= 6) { lock (trava) { if (falha == null) falha = ex; } parar.Cancel(); return; }
                                    if (parar.Token.WaitHandle.WaitOne(1000 * erros)) return;
                                }
                            }
                            lock (trava) t.emUso = false;
                            Interlocked.Exchange(ref pedidoDesde[k], 0);
                        }
                    }
                }
                catch (Exception ex) { lock (trava) { if (falha == null) falha = ex; } parar.Cancel(); }
                finally { Interlocked.Exchange(ref pedidoDesde[k], 0); Interlocked.Decrement(ref ativos); }
            };

            int quantos = feito >= tamanho ? 0 : Conexoes;
            ativos = quantos;
            for (int k = 0; k < quantos; k++) new Thread(trabalho) { IsBackground = true, Name = "baixar-" + k }.Start(k);

            // velocidade = media dos ultimos 4 segundos (a de uma conexao sozinha oscila muito)
            var amostras = new Queue<Tuple<long, long>>();
            var porConexao = new Queue<Tuple<long, long[]>>();
            long ultimoMapa = 0;
            double pico = 0;   // melhor velocidade de uma conexao nos ultimos segundos (cai 5% por segundo)
            while (Volatile.Read(ref ativos) > 0)
            {
                if (ct.IsCancellationRequested) parar.Cancel();
                Thread.Sleep(200);
                long agora = relogio.ElapsedMilliseconds, f = Interlocked.Read(ref feito);
                amostras.Enqueue(Tuple.Create(agora, f));
                while (amostras.Count > 2 && agora - amostras.Peek().Item1 > 4000) amostras.Dequeue();
                var a0 = amostras.Peek();
                progresso(Math.Min(f, tamanho), agora > a0.Item1 ? Math.Max(0, f - a0.Item2) * 1000.0 / (agora - a0.Item1) : 0);
                if (agora - ultimoMapa > 1000)
                {
                    ultimoMapa = agora;
                    salvarMapa();
                    // Conexao que ha 3 s rende menos de 1/4 da melhor: troca por uma nova. A comparacao e com a
                    // melhor (e nao com a media) porque as lentas tendem a virar maioria: as rapidas terminam logo
                    // e a cada pedido novo podem cair numa lenta. Se a rede toda e lenta, todas rendem parecido
                    // e ninguem e trocado.
                    porConexao.Enqueue(Tuple.Create(agora, (long[])recebido.Clone()));
                    while (porConexao.Count > 1 && agora - porConexao.Peek().Item1 > 3500) porConexao.Dequeue();
                    var velha = porConexao.Peek();
                    if (agora - velha.Item1 >= 2500)
                    {
                        pico *= 0.95;
                        var vel = new List<Tuple<int, double>>();
                        for (int k = 0; k < quantos; k++)
                        {
                            double v = (Interlocked.Read(ref recebido[k]) - velha.Item2[k]) * 1000.0 / (agora - velha.Item1);
                            pico = Math.Max(pico, v);
                            long desde = Interlocked.Read(ref pedidoDesde[k]);
                            if (desde > 0 && desde <= velha.Item1) vel.Add(Tuple.Create(k, v));   // no mesmo pedido a janela inteira
                        }
                        foreach (var v in vel)
                            if (pico > 96 * 1024 && v.Item2 < pico * 0.25) { Volatile.Write(ref trocar[v.Item1], true); trocas++; }
                    }
                }
            }
            salvarMapa();
            ct.ThrowIfCancellationRequested();
            if (falha != null) throw falha;
            bool falta;
            lock (trava) falta = trechos.Any(t => t.pos < t.fim);
            if (falta) throw new IOException("download incompleto");
            double seg = Math.Max(0.001, relogio.Elapsed.TotalSeconds);
            if (quantos > 0) Log("  " + Tam(feito - inicio) + " em " + seg.ToString("0") + " s (" + Tam((long)((feito - inicio) / seg)) + "/s, " + quantos + " conexões" +
                (trocas > 0 ? ", " + trocas + " conexão(ões) lenta(s) trocada(s)" : "") + ")");
        }

        // um arquivo so, em sequencia: para servidores que nao aceitam download em partes
        void BaixarSimples(string url, string parte, long tamanho, Action<long, double> progresso, CancellationToken ct)
        {
            var req = (HttpWebRequest)WebRequest.Create(url);
            req.UserAgent = "ArgosEPI-Setup";
            req.Timeout = 30000; req.ReadWriteTimeout = 60000;
            using (var resp = (HttpWebResponse)req.GetResponse())
            using (var rs = resp.GetResponseStream())
            using (var fs = new FileStream(parte, FileMode.Create, FileAccess.Write))
            {
                var buf = new byte[1 << 20];
                var relogio = Stopwatch.StartNew();
                long ja = 0, ultimo = 0;
                int lido;
                while ((lido = rs.Read(buf, 0, buf.Length)) > 0)
                {
                    ct.ThrowIfCancellationRequested();
                    fs.Write(buf, 0, lido);
                    ja += lido;
                    if (relogio.ElapsedMilliseconds - ultimo > 250)
                    {
                        ultimo = relogio.ElapsedMilliseconds;
                        progresso(ja, ja / Math.Max(0.001, relogio.Elapsed.TotalSeconds));
                    }
                }
            }
        }

        // download com retomada e conferencia do SHA-256
        void Baixar(string url, string dest, long tamanho, string sha, Action<long, double> progresso, CancellationToken ct)
        {
            if (File.Exists(dest) && new FileInfo(dest).Length == tamanho && Sha256(dest) == sha) { Log("já baixado: " + Path.GetFileName(dest)); progresso(tamanho, 0); return; }
            var parte = dest + ".parte";
            bool emBlocos = true;
            for (int tentativa = 1; ; tentativa++)
            {
                try
                {
                    if (File.Exists(parte) && new FileInfo(parte).Length > tamanho) { File.Delete(parte); try { File.Delete(parte + ".mapa"); } catch { } }
                    if (emBlocos)
                    {
                        try { BaixarEmBlocos(url, parte, tamanho, progresso, ct); }
                        catch (NotSupportedException) { emBlocos = false; Log("  o servidor não aceita download em partes: baixando em sequência"); }
                    }
                    if (!emBlocos) BaixarSimples(url, parte, tamanho, progresso, ct);
                    if (new FileInfo(parte).Length != tamanho) throw new IOException("download incompleto");
                    Etapa(null, -1, "Conferindo o arquivo...");
                    if (!string.IsNullOrEmpty(sha) && Sha256(parte) != sha)
                    {
                        File.Delete(parte); try { File.Delete(parte + ".mapa"); } catch { }
                        throw new IOException("o arquivo veio corrompido (SHA-256 diferente)");
                    }
                    try { File.Delete(parte + ".mapa"); } catch { }
                    if (File.Exists(dest)) File.Delete(dest);
                    File.Move(parte, dest);
                    Log("baixado: " + Path.GetFileName(dest));
                    return;
                }
                catch (OperationCanceledException) { throw; }
                catch (Exception ex)
                {
                    Log("  tentativa " + tentativa + ": " + ex.Message);
                    if (tentativa >= 6) throw new Exception("Não consegui baixar " + Path.GetFileName(dest) + ": " + ex.Message);
                    Thread.Sleep(3000 * tentativa);
                }
            }
        }

        static string Sha256(string arq)
        {
            using (var s = SHA256.Create()) using (var f = File.OpenRead(arq))
                return BitConverter.ToString(s.ComputeHash(f)).Replace("-", "").ToLowerInvariant();
        }

        // nunca escreve em dados\ nem no .env do usuario
        void Extrair(string zip, string destino, int de, int ate, CancellationToken ct)
        {
            using (var z = ZipFile.OpenRead(zip))
            {
                var itens = z.Entries.Where(e => !string.IsNullOrEmpty(e.Name)).ToList();
                long total = Math.Max(1, itens.Sum(e => e.Length)), feito = 0;
                int i = 0;
                var raiz = Path.GetFullPath(destino) + "\\";
                foreach (var e in itens)
                {
                    ct.ThrowIfCancellationRequested();
                    var rel = e.FullName.Replace('/', '\\');
                    if (rel.StartsWith("dados\\", StringComparison.OrdinalIgnoreCase) || rel.Equals(".env", StringComparison.OrdinalIgnoreCase)) continue;
                    var alvo = Path.GetFullPath(Path.Combine(destino, rel));
                    if (!alvo.StartsWith(raiz, StringComparison.OrdinalIgnoreCase)) continue;
                    Directory.CreateDirectory(Path.GetDirectoryName(alvo));
                    for (int t = 0; ; t++)
                    {
                        try { e.ExtractToFile(alvo, true); break; }
                        catch (IOException) { if (t >= 10) throw; Thread.Sleep(500); }   // arquivo ainda preso por um processo que esta fechando
                    }
                    feito += e.Length;
                    if (++i % 200 == 0) Etapa(null, de + (int)((ate - de) * (double)feito / total), i + " de " + itens.Count + " arquivos");
                }
            }
        }

        // pede para o programa sair (desliga backend e banco); se nao responder, encerra os processos da pasta
        void Fechar(string pasta)
        {
            string chave;
            using (var sha = SHA1.Create())
                chave = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(Path.GetFullPath(pasta).TrimEnd('\\').ToLowerInvariant()))).Replace("-", "").Substring(0, 10);
            try { EventWaitHandle.OpenExisting("Local\\ArgosEPIServidor-sair-" + chave).Set(); Log("pedindo para o programa aberto fechar..."); } catch { }
            for (int i = 0; i < 60 && ProcessosDaPasta(pasta).Any(p => p.Item2.EndsWith("ArgosEPI.exe", StringComparison.OrdinalIgnoreCase)); i++) Thread.Sleep(1000);
            var pgctl = Path.Combine(pasta, "bin", "pgsql", "bin", "pg_ctl.exe");
            var pgdata = Path.Combine(pasta, "dados", "pgdata");
            if (File.Exists(pgctl) && File.Exists(Path.Combine(pgdata, "postmaster.pid")))
                Rodar(pgctl, "-D \"" + pgdata + "\" -m fast -w -t 60 stop", 90000);
            foreach (var p in ProcessosDaPasta(pasta))
                try { Process.GetProcessById(p.Item1).Kill(); Log("encerrado: " + p.Item2); } catch { }
            Thread.Sleep(1000);
        }

        static List<Tuple<int, string>> ProcessosDaPasta(string pasta)
        {
            var r = new List<Tuple<int, string>>();
            var raiz = Path.GetFullPath(pasta).TrimEnd('\\') + "\\";
            try
            {
                using (var s = new System.Management.ManagementObjectSearcher("SELECT ProcessId, ExecutablePath FROM Win32_Process"))
                    foreach (System.Management.ManagementObject o in s.Get())
                    {
                        var c = o["ExecutablePath"] as string;
                        if (c != null && c.StartsWith(raiz, StringComparison.OrdinalIgnoreCase) && Convert.ToInt32(o["ProcessId"]) != Process.GetCurrentProcess().Id)
                            r.Add(Tuple.Create(Convert.ToInt32(o["ProcessId"]), c));
                    }
            }
            catch { }
            return r;
        }

        void GarantirWebView2(string cache)
        {
            if (VersaoWebView2() != null) { Log("WebView2 " + VersaoWebView2() + " já instalado"); return; }
            Log("Instalando o WebView2 da Microsoft (a janela do programa usa)...");
            var exe = Path.Combine(cache, "MicrosoftEdgeWebview2Setup.exe");
            using (var wc = new WebClient()) wc.DownloadFile("https://go.microsoft.com/fwlink/p/?LinkId=2124703", exe);
            Rodar(exe, "/silent /install", 15 * 60 * 1000);
            Log(VersaoWebView2() != null ? "WebView2 instalado" : "! WebView2 não ficou instalado: instale o Microsoft Edge WebView2 Runtime");
        }

        static string VersaoWebView2()
        {
            foreach (var chave in new[] { @"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
                                          @"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" })
                foreach (var raiz in new[] { Registry.LocalMachine, Registry.CurrentUser })
                    try
                    {
                        using (var k = raiz.OpenSubKey(chave))
                        {
                            var v = k != null ? k.GetValue("pv") as string : null;
                            if (!string.IsNullOrEmpty(v) && v != "0.0.0.0") return v;
                        }
                    }
                    catch { }
            return null;
        }

        void IniciarComWindows(string pasta, string exe)
        {
            try
            {
                using (var k = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", true))
                    k.SetValue(Principal.Nome, "\"" + exe + "\" --minimizado");
                // o programa le esta configuracao (Ajustes > Iniciar com o Windows)
                var cfg = Path.Combine(pasta, "dados", "servidor-app.json");
                Directory.CreateDirectory(Path.GetDirectoryName(cfg));
                var js = new JavaScriptSerializer();
                var d = File.Exists(cfg) ? js.Deserialize<Dictionary<string, object>>(File.ReadAllText(cfg)) : new Dictionary<string, object>();
                d["iniciar_com_sistema"] = true;
                d["iniciar_minimizado"] = true;
                File.WriteAllText(cfg, js.Serialize(d), new UTF8Encoding(false));
                Log("iniciar com o Windows: ligado");
            }
            catch (Exception ex) { Log("! iniciar com o Windows: " + ex.Message); }
        }

        void RegistrarDesinstalar(string pasta, string exe, string versao)
        {
            using (var k = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\ArgosEPIServidor"))
            {
                k.SetValue("DisplayName", Principal.Nome);
                k.SetValue("DisplayVersion", versao ?? "");
                k.SetValue("Publisher", "Argos EPI - SENAI Lauro de Freitas/BA");
                k.SetValue("DisplayIcon", exe);
                k.SetValue("InstallLocation", pasta);
                k.SetValue("UninstallString", "\"" + exe + "\" --desinstalar");
                k.SetValue("URLInfoAbout", "https://argosepi.vercel.app");
                k.SetValue("NoModify", 1, RegistryValueKind.DWord);
                k.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                try { k.SetValue("EstimatedSize", (int)(new DirectoryInfo(pasta).EnumerateFiles("*", SearchOption.AllDirectories).Sum(f => f.Length) / 1024), RegistryValueKind.DWord); } catch { }
            }
        }

        void Atalho(string lnk, string alvo, string pasta)
        {
            try
            {
                var tipo = Type.GetTypeFromProgID("WScript.Shell");
                var shell = Activator.CreateInstance(tipo);
                var sc = tipo.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { lnk });
                var t = sc.GetType();
                t.InvokeMember("TargetPath", BindingFlags.SetProperty, null, sc, new object[] { alvo });
                t.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, sc, new object[] { pasta });
                t.InvokeMember("Description", BindingFlags.SetProperty, null, sc, new object[] { Principal.Nome });
                t.InvokeMember("Save", BindingFlags.InvokeMethod, null, sc, null);
                Log("atalho: " + lnk);
            }
            catch (Exception ex) { Log("! atalho: " + ex.Message); }
        }

        void Abrir(string pasta)
        {
            try { Process.Start(new ProcessStartInfo(Path.Combine(pasta, "ArgosEPI.exe")) { WorkingDirectory = pasta, UseShellExecute = true }); } catch { }
        }

        void ApagarPasta(string p)
        {
            for (int t = 0; ; t++)
            {
                try { if (Directory.Exists(p)) Directory.Delete(p, true); return; }
                catch (Exception)
                {
                    if (t >= 15) throw;
                    // arquivo somente leitura ou ainda preso por um processo que esta fechando
                    try { foreach (var f in Directory.EnumerateFiles(p, "*", SearchOption.AllDirectories)) File.SetAttributes(f, FileAttributes.Normal); } catch { }
                    Thread.Sleep(1000);
                }
            }
        }

        string Rodar(string exe, string args, int ms)
        {
            try
            {
                var p = Process.Start(new ProcessStartInfo(exe, args) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true });
                var saida = p.StandardOutput.ReadToEndAsync();
                p.WaitForExit(ms);
                return saida.Result;
            }
            catch (Exception ex) { Log("! " + Path.GetFileName(exe) + ": " + ex.Message); return ""; }
        }

        // ---------- utilidades ----------
        void Etapa(string titulo, int milesimos, string detalhe = null)
        {
            if (InvokeRequired) { try { BeginInvoke(new Action(() => Etapa(titulo, milesimos, detalhe))); } catch { } return; }
            if (titulo != null) { lblEtapa.Text = titulo; Log("> " + titulo); }
            if (milesimos >= 0) barra.Value = Math.Max(0, Math.Min(1000, milesimos));
            if (detalhe != null) lblDetalhe.Text = detalhe;
            if (mini != null) mini.Definir(lblEtapa.Text, barra.Value / 10, lblDetalhe.Text);
        }

        void Log(string s)
        {
            if (InvokeRequired) { try { BeginInvoke(new Action(() => Log(s))); } catch { } return; }
            var linha = DateTime.Now.ToString("HH:mm:ss ") + s + Environment.NewLine;
            txtLog.AppendText(linha);
            // copia em %TEMP%\ArgosEPI-Setup\instalador.log: da para conferir depois de fechar a janela
            try
            {
                var pasta = Path.Combine(Path.GetTempPath(), "ArgosEPI-Setup");
                Directory.CreateDirectory(pasta);
                File.AppendAllText(Path.Combine(pasta, "instalador.log"), linha, Encoding.UTF8);
            }
            catch { }
        }

        static string Rotulo(string v) { return v == "nvidia" ? "NVIDIA" : v == "dml" ? "AMD/Intel" : "CPU"; }

        static string Tam(long b)
        {
            if (b >= 1L << 30) return (b / (double)(1L << 30)).ToString("0.0") + " GB";
            if (b >= 1L << 20) return (b / (double)(1L << 20)).ToString("0") + " MB";
            return Math.Max(1, b / 1024) + " KB";
        }

        static Dictionary<string, object> Obj(Dictionary<string, object> d, string k)
        {
            object v; return d != null && d.TryGetValue(k, out v) ? v as Dictionary<string, object> : null;
        }
        static string Str(Dictionary<string, object> d, string k)
        {
            object v; return d != null && d.TryGetValue(k, out v) && v != null ? Convert.ToString(v, System.Globalization.CultureInfo.InvariantCulture) : null;
        }
        static long Num(Dictionary<string, object> d, string k)
        {
            object v; return d != null && d.TryGetValue(k, out v) && v != null ? Convert.ToInt64(v) : 0;
        }
    }

    // Placa de video: NVIDIA -> nvidia; AMD Radeon ou Intel Arc/Iris Xe -> dml; o resto -> cpu.
    // ---------------------------------------------------------------- janelinha da atualizacao
    // Logo com um anel girando, o que esta sendo feito e uma barra fina. E o que a pessoa ve quando
    // toca em "Atualizar" no programa: ele fecha, isto aparece, e no fim o programa abre de novo.
    public class JanelaAtualizando : Form
    {
        static readonly Color Fundo = Color.FromArgb(8, 10, 29), Trilho = Color.FromArgb(40, 46, 96), Ouro = Color.FromArgb(251, 195, 67),
            Azul = Color.FromArgb(77, 88, 216), Texto = Color.FromArgb(233, 235, 251), Suave = Color.FromArgb(167, 176, 228);
        const int Largura = 400, Altura = 276;
        readonly System.Windows.Forms.Timer anim;
        readonly Image logo;
        readonly float k = 1f;              // escala da tela (125%, 150%...)
        string status = "Preparando a atualização...", detalhe = "", versao = "";
        int pct = -1;
        float angulo, fase;
        bool podeFechar;

        [System.Runtime.InteropServices.DllImport("gdi32.dll")] static extern IntPtr CreateRoundRectRgn(int l, int t, int r, int b, int w, int h);
        [System.Runtime.InteropServices.DllImport("user32.dll")] static extern bool ReleaseCapture();
        [System.Runtime.InteropServices.DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr w, IntPtr l);

        public JanelaAtualizando(Icon icone)
        {
            Text = "Atualizando o " + Principal.Nome;
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Fundo;
            ShowInTaskbar = true;
            try { using (var g = CreateGraphics()) k = Math.Max(1f, g.DpiX / 96f); } catch { }
            ClientSize = new Size((int)(Largura * k), (int)(Altura * k));
            if (icone != null)
            {
                Icon = icone;
                try { logo = new Icon(icone, 256, 256).ToBitmap(); } catch { }
            }
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer, true);
            Region = Region.FromHrgn(CreateRoundRectRgn(0, 0, ClientSize.Width + 1, ClientSize.Height + 1, (int)(26 * k), (int)(26 * k)));
            // arrasta por qualquer ponto
            MouseDown += (s, e) => { if (e.Button == MouseButtons.Left) { ReleaseCapture(); SendMessage(Handle, 0xA1, (IntPtr)2, IntPtr.Zero); } };
            // fechar no meio deixaria a instalacao pela metade: so o instalador fecha esta janela
            FormClosing += (s, e) => { if (!podeFechar && e.CloseReason == CloseReason.UserClosing) e.Cancel = true; };
            anim = new System.Windows.Forms.Timer { Interval = 16 };
            anim.Tick += (s, e) => { angulo = (angulo + 5f) % 360; fase += 0.045f; Invalidate(); };
            anim.Start();
        }

        public void Definir(string st, int porcento, string det)
        {
            if (IsDisposed) return;
            if (!string.IsNullOrEmpty(st)) status = st;
            pct = porcento;
            detalhe = det ?? "";
            Invalidate();
        }

        public void Versao(string v) { versao = v ?? ""; Invalidate(); }

        public void Fechar()
        {
            podeFechar = true;
            anim.Stop();
            try { if (!IsDisposed) Close(); } catch { }
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAliasGridFit;
            g.InterpolationMode = InterpolationMode.HighQualityBicubic;
            g.Clear(Fundo);
            g.ScaleTransform(k, k);
            // brilho azul atras da logo, como o fundo do painel
            using (var gp = new GraphicsPath())
            {
                gp.AddEllipse(Largura / 2 - 170, -90, 340, 270);
                using (var pb = new PathGradientBrush(gp) { CenterColor = Color.FromArgb(90, Azul), SurroundColors = new[] { Color.FromArgb(0, Fundo) } }) g.FillPath(pb, gp);
            }
            using (var faixa = new SolidBrush(Ouro)) g.FillRectangle(faixa, 0, 0, Largura, 5);
            float cx = Largura / 2f, cy = 92, r = 50;
            if (logo != null)
            {
                float t = 62 * (1 + 0.02f * (float)Math.Sin(fase * 2));
                g.DrawImage(logo, new RectangleF(cx - t / 2, cy - t / 2, t, t));
            }
            var anel = new RectangleF(cx - r, cy - r, r * 2, r * 2);
            using (var trilho = new Pen(Trilho, 3.5f)) g.DrawEllipse(trilho, anel);
            using (var pen = new Pen(Ouro, 3.5f) { StartCap = LineCap.Round, EndCap = LineCap.Round })
            {
                float arco = 70 + 110 * (0.5f + 0.5f * (float)Math.Sin(fase * 1.6));
                g.DrawArc(pen, anel, angulo - 90, arco);
            }
            var centro = new StringFormat { Alignment = StringAlignment.Center, Trimming = StringTrimming.EllipsisCharacter, FormatFlags = StringFormatFlags.NoWrap };
            using (var ft = new Font("Segoe UI Semibold", 12.5f))
            using (var b = new SolidBrush(Texto))
                g.DrawString(status, ft, b, new RectangleF(14, 160, Largura - 28, 28), centro);
            using (var fd = new Font("Segoe UI", 9f))
            using (var b = new SolidBrush(Suave))
                g.DrawString(detalhe, fd, b, new RectangleF(14, 190, Largura - 28, 20), centro);
            // barra fina com o andamento
            var barra = new RectangleF(56, 222, Largura - 112, 5);
            using (var bt = new SolidBrush(Trilho)) g.FillRectangle(bt, barra);
            if (pct > 0)
                using (var ba = new SolidBrush(Ouro)) g.FillRectangle(ba, barra.X, barra.Y, barra.Width * Math.Min(100, pct) / 100f, barra.Height);
            using (var fv = new Font("Segoe UI", 8f))
            using (var b = new SolidBrush(Suave))
                g.DrawString(Principal.Nome + (versao.Length > 0 ? " · " + versao : ""), fv, b, new RectangleF(14, 242, Largura - 28, 18), centro);
        }
    }

    public class Placa
    {
        public string Nome, Modo = "cpu", Resumo = "Nenhuma placa de vídeo dedicada encontrada: o recomendado é só o processador.";
        public bool Nvidia;
        static readonly string[] Virtuais = { "microsoft", "basic display", "remote", "virtual", "parsec", "citrix", "hyper-v", "vmware", "virtualbox", "spacedesk", "displaylink", "meta " };

        public static Placa Detectar()
        {
            var p = new Placa();
            var nomes = new List<string>();
            try
            {
                using (var cls = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64)
                    .OpenSubKey(@"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"))
                    if (cls != null)
                        foreach (var sub in cls.GetSubKeyNames().Where(s => Regex.IsMatch(s, @"^\d{4}$")))
                            try
                            {
                                using (var k = cls.OpenSubKey(sub))
                                {
                                    var n = (k.GetValue("DriverDesc") as string ?? "").Trim();
                                    if (n.Length > 0 && !Virtuais.Any(v => n.ToLowerInvariant().Contains(v)) && !nomes.Contains(n)) nomes.Add(n);
                                }
                            }
                            catch { }
            }
            catch { }
            var nv = nomes.FirstOrDefault(n => n.IndexOf("nvidia", StringComparison.OrdinalIgnoreCase) >= 0);
            var amd = nomes.FirstOrDefault(n => Regex.IsMatch(n, @"(?i)\b(amd|radeon|ati)\b"));
            var intel = nomes.FirstOrDefault(n => Regex.IsMatch(n, @"(?i)intel.*\b(arc|iris)\b"));
            if (nv != null) { p.Nvidia = true; p.Nome = nv; p.Modo = "nvidia"; p.Resumo = "Encontrada: " + nv + " (recomendado: NVIDIA)."; }
            else if (amd != null || intel != null) { p.Nome = amd ?? intel; p.Modo = "dml"; p.Resumo = "Encontrada: " + p.Nome + " (recomendado: AMD ou Intel)."; }
            else if (nomes.Count > 0) { p.Nome = nomes[0]; p.Resumo = "Vídeo integrado (" + nomes[0] + "): o recomendado é só o processador."; }
            return p;
        }
    }
}
