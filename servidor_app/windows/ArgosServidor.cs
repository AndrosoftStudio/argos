// Argos EPI Servidor - janela do programa no Windows (ArgosEPI.exe).
// C# + WebView2, compilado com o csc do .NET Framework que ja vem no Windows (ver build/empacotar_windows.ps1).
// A janela nao tem console: ela abre o motor (motor\ArgosMotor.exe supervisor) escondido, que liga o banco e
// o servidor, e mostra a interface dele (recursos.pak). Icone na bandeja, "iniciar com o Windows" e a
// escolha do X (fechar de vez ou ficar em segundo plano) vem dos Ajustes da interface.
// Design proprio: sem a barra de titulo do Windows; a barra azul da interface e a barra da janela
// (arrastar, minimizar, maximizar, fechar), mantendo borda de redimensionar, encaixe e cantos do Windows 11.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Microsoft.Win32;

namespace ArgosEPI
{
    static class Programa
    {
        public const string Nome = "Argos EPI Servidor";
        public static readonly string Pasta = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        // uma instancia por pasta de instalacao (dois servidores em pastas diferentes nao se misturam)
        public static readonly string Chave = Hash(Pasta.ToLowerInvariant());
        public static string EventoMostrar { get { return "Local\\ArgosEPIServidor-mostrar-" + Chave; } }
        public static string EventoSair { get { return "Local\\ArgosEPIServidor-sair-" + Chave; } }

        static string Hash(string s)
        {
            using (var sha = System.Security.Cryptography.SHA1.Create())
                return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(s))).Replace("-", "").Substring(0, 10);
        }

        [STAThread]
        static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            if (args.Contains("--desinstalar")) { Desinstalador.Rodar(); return; }
            bool criado;
            using (var mutex = new Mutex(true, "Local\\ArgosEPIServidor-" + Chave, out criado))
            {
                if (!criado)
                {
                    // ja aberto (talvez escondido na bandeja): so mostra a janela que existe
                    try { EventWaitHandle.OpenExisting(EventoMostrar).Set(); } catch { }
                    return;
                }
                Application.Run(new Janela(args.Contains("--minimizado")));
            }
        }
    }

    public class Janela : Form
    {
        static readonly Color Navy = Color.FromArgb(8, 10, 29);
        readonly WebView2 web;
        readonly NotifyIcon bandeja;
        readonly bool comecarEscondido;
        bool primeiraVez = true, avisouBandeja, saindoDeVez, webPronta;
        Process sup;
        string urlUi, baseUi, token, aoFechar = "perguntar";
        readonly StringBuilder errosSup = new StringBuilder();
        readonly EventWaitHandle evMostrar, evSair;

        [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int valor, int tamanho);
        [DllImport("user32.dll")] static extern bool ReleaseCapture();
        [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);
        [DllImport("user32.dll")] static extern int GetSystemMetrics(int indice);
        [DllImport("user32.dll")] static extern bool IsZoomed(IntPtr hWnd);
        [DllImport("user32.dll")] static extern bool IsIconic(IntPtr hWnd);
        [StructLayout(LayoutKind.Sequential)] struct RECT { public int Left, Top, Right, Bottom; }

        // faixa em volta da interface por onde a janela e redimensionada (a cor acompanha a barra do topo)
        const int Borda = 5;
        const int WM_NCCALCSIZE = 0x83, WM_NCHITTEST = 0x84, WM_NCLBUTTONDOWN = 0xA1, WM_WINDOWPOSCHANGED = 0x47, HTCAPTION = 2;
        static readonly Color Azul = Color.FromArgb(55, 64, 180);

        public Janela(bool minimizado)
        {
            comecarEscondido = minimizado;
            Text = Programa.Nome;
            ClientSize = new Size(1120, 740);
            MinimumSize = new Size(780, 540);
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Azul;
            Padding = new Padding(Borda);
            Resize += (s, e) =>
            {
                Padding = new Padding(IsHandleCreated && IsZoomed(Handle) ? 0 : Borda);
                EnviarEstadoJanela();
            };
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

            web = new WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = Navy };
            Controls.Add(web);

            bandeja = new NotifyIcon { Icon = Icon, Text = Programa.Nome, Visible = true };
            var menu = new ContextMenuStrip { Font = new Font("Segoe UI", 9.5f), ShowImageMargin = false };
            var abrir = new ToolStripMenuItem("Abrir o " + Programa.Nome, null, (s, e) => Mostrar()) { Font = new Font("Segoe UI", 9.5f, FontStyle.Bold) };
            menu.Items.Add(abrir);
            menu.Items.Add(new ToolStripMenuItem("Abrir o painel (site)", null, (s, e) => AbrirExterno("https://argosepi.vercel.app")));
            menu.Items.Add(new ToolStripMenuItem("Câmeras deste servidor", null, (s, e) => { Mostrar(); Enviar("{\"tipo\":\"ir\",\"pagina\":\"cameras\"}"); }));
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(new ToolStripMenuItem("Sair (desliga o servidor)", null, (s, e) => SairDeVez()));
            bandeja.ContextMenuStrip = menu;
            bandeja.MouseClick += (s, e) => { if (e.Button == MouseButtons.Left) Mostrar(); };
            bandeja.BalloonTipClicked += (s, e) => Mostrar();

            // outra copia aberta pede para mostrar esta; o instalador pede para sair (atualizacao)
            evMostrar = new EventWaitHandle(false, EventResetMode.AutoReset, Programa.EventoMostrar);
            evSair = new EventWaitHandle(false, EventResetMode.AutoReset, Programa.EventoSair);
            new Thread(() => { while (evMostrar.WaitOne()) try { BeginInvoke(new Action(Mostrar)); } catch { return; } }) { IsBackground = true }.Start();
            new Thread(() => { if (evSair.WaitOne()) try { BeginInvoke(new Action(SairDeVez)); } catch { } }) { IsBackground = true }.Start();

            Load += async (s, e) => await IniciarWeb();
            FormClosing += AoFechar;
        }

        // comeca escondido na bandeja quando o Windows abre o programa com --minimizado
        protected override void SetVisibleCore(bool value)
        {
            if (primeiraVez && comecarEscondido)
            {
                primeiraVez = false;
                CreateHandle();
                value = false;
                // o Load so roda quando a janela aparece: liga o servidor mesmo assim
                BeginInvoke(new Action(async () => await IniciarWeb()));
            }
            primeiraVez = false;
            base.SetVisibleCore(value);
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            try
            {
                int escuro = 1;
                if (DwmSetWindowAttribute(Handle, 20, ref escuro, 4) != 0) DwmSetWindowAttribute(Handle, 19, ref escuro, 4);
                int cantos = 2;   // cantos arredondados (Windows 11)
                DwmSetWindowAttribute(Handle, 33, ref cantos, 4);
                CorDaMoldura(Azul);
            }
            catch { }
        }

        void CorDaMoldura(Color c)
        {
            BackColor = c;
            try { int cor = c.R | (c.G << 8) | (c.B << 16); DwmSetWindowAttribute(Handle, 34, ref cor, 4); } catch { }   // COLORREF 0x00BBGGRR
        }

        // Sem a barra de titulo do Windows: a area do cliente ocupa a janela toda e a barra e a da interface.
        // A faixa de Borda px em volta (Padding) continua redimensionando; encaixe e sombra seguem os do sistema.
        protected override void WndProc(ref Message m)
        {
            if (m.Msg == WM_NCCALCSIZE && m.WParam != IntPtr.Zero)
            {
                // IsZoomed e nao WindowState: nesta mensagem o WinForms ainda nao atualizou o estado
                if (IsZoomed(Handle))
                {
                    // maximizada, a janela passa um pouco da tela: desconta essa sobra
                    var r = (RECT)Marshal.PtrToStructure(m.LParam, typeof(RECT));
                    int fx = GetSystemMetrics(32) + GetSystemMetrics(92), fy = GetSystemMetrics(33) + GetSystemMetrics(92);
                    r.Left += fx; r.Right -= fx; r.Top += fy; r.Bottom -= fy;
                    Marshal.StructureToPtr(r, m.LParam, false);
                }
                m.Result = IntPtr.Zero;
                return;
            }
            if (m.Msg == WM_NCHITTEST && !IsZoomed(Handle))
            {
                int x = (short)((long)m.LParam & 0xFFFF), y = (short)(((long)m.LParam >> 16) & 0xFFFF);
                var p = PointToClient(new Point(x, y));
                int b = Borda + 2, w = ClientSize.Width, h = ClientSize.Height;
                bool esq = p.X < b, dir = p.X >= w - b, cima = p.Y < b, baixo = p.Y >= h - b;
                int onde = cima && esq ? 13 : cima && dir ? 14 : baixo && esq ? 16 : baixo && dir ? 17
                         : esq ? 10 : dir ? 11 : cima ? 12 : baixo ? 15 : 0;
                if (onde != 0) { m.Result = (IntPtr)onde; return; }
            }
            base.WndProc(ref m);
            if (m.Msg == WM_WINDOWPOSCHANGED) GuardarLimites();   // depois de o WinForms aplicar o tamanho dele
        }

        // Sem a moldura do Windows, o WinForms erra a conta ao voltar de maximizada/minimizada (soma a
        // moldura que nao existe e a janela cresce a cada vez): quem guarda e devolve o tamanho normal e a janela.
        Rectangle limitesNormais;
        bool estavaNormal, tamanhoInicial;

        void GuardarLimites()
        {
            if (!IsHandleCreated || !Visible) return;
            bool normal = !IsZoomed(Handle) && !IsIconic(Handle);
            if (normal)
            {
                if (!tamanhoInicial)
                {
                    tamanhoInicial = true;
                    float escala;
                    using (var g = CreateGraphics()) escala = g.DpiX / 96f;
                    var area = Screen.FromHandle(Handle).WorkingArea;
                    var t = new Size(Math.Min((int)(1140 * escala), area.Width), Math.Min((int)(780 * escala), area.Height));
                    limitesNormais = new Rectangle(area.X + (area.Width - t.Width) / 2, area.Y + (area.Height - t.Height) / 2, t.Width, t.Height);
                    estavaNormal = true;
                    Bounds = limitesNormais;
                }
                else if (!estavaNormal && Size != limitesNormais.Size) { estavaNormal = true; Size = limitesNormais.Size; }
                else limitesNormais = Bounds;
            }
            estavaNormal = normal;
        }

        void EnviarEstadoJanela()
        {
            Enviar("{\"tipo\":\"janela_estado\",\"maximizada\":" + (WindowState == FormWindowState.Maximized ? "true" : "false") + "}");
        }

        void AcaoDaJanela(string acao)
        {
            switch (acao)
            {
                case "arrastar": ReleaseCapture(); SendMessage(Handle, WM_NCLBUTTONDOWN, (IntPtr)HTCAPTION, IntPtr.Zero); break;
                case "min": WindowState = FormWindowState.Minimized; break;
                case "max": WindowState = WindowState == FormWindowState.Maximized ? FormWindowState.Normal : FormWindowState.Maximized; break;
                case "fechar": Close(); break;
                case "estado": EnviarEstadoJanela(); break;
            }
        }

        bool iniciouWeb;
        async Task IniciarWeb()
        {
            if (iniciouWeb) return;
            iniciouWeb = true;
            IniciarSupervisor();
            try
            {
                var dadosWeb = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ArgosEPI", "WebView2-" + Programa.Chave);
                var env = await CoreWebView2Environment.CreateAsync(null, dadosWeb);
                await web.EnsureCoreWebView2Async(env);
                var core = web.CoreWebView2;
                core.Settings.AreDevToolsEnabled = Environment.GetEnvironmentVariable("ARGOS_DEVTOOLS") == "1";
                core.Settings.AreDefaultContextMenusEnabled = false;
                core.Settings.IsStatusBarEnabled = false;
                core.Settings.IsZoomControlEnabled = false;
                core.Settings.IsGeneralAutofillEnabled = false;
                core.WebMessageReceived += AoReceber;
                core.NewWindowRequested += (o, a) => { a.Handled = true; AbrirExterno(a.Uri); };
                core.NavigationStarting += (o, a) =>
                {
                    if (a.Uri.StartsWith("data:") || a.Uri == "about:blank" || (baseUi != null && a.Uri.StartsWith(baseUi))) return;
                    a.Cancel = true;
                    AbrirExterno(a.Uri);
                };
                webPronta = true;
                if (urlUi != null) core.Navigate(urlUi);
                else core.NavigateToString(Abertura("Abrindo o servidor..."));
            }
            catch (Exception ex)
            {
                MessageBox.Show("Não foi possível abrir a janela (WebView2).\nRode o instalador do Argos EPI Servidor de novo.\n\n" + ex.Message,
                    Programa.Nome, MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        // tela dos primeiros segundos (antes de a interface responder): ja arrasta e fecha a janela
        static string Abertura(string texto)
        {
            return "<!doctype html><html><body style=\"margin:0;height:100vh;display:grid;place-items:center;background:#080a1d;color:#fff;" +
                   "font-family:'Segoe UI',sans-serif;user-select:none;overflow:hidden\">" +
                   "<button id=\"x\" title=\"Fechar\" style=\"position:fixed;top:10px;right:10px;width:38px;height:34px;border:0;border-radius:10px;" +
                   "background:transparent;color:#fff;font-size:18px;cursor:pointer\" onmouseover=\"this.style.background='#e5484d'\" " +
                   "onmouseout=\"this.style.background='transparent'\">&#10005;</button>" +
                   "<div style=\"text-align:center\"><div style=\"font:700 44px 'Bahnschrift','Segoe UI';letter-spacing:.04em\">ARGOS " +
                   "<span style=\"font-size:16px;background:#fbc343;color:#05075a;padding:4px 8px;border-radius:4px;vertical-align:middle\">SERVIDOR</span></div>" +
                   "<p style=\"opacity:.75;margin-top:14px\">" + WebUtility.HtmlEncode(texto) + "</p></div>" +
                   "<script>var w=window.chrome&&window.chrome.webview;function e(a){if(w)w.postMessage(JSON.stringify({tipo:'janela',acao:a}));}" +
                   "document.getElementById('x').onclick=function(ev){ev.stopPropagation();e('fechar');};" +
                   "document.onmousedown=function(ev){if(ev.button===0&&ev.target.id!=='x')e('arrastar');};</script></body></html>";
        }

        void IniciarSupervisor()
        {
            var motor = Path.Combine(Programa.Pasta, "motor", "ArgosMotor.exe");
            if (!File.Exists(motor))
            {
                MessageBox.Show("A instalação está incompleta (falta o motor do Argos).\nRode o instalador do Argos EPI Servidor de novo.",
                    Programa.Nome, MessageBoxButtons.OK, MessageBoxIcon.Error);
                saindoDeVez = true;
                Close();
                return;
            }
            var psi = new ProcessStartInfo(motor, "supervisor --pai " + Process.GetCurrentProcess().Id +
                                                  " --exe \"" + Application.ExecutablePath + "\"")
            {
                UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Programa.Pasta,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
            };
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            sup = new Process { StartInfo = psi, EnableRaisingEvents = true };
            sup.OutputDataReceived += (s, e) =>
            {
                if (e.Data == null || !e.Data.StartsWith("ARGOS_UI ")) return;
                urlUi = e.Data.Substring(9).Trim();
                var u = new Uri(urlUi);
                baseUi = u.GetLeftPart(UriPartial.Authority) + "/";
                var i = urlUi.IndexOf("?t=", StringComparison.Ordinal);
                token = i >= 0 ? Uri.UnescapeDataString(urlUi.Substring(i + 3)) : "";
                try { BeginInvoke(new Action(() => { if (webPronta) web.CoreWebView2.Navigate(urlUi); })); } catch { }
            };
            sup.ErrorDataReceived += (s, e) => { if (e.Data != null) lock (errosSup) { errosSup.AppendLine(e.Data); if (errosSup.Length > 8000) errosSup.Remove(0, 4000); } };
            sup.Exited += (s, e) => { try { BeginInvoke(new Action(SupervisorSaiu)); } catch { } };
            sup.Start();
            sup.BeginOutputReadLine();
            sup.BeginErrorReadLine();
        }

        void SupervisorSaiu()
        {
            if (saindoDeVez) { Close(); return; }
            string erros;
            lock (errosSup) erros = errosSup.ToString();
            if (erros.Length > 1500) erros = "..." + erros.Substring(erros.Length - 1500);
            Mostrar();
            var r = MessageBox.Show(this, "O servidor do Argos parou de repente." + (erros.Trim().Length > 0 ? "\n\n" + erros.Trim() : "") +
                "\n\nAbrir de novo?", Programa.Nome, MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
            if (r == DialogResult.Yes)
            {
                urlUi = null;
                if (webPronta) web.CoreWebView2.NavigateToString(Abertura("Abrindo o servidor de novo..."));
                lock (errosSup) errosSup.Clear();
                IniciarSupervisor();
            }
            else { saindoDeVez = true; Close(); }
        }

        void AoReceber(object o, CoreWebView2WebMessageReceivedEventArgs e)
        {
            Dictionary<string, object> m;
            try { m = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(e.TryGetWebMessageAsString()); }
            catch { return; }
            object tipo;
            if (!m.TryGetValue("tipo", out tipo)) return;
            switch (tipo as string)
            {
                case "config":
                    object af, cor;
                    if (m.TryGetValue("ao_fechar", out af) && af is string) aoFechar = (string)af;
                    // a moldura da janela acompanha a cor da barra do topo (tema claro/escuro)
                    if (m.TryGetValue("cor", out cor) && cor is string)
                        try { var c = ColorTranslator.FromHtml(((string)cor).Trim()); if (c.ToArgb() != BackColor.ToArgb()) CorDaMoldura(c); } catch { }
                    break;
                case "janela":
                    object ac;
                    if (m.TryGetValue("acao", out ac) && ac is string) AcaoDaJanela((string)ac);
                    break;
                case "esconder": Esconder(); break;
                case "saindo":
                    saindoDeVez = true;
                    Hide();
                    bandeja.Visible = false;
                    EsperarSupervisorEFechar();
                    break;
            }
        }

        void Enviar(string json)
        {
            try { if (webPronta && urlUi != null && web.CoreWebView2 != null) web.CoreWebView2.PostWebMessageAsString(json); } catch { }
        }

        void AoFechar(object s, FormClosingEventArgs e)
        {
            if (saindoDeVez)
            {
                bandeja.Visible = false;
                return;
            }
            if (e.CloseReason == CloseReason.WindowsShutDown || e.CloseReason == CloseReason.TaskManagerClosing)
            {
                saindoDeVez = true;
                bandeja.Visible = false;
                PedirSaida();
                try { if (sup != null) sup.WaitForExit(15000); } catch { }
                return;
            }
            e.Cancel = true;
            if (aoFechar == "bandeja") Esconder();
            else if (aoFechar == "sair") SairDeVez();
            else if (webPronta && urlUi != null) { Mostrar(); Enviar("{\"tipo\":\"pedir_fechar\"}"); }
            else
            {
                var r = MessageBox.Show(this, "Manter o servidor ligado em segundo plano?\n\nSim: a janela some e o ícone fica perto do relógio.\nNão: fecha e desliga o servidor.",
                    Programa.Nome, MessageBoxButtons.YesNoCancel, MessageBoxIcon.Question);
                if (r == DialogResult.Yes) Esconder();
                else if (r == DialogResult.No) SairDeVez();
            }
        }

        void Mostrar()
        {
            if (saindoDeVez) return;
            Show();
            if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
            Activate();
            BringToFront();
        }

        void Esconder()
        {
            Hide();
            if (!avisouBandeja)
            {
                avisouBandeja = true;
                bandeja.ShowBalloonTip(5000, Programa.Nome, "Continuo ligado em segundo plano, analisando as câmeras. Clique no ícone para abrir; para desligar, botão direito > Sair.", ToolTipIcon.Info);
            }
        }

        void SairDeVez()
        {
            if (saindoDeVez && !Visible) return;
            saindoDeVez = true;
            Hide();
            bandeja.Visible = false;
            PedirSaida();
            EsperarSupervisorEFechar();
        }

        void PedirSaida()
        {
            if (sup == null || sup.HasExited) return;
            try
            {
                using (var wc = new WebClient())
                {
                    wc.Headers.Add("X-Argos-Token", token ?? "");
                    wc.Headers.Add("Content-Type", "application/json");
                    wc.UploadString(baseUi + "api/acao", "{\"acao\":\"sair\"}");
                }
            }
            catch { }
        }

        void EsperarSupervisorEFechar()
        {
            Task.Run(() =>
            {
                try
                {
                    if (sup != null && !sup.HasExited && !sup.WaitForExit(45000))
                        Process.Start(new ProcessStartInfo("taskkill", "/PID " + sup.Id + " /T /F") { CreateNoWindow = true, UseShellExecute = false }).WaitForExit(15000);
                }
                catch { }
                try { BeginInvoke(new Action(Close)); } catch { }
            });
        }

        static void AbrirExterno(string url)
        {
            if (string.IsNullOrEmpty(url) || !(url.StartsWith("https://") || url.StartsWith("http://"))) return;
            try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); } catch { }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) { try { bandeja.Visible = false; bandeja.Dispose(); } catch { } }
            base.Dispose(disposing);
        }
    }

    // "Desinstalar" de Aplicativos instalados do Windows. Por padrao guarda a pasta dados
    // (banco, fotos e rostos dos funcionarios): reinstalar volta com tudo.
    static class Desinstalador
    {
        public static void Rodar()
        {
            var nome = Programa.Nome;
            if (MessageBox.Show("Desinstalar o " + nome + "?", nome, MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) return;
            var apagarDados = MessageBox.Show("Apagar também os dados deste servidor?\n\n(banco de dados, fotos e rostos dos funcionários, registros de faltas e gravações)\n\n" +
                "Sim = apagar tudo\nNão = guardar (útil para reinstalar depois)", nome, MessageBoxButtons.YesNoCancel, MessageBoxIcon.Warning);
            if (apagarDados == DialogResult.Cancel) return;

            // programa aberto (talvez na bandeja): pede para fechar e espera desligar o banco
            try
            {
                EventWaitHandle.OpenExisting(Programa.EventoSair).Set();
                var eu = Process.GetCurrentProcess().Id;
                for (int i = 0; i < 60 && Process.GetProcessesByName("ArgosEPI").Any(p => p.Id != eu); i++) Thread.Sleep(1000);
            }
            catch { }   // nao estava aberto

            foreach (var lnk in new[] {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), nome + ".lnk"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), nome + ".lnk") })
                try { if (File.Exists(lnk)) File.Delete(lnk); } catch { }
            try { Registry.CurrentUser.DeleteSubKeyTree(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\ArgosEPIServidor", false); } catch { }
            try { using (var k = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", true)) if (k != null) k.DeleteValue(nome, false); } catch { }
            try { var wv = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ArgosEPI", "WebView2-" + Programa.Chave); if (Directory.Exists(wv)) Directory.Delete(wv, true); } catch { }

            // o .exe em uso nao se apaga: um .cmd escondido termina o servico depois que ele fechar
            var pasta = Programa.Pasta;
            var sb = new StringBuilder();
            sb.AppendLine("@echo off");
            sb.AppendLine("chcp 65001 >nul");
            sb.AppendLine("ping 127.0.0.1 -n 4 >nul");
            var pgctl = Path.Combine(pasta, "bin", "pgsql", "bin", "pg_ctl.exe");
            sb.AppendLine("if exist \"" + pgctl + "\" \"" + pgctl + "\" -D \"" + Path.Combine(pasta, "dados", "pgdata") + "\" -m fast stop >nul 2>&1");
            if (apagarDados == DialogResult.Yes)
                sb.AppendLine("rmdir /s /q \"" + pasta + "\"");
            else
            {
                sb.AppendLine("for /d %%D in (\"" + pasta + "\\*\") do if /i not \"%%~nxD\"==\"dados\" rmdir /s /q \"%%D\"");
                sb.AppendLine("for %%F in (\"" + pasta + "\\*\") do if /i not \"%%~nxF\"==\".env\" del /f /q \"%%F\"");
            }
            sb.AppendLine("del /f /q \"%~f0\"");
            var cmd = Path.Combine(Path.GetTempPath(), "argos-desinstalar-" + Programa.Chave + ".cmd");
            File.WriteAllText(cmd, sb.ToString(), new UTF8Encoding(false));
            Process.Start(new ProcessStartInfo("cmd.exe", "/c \"" + cmd + "\"") { CreateNoWindow = true, UseShellExecute = false, WorkingDirectory = Path.GetTempPath() });
            MessageBox.Show(nome + " foi desinstalado." + (apagarDados == DialogResult.Yes ? "" : "\n\nOs dados ficaram guardados em:\n" + Path.Combine(pasta, "dados")), nome);
        }
    }
}
