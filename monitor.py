"""
monitor.py v14 — Monitor com menu hambúrguer
Telas: Console | Usuários | Dashboard
"""
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import subprocess, threading, sys, os, time, socket, queue, re, json
try:
    import requests; HAS_REQ = True
except: HAS_REQ = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(BASE_DIR,"venv","Scripts","python.exe")
if not os.path.exists(VENV_PY): VENV_PY = sys.executable

BG="#0b0d12"; SURF="#13161f"; BORDER="#252836"; ACCENT="#6c5ce7"; ACCENT2="#a29bfe"
TEXT="#e8eaf0"; MUTED="#6b7280"; SUCCESS="#27ae60"; DANGER="#e74c3c"; WARN="#f39c12"
CON_BG="#050608"; CON_FG="#b0ffc8"
MENU_W = 52

class LogPane:
    def __init__(self, parent):
        self.q = queue.Queue()
        self.txt = scrolledtext.ScrolledText(parent, bg=CON_BG, fg=CON_FG,
            font=("Consolas",9), wrap=tk.WORD, state=tk.DISABLED, relief=tk.FLAT, bd=0)
        self.txt.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.txt.tag_config("ok",   foreground=SUCCESS)
        self.txt.tag_config("err",  foreground=DANGER)
        self.txt.tag_config("warn", foreground=WARN)
        self.txt.tag_config("info", foreground=ACCENT2)
        self._poll()
    def _poll(self):
        try:
            while True:
                tag, line = self.q.get_nowait()
                self.txt.configure(state=tk.NORMAL)
                self.txt.insert(tk.END, line+"\n", tag)
                self.txt.see(tk.END)
                self.txt.configure(state=tk.DISABLED)
        except queue.Empty: pass
        self.txt.after(80, self._poll)
    def log(self, line):
        ll = line.lower()
        if any(w in ll for w in ["erro","error","traceback","exception","failed"]): tag="err"
        elif any(w in ll for w in ["✓","sucesso","ok","running","iniciado","ativo"]): tag="ok"
        elif any(w in ll for w in ["⚠","aviso","warning"]): tag="warn"
        elif any(w in ll for w in ["cloudflare","tunnel","https://","url","ip"]): tag="info"
        else: tag=""
        self.q.put((tag, line))
    def clear(self):
        self.txt.configure(state=tk.NORMAL); self.txt.delete("1.0",tk.END); self.txt.configure(state=tk.DISABLED)

class MonitorApp:
    def __init__(self, root):
        self.root = root
        root.title("Monitor — Argos EPI v14")
        root.configure(bg=BG)
        root.geometry("1000x680"); root.minsize(750,500)
        self._back_proc = self._front_proc = None
        self._running = False
        self._backend_ok = False
        self._paused = False
        self._cf_url = tk.StringVar(value="Aguardando...")
        self._lip_url = tk.StringVar(value=self._local_ip())
        self._status = tk.StringVar(value="⏹ Parado")
        self._current_screen = "console"
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._close)

    def _local_ip(self):
        try:
            s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(("8.8.8.8",80))
            ip=s.getsockname()[0]; s.close(); return f"http://{ip}"
        except: return "http://localhost"

    def _build(self):
        # ── Top bar ──────────────────────────────────────────
        top = tk.Frame(self.root, bg=SURF, pady=8)
        top.pack(fill=tk.X)
        tk.Label(top,text="☰",font=("Segoe UI",14,"bold"),bg=SURF,fg=TEXT,cursor="hand2",
                 padx=12).pack(side=tk.LEFT)
        tk.Label(top,text="Argos EPI v14",font=("Segoe UI",11,"bold"),bg=SURF,fg=TEXT).pack(side=tk.LEFT)
        self._status_lbl = tk.Label(top,textvariable=self._status,font=("Segoe UI",9),bg=SURF,fg=MUTED)
        self._status_lbl.pack(side=tk.LEFT, padx=10)
        btnf = tk.Frame(top, bg=SURF); btnf.pack(side=tk.RIGHT, padx=12)
        self._btn_start = self._btn(btnf,"▶ Iniciar",SUCCESS,self._start)
        self._btn_pause = self._btn(btnf,"⏸ Pausar IA",WARN,self._pause,state=tk.DISABLED)
        self._btn_stop  = self._btn(btnf,"⏹ Encerrar",DANGER,self._stop,state=tk.DISABLED)
        for b in (self._btn_stop,self._btn_pause,self._btn_start): b.pack(side=tk.RIGHT,padx=3)

        # ── URL row ──────────────────────────────────────────
        urow = tk.Frame(self.root, bg=BG, pady=6)
        urow.pack(fill=tk.X, padx=12)
        self._url_row(urow,"🌐 IP Local:",self._lip_url,0)
        self._url_row(urow,"☁ Cloudflare:",self._cf_url,1)
        urow.columnconfigure(0,weight=1); urow.columnconfigure(1,weight=1)

        # ── Body: sidebar + content ───────────────────────────
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill=tk.BOTH, expand=True)

        # Sidebar menu
        self._sidebar = tk.Frame(body, bg=SURF, width=MENU_W)
        self._sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self._sidebar.pack_propagate(False)
        self._menu_btns = {}
        menus = [("💻","console","Console"),("👥","users","Usuários"),("📊","dashboard","Dashboard")]
        for icon, key, label in menus:
            btn = tk.Button(self._sidebar, text=icon, font=("Segoe UI",14), bg=SURF, fg=MUTED,
                            relief=tk.FLAT, cursor="hand2", pady=14,
                            command=lambda k=key: self._show(k))
            btn.pack(fill=tk.X)
            btn.bind("<Enter>", lambda e, b=btn: b.configure(fg=TEXT))
            btn.bind("<Leave>", lambda e, b=btn, k=key: b.configure(fg=TEXT if self._current_screen==k else MUTED))
            self._menu_btns[key] = btn

        # Content area
        self._content = tk.Frame(body, bg=BG)
        self._content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Screens ──────────────────────────────────────────
        self._screens = {}
        self._screens["console"] = self._build_console()
        self._screens["users"]   = self._build_users()
        self._screens["dashboard"] = self._build_dashboard()

        self._show("console")

        # Footer
        tk.Label(self.root,text="AndrosoftStudio • Argos EPI v14",
                 font=("Segoe UI",8),bg=BG,fg=MUTED).pack(pady=4)

    def _url_row(self, parent, label, var, col):
        f = tk.Frame(parent, bg=BG); f.grid(row=0,column=col,padx=8,sticky="ew")
        tk.Label(f,text=label,font=("Segoe UI",8,"bold"),bg=BG,fg=MUTED).pack(side=tk.LEFT)
        e = tk.Entry(f,textvariable=var,font=("Consolas",8),bg=SURF,fg=ACCENT2,
                     relief=tk.FLAT,readonlybackground=SURF,state="readonly",width=36)
        e.pack(side=tk.LEFT,padx=4)
        tk.Button(f,text="📋",font=("Segoe UI",9),bg=BORDER,fg=TEXT,relief=tk.FLAT,cursor="hand2",
                  command=lambda v=var: self._copy(v.get())).pack(side=tk.LEFT)

    def _btn(self, parent, text, color, cmd, state=tk.NORMAL):
        return tk.Button(parent,text=text,font=("Segoe UI",9,"bold"),bg=color,fg="#fff",
                         relief=tk.FLAT,cursor="hand2",padx=10,pady=5,command=cmd,state=state)

    def _copy(self, text):
        self.root.clipboard_clear(); self.root.clipboard_append(text); self.root.update()
        self._log_sistema(f"[Clipboard] Copiado: {text}")

    def _show(self, key):
        self._current_screen = key
        for k, frame in self._screens.items():
            frame.pack_forget()
        self._screens[key].pack(fill=tk.BOTH, expand=True)
        for k, btn in self._menu_btns.items():
            btn.configure(fg=TEXT if k==key else MUTED,
                         bg=ACCENT if k==key else SURF)
        if key == "users" and self._running: self._refresh_users()
        if key == "dashboard" and self._running: self._refresh_dashboard()

    # ── Console screen ────────────────────────────────────────
    def _build_console(self):
        frame = tk.Frame(self._content, bg=BG)
        nb_style = ttk.Style()
        nb_style.theme_use("default")
        nb_style.configure("C.TNotebook", background=BG, borderwidth=0)
        nb_style.configure("C.TNotebook.Tab", background=SURF, foreground=MUTED,
                           padding=[12,5], borderwidth=0, font=("Segoe UI",9))
        nb_style.map("C.TNotebook.Tab",background=[("selected",ACCENT)],foreground=[("selected",TEXT)])
        nb = ttk.Notebook(frame, style="C.TNotebook")
        nb.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        for tab_name in ["  Sistema  ","  Backend  ","  Cloudflare  "]:
            f = tk.Frame(nb, bg=BG); nb.add(f, text=tab_name)
        self._log_sistema_pane  = LogPane(nb.nametowidget(nb.tabs()[0]))
        self._log_backend_pane  = LogPane(nb.nametowidget(nb.tabs()[1]))
        self._log_cf_pane       = LogPane(nb.nametowidget(nb.tabs()[2]))
        return frame

    def _log_sistema(self, line): self._log_sistema_pane.log(line)
    def _log_backend(self, line): self._log_backend_pane.log(line)
    def _log_cf(self, line): self._log_cf_pane.log(line)

    # ── Users screen ─────────────────────────────────────────
    def _build_users(self):
        frame = tk.Frame(self._content, bg=BG)
        top = tk.Frame(frame, bg=BG, pady=8); top.pack(fill=tk.X, padx=12)
        tk.Label(top,text="👥 Usuários",font=("Segoe UI",13,"bold"),bg=BG,fg=TEXT).pack(side=tk.LEFT)
        self._filter_var = tk.StringVar(value="todos")
        filters = [("Todos","todos"),("Online","online"),("Offline","offline"),("Transmitindo","streaming")]
        for lbl, val in filters:
            tk.Radiobutton(top,text=lbl,variable=self._filter_var,value=val,
                          bg=BG,fg=MUTED,selectcolor=SURF,activebackground=BG,
                          command=self._refresh_users).pack(side=tk.LEFT,padx=6)
        tk.Button(top,text="⟳ Atualizar",font=("Segoe UI",9),bg=BORDER,fg=TEXT,
                  relief=tk.FLAT,cursor="hand2",command=self._refresh_users).pack(side=tk.RIGHT,padx=4)

        paned = tk.PanedWindow(frame, orient=tk.HORIZONTAL, bg=BG, sashwidth=4, sashrelief=tk.FLAT)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Lista
        left = tk.Frame(paned, bg=SURF); paned.add(left, minsize=220)
        self._user_listbox = tk.Listbox(left, bg=SURF, fg=TEXT, font=("Segoe UI",9),
                                        selectbackground=ACCENT, relief=tk.FLAT, bd=0,
                                        activestyle="none")
        self._user_listbox.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._user_listbox.bind("<<ListboxSelect>>", self._on_user_select)

        # Detalhe
        right = tk.Frame(paned, bg=BG); paned.add(right, minsize=300)
        self._user_detail = tk.Frame(right, bg=BG)
        self._user_detail.pack(fill=tk.BOTH, expand=True)
        self._user_data = {}
        self._build_user_detail_empty()
        return frame

    def _build_user_detail_empty(self):
        for w in self._user_detail.winfo_children(): w.destroy()
        tk.Label(self._user_detail,text="Selecione um usuário",font=("Segoe UI",10),
                 bg=BG,fg=MUTED).pack(expand=True)

    def _refresh_users(self):
        if not HAS_REQ or not self._running or not self._backend_ok: return
        def do():
            try:
                r = requests.get("http://localhost:8088/admin/users", timeout=4)
                users = r.json().get("users",[])
                flt = self._filter_var.get()
                if flt == "online":    users = [u for u in users if u.get("streams_ativos",0)>0]
                elif flt == "offline": users = [u for u in users if u.get("streams_ativos",0)==0]
                elif flt == "streaming": users = [u for u in users if u.get("streams_ativos",0)>0]
                self._user_data = {u['id']:u for u in users}
                self.root.after(0, lambda: self._update_user_list(users))
            except Exception as e:
                self._log_sistema(f"[Users] Erro: {e}")
        threading.Thread(target=do, daemon=True).start()

    def _update_user_list(self, users):
        self._user_listbox.delete(0, tk.END)
        for u in users:
            status = "🟢" if u.get("streams_ativos",0)>0 else "⚫"
            bloq = "🔒" if u.get("bloqueado") else ""
            self._user_listbox.insert(tk.END, f"  {status} {bloq} {u.get('nome','?')}")

    def _on_user_select(self, event):
        sel = self._user_listbox.curselection()
        if not sel: return
        idx = sel[0]
        uids = list(self._user_data.keys())
        if idx >= len(uids): return
        uid = uids[idx]
        u = self._user_data[uid]
        self._show_user_detail(u)

    def _show_user_detail(self, u):
        for w in self._user_detail.winfo_children(): w.destroy()
        f = tk.Frame(self._user_detail, bg=BG, padx=16, pady=12)
        f.pack(fill=tk.BOTH, expand=True)
        tk.Label(f,text=u.get('nome','?'),font=("Segoe UI",12,"bold"),bg=BG,fg=TEXT).grid(row=0,column=0,columnspan=2,sticky="w",pady=(0,10))
        infos = [("📧 Email",u.get('email','-')),("📄 CPF/CNPJ",u.get('doc','-')),
                 ("📱 Telefone",u.get('telefone','-')),("🏭 Setor",u.get('setor','-')),
                 ("📡 Streams",str(u.get('streams_ativos',0))),
                 ("🔒 Bloqueado","Sim" if u.get('bloqueado') else "Não"),
                 ("⚙ Restrições",", ".join(u.get('restricoes',[])) or "Nenhuma")]
        for i,(k,v) in enumerate(infos):
            tk.Label(f,text=k+":",font=("Segoe UI",9),bg=BG,fg=MUTED).grid(row=i+1,column=0,sticky="w",pady=2)
            tk.Label(f,text=v,font=("Segoe UI",9),bg=BG,fg=TEXT).grid(row=i+1,column=1,sticky="w",padx=8,pady=2)
        # Botões de ação
        bframe = tk.Frame(f, bg=BG); bframe.grid(row=len(infos)+2,column=0,columnspan=2,pady=12,sticky="w")
        bloq_txt = "🔓 Desbloquear" if u.get('bloqueado') else "🔒 Bloquear"
        tk.Button(bframe,text=bloq_txt,font=("Segoe UI",9),bg=WARN,fg="#fff",relief=tk.FLAT,
                  cursor="hand2",padx=10,pady=5,
                  command=lambda: self._toggle_block(u['id'], not u.get('bloqueado'))).pack(side=tk.LEFT,padx=4)
        # Streams ativos
        sids = u.get('stream_ids',[])
        if sids:
            tk.Label(f,text="Streams ativos:",font=("Segoe UI",9,"bold"),bg=BG,fg=TEXT).grid(
                row=len(infos)+3,column=0,columnspan=2,sticky="w",pady=(10,4))
            for sid in sids:
                sf = tk.Frame(f, bg=SURF, padx=8, pady=4)
                sf.grid(row=len(infos)+4+sids.index(sid), column=0, columnspan=2, sticky="ew", pady=2)
                tk.Label(sf,text=f"📹 {sid}",font=("Consolas",8),bg=SURF,fg=ACCENT2).pack(side=tk.LEFT)

    def _toggle_block(self, uid, block):
        if not HAS_REQ: return
        def do():
            try:
                requests.post(f"http://localhost:8088/admin/users/{uid}/block",
                              json={"block":block}, timeout=4)
                self._refresh_users()
            except: pass
        threading.Thread(target=do, daemon=True).start()

    # ── Dashboard screen ──────────────────────────────────────
    def _build_dashboard(self):
        frame = tk.Frame(self._content, bg=BG)
        tk.Label(frame,text="📊 Dashboard",font=("Segoe UI",13,"bold"),bg=BG,fg=TEXT).pack(anchor="w",padx=16,pady=10)
        grid = tk.Frame(frame, bg=BG); grid.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

        # Cards de métricas
        self._metric_cards = {}
        card_defs = [("cpu","🖥 CPU","0%",0,0),("ram","🧠 RAM","0%",0,1),
                     ("disk","💾 Disco","0%",0,2),("gpu","🎮 GPU","N/A",0,3),
                     ("users","👥 Usuários ativos","0",1,0),("streams","📹 Streams ativos","0",1,1),
                     ("total_users","👤 Total cadastros","0",1,2)]
        for key, lbl, default, row, col in card_defs:
            card = tk.Frame(grid, bg=SURF, padx=16, pady=14, relief=tk.FLAT)
            card.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")
            grid.columnconfigure(col, weight=1); grid.rowconfigure(row, weight=1)
            tk.Label(card,text=lbl,font=("Segoe UI",8),bg=SURF,fg=MUTED).pack(anchor="w")
            val_lbl = tk.Label(card,text=default,font=("Segoe UI",18,"bold"),bg=SURF,fg=TEXT)
            val_lbl.pack(anchor="w",pady=(4,0))
            bar_frame = tk.Frame(card, bg=BORDER, height=6); bar_frame.pack(fill=tk.X, pady=(6,0))
            bar = tk.Frame(bar_frame, bg=ACCENT, height=6); bar.place(x=0,y=0,relwidth=0,height=6)
            self._metric_cards[key] = {"label": val_lbl, "bar": bar, "bar_frame": bar_frame}

        tk.Button(frame,text="⟳ Atualizar",font=("Segoe UI",9),bg=BORDER,fg=TEXT,
                  relief=tk.FLAT,cursor="hand2",command=self._refresh_dashboard).pack(pady=8)
        self._dash_auto = True
        threading.Thread(target=self._dash_loop, daemon=True).start()
        return frame

    def _dash_loop(self):
        while True:
            time.sleep(3)
            if self._running and self._current_screen == "dashboard":
                self.root.after(0, self._refresh_dashboard)

    def _refresh_dashboard(self):
        if not HAS_REQ or not self._running or not self._backend_ok: return
        def do():
            try:
                r = requests.get("http://localhost:8088/dashboard/metrics", timeout=4)
                m = r.json()
                self.root.after(0, lambda: self._update_cards(m))
            except: pass
        threading.Thread(target=do, daemon=True).start()

    def _update_cards(self, m):
        updates = {
            "cpu": (f"{m.get('cpu',0):.1f}%", m.get('cpu',0)/100),
            "ram": (f"{m.get('ram_pct',0):.1f}%", m.get('ram_pct',0)/100),
            "disk": (f"{m.get('disk_pct',0):.1f}%", m.get('disk_pct',0)/100),
            "gpu": (f"{m.get('gpu_pct','N/A')}%" if m.get('gpu_pct') is not None else "N/A", (m.get('gpu_pct',0) or 0)/100),
            "users": (str(m.get('active_users',0)), 0),
            "streams": (str(m.get('active_streams',0)), 0),
            "total_users": (str(m.get('total_users',0)), 0),
        }
        for key, (text, pct) in updates.items():
            card = self._metric_cards.get(key)
            if card:
                card["label"].configure(text=text)
                color = DANGER if pct > 0.85 else WARN if pct > 0.6 else SUCCESS
                bf = card["bar_frame"]; bf.update_idletasks()
                w = bf.winfo_width()
                card["bar"].configure(bg=color); card["bar"].place(relwidth=min(pct,1), height=6)

    # ── Controles ─────────────────────────────────────────────
    def _start(self):
        if self._running: return
        self._running = True
        self._btn_start.configure(state=tk.DISABLED)
        self._btn_pause.configure(state=tk.NORMAL)
        self._btn_stop.configure(state=tk.NORMAL)
        self._set_status("🟡 Iniciando...", WARN)
        for pane in [self._log_sistema_pane, self._log_backend_pane, self._log_cf_pane]: pane.clear()
        
        # Write the correct backend URL (using real LAN IP) into config.js
        try:
            raw_ip = self._local_ip()  # returns "http://192.168.x.x"
            backend_url = raw_ip.rstrip('/') + ':8088' if raw_ip else 'http://localhost:8088'
            config_path = os.path.join(BASE_DIR, "frontend", "config.js")
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(f'window.BACKEND_URL = "{backend_url}";\n')
            self._log_sistema(f"[Sistema] ✓ config.js atualizado: {backend_url}")
        except Exception as e:
            self._log_sistema(f"[Sistema] ⚠ Não foi possível atualizar config.js: {e}")

        threading.Thread(target=self._run_backend,  daemon=True).start()
        threading.Thread(target=self._poll_links,   daemon=True).start()

    def _pause(self):
        def do():
            try:
                self._paused = not self._paused
                if HAS_REQ: requests.post("http://localhost:8088/pause",json={"paused":self._paused},timeout=3)
                lbl = "▶ Retomar IA" if self._paused else "⏸ Pausar IA"
                self.root.after(0, lambda: self._btn_pause.configure(text=lbl))
                self._log_sistema(f"[Monitor] IA {'pausada' if self._paused else 'retomada'}")
            except Exception as e: self._log_sistema(f"[Monitor] Erro: {e}")
        threading.Thread(target=do, daemon=True).start()

    def _stop(self):
        if not messagebox.askyesno("Encerrar","Deseja encerrar o sistema?"): return
        self._running = False; self._set_status("⏹ Encerrando...", DANGER)
        def kill():
            if self._back_proc:
                try: self._back_proc.terminate()
                except: pass
            self.root.after(600, self._after_stop)
        threading.Thread(target=kill, daemon=True).start()

    def _after_stop(self):
        self._backend_ok = False
        self._btn_start.configure(state=tk.NORMAL)
        self._btn_pause.configure(state=tk.DISABLED, text="⏸ Pausar IA")
        self._btn_stop.configure(state=tk.DISABLED)
        self._set_status("⏹ Parado", MUTED)
        self._cf_url.set("Aguardando...")

    def _set_status(self, text, color=TEXT):
        self._status.set(text); self._status_lbl.configure(fg=color)

    # ── Processos ─────────────────────────────────────────────
    def _run_backend(self):
        self._log_sistema("[Sistema] Iniciando Backend Flask (porta 8088)...")
        try:
            proc = subprocess.Popen([VENV_PY, os.path.join(BASE_DIR,"run.py")],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=BASE_DIR)
            self._back_proc = proc
            self._log_sistema(f"[Sistema] Backend PID: {proc.pid}")
            pat = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com")
            for line in iter(proc.stdout.readline,''):
                line = line.rstrip()
                self._log_backend(line)
                m = pat.search(line)
                if m:
                    url = m.group(0)
                    self.root.after(0, lambda u=url: self._cf_url.set(u))
                    self._log_cf(f"✓ URL Cloudflare: {url}")
                    self._backend_ok = True
                    self.root.after(0, lambda: self._set_status("🟢 Sistema ativo em 8088", SUCCESS))
                    try:
                        import webbrowser; webbrowser.open("http://localhost:8088")
                    except: pass
        except Exception as e: self._log_backend(f"[ERRO] {e}")
        self._log_sistema("[Sistema] Processo encerrado.")

    def _poll_links(self):
        if not HAS_REQ: return
        time.sleep(10)
        while self._running:
            try:
                r = requests.get("http://localhost:8088/tunnels", timeout=3)
                d = r.json()
                cf = d.get("cloudflare_url"); lip = d.get("local_ip_url")
                if cf and cf != self._cf_url.get(): self.root.after(0, lambda u=cf: self._cf_url.set(u))
                if lip: self.root.after(0, lambda u=lip: self._lip_url.set(u))
            except: pass
            time.sleep(5)

    def _close(self):
        if self._running and messagebox.askyesno("Sair","Encerrar o sistema antes de fechar?"):
            self._running = False
            if self._back_proc:
                try: self._back_proc.terminate()
                except: pass
        self.root.destroy()

def main():
    root = tk.Tk(); root.resizable(True, True)
    try: root.iconbitmap(os.path.join(BASE_DIR,"icon.ico"))
    except: pass
    MonitorApp(root); root.mainloop()

if __name__ == "__main__": main()
