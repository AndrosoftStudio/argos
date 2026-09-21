# -*- coding: utf-8 -*-
"""
Argos EPI v16 - Downloader de binarios e modelos
Uso: python downloader.py <pasta_bin> <item1> <item2> ...
Itens validos: uv, aria2c, cloudflared, modelos
               (+ ffmpeg, ffprobe, ytdlp, gallery-dl, jq, wget, rhash, exiftool, streamlink, magick)
"""
import urllib.request, os, sys, threading, time, zipfile, shutil, subprocess, json

# --- Progress bar -------------------------------------------------------------
def _prog(done_arr, total_ref, stop_ev, label=''):
    t0 = time.time()
    while not stop_ev.is_set():
        got   = sum(done_arr)
        el    = max(time.time() - t0, 0.01)
        speed = got / el / 1048576
        if total_ref[0]:
            pct = min(int(got * 100 / total_ref[0]), 100)
            bar = '#' * (pct // 5) + '-' * (20 - pct // 5)
            print(f'\r  [{bar}] {pct:3d}%  {speed:5.1f} MB/s  {label}', end='', flush=True)
        else:
            print(f'\r  {got/1048576:.1f} MB  {speed:.1f} MB/s  {label}', end='', flush=True)
        stop_ev.wait(0.35)
    print()

# --- Download paralelo (Range) ------------------------------------------------
def dl(url, dest, parts=8, label=''):
    os.makedirs(os.path.dirname(dest) if os.path.dirname(dest) else '.', exist_ok=True)
    total = 0; sup_range = False
    try:
        rq = urllib.request.Request(url, method='HEAD')
        rq.add_header('User-Agent', 'Mozilla/5.0')
        with urllib.request.urlopen(rq, timeout=20) as r:
            total = int(r.headers.get('Content-Length', 0))
            sup_range = 'bytes' in r.headers.get('Accept-Ranges', '')
    except Exception:
        pass

    total_ref = [total]; done = [0]*max(parts,1); errs = []; ev = threading.Event()
    thr_prog = threading.Thread(target=_prog, args=(done, total_ref, ev, label), daemon=True)
    thr_prog.start()

    def fetch(i, s, e, tmp):
        try:
            rq2 = urllib.request.Request(url)
            rq2.add_header('User-Agent', 'Mozilla/5.0')
            rq2.add_header('Range', f'bytes={s}-{e}')
            with urllib.request.urlopen(rq2, timeout=180) as r2, open(tmp, 'wb') as f:
                while True:
                    buf = r2.read(131072)
                    if not buf: break
                    f.write(buf); done[i] += len(buf)
        except Exception as ex:
            errs.append(str(ex))

    if sup_range and total > 0 and parts > 1:
        sz   = total // parts
        tmps = [dest + f'.p{i}' for i in range(parts)]
        ths  = [threading.Thread(target=fetch,
                args=(i, i*sz, (i*sz+sz-1) if i < parts-1 else total-1, tmps[i]), daemon=True)
                for i in range(parts)]
        for t in ths: t.start()
        for t in ths: t.join()
        ev.set(); thr_prog.join()
        if not errs:
            with open(dest, 'wb') as out:
                for tmp in tmps:
                    if os.path.exists(tmp):
                        with open(tmp, 'rb') as p: out.write(p.read())
                        os.remove(tmp)
            return
        for tmp in tmps:
            try: os.remove(tmp)
            except: pass
        print('  Fallback direto...')

    # Fallback: download direto (1 conexão)
    ev2 = threading.Event(); done2 = [0]
    thr2 = threading.Thread(target=_prog, args=(done2, [total], ev2, label + ' (direto)'), daemon=True)
    thr2.start()
    rq3 = urllib.request.Request(url)
    rq3.add_header('User-Agent', 'Mozilla/5.0')
    with urllib.request.urlopen(rq3, timeout=300) as r3, open(dest, 'wb') as f:
        while True:
            buf = r3.read(262144)
            if not buf: break
            f.write(buf); done2[0] += len(buf)
    ev2.set(); thr2.join()

# --- ZIP helper ---------------------------------------------------------------
def dl_zip(url, dest_exe, suffix, tmp_dir, label=''):
    tmp = os.path.join(tmp_dir, f'_epi_{suffix.replace("/","_").replace("(","").replace(")","")}.zip')
    dl(url, tmp, label=label or suffix)
    with open(tmp, 'rb') as f: magic = f.read(4)
    if magic[:2] != b'PK':
        alt = url.split('?')[0]
        if alt != url:
            print('  Redirect detectado, tentando URL direta...')
            dl(alt, tmp, label=label or suffix)
            with open(tmp, 'rb') as f: magic = f.read(4)
        if magic[:2] != b'PK':
            print(f'  ERRO: ZIP invalido para {suffix}')
            try: os.remove(tmp)
            except: pass
            return False
    found = False
    with zipfile.ZipFile(tmp) as z:
        for n in z.namelist():
            if n.lower().endswith(suffix.lower()):
                with z.open(n) as src, open(dest_exe, 'wb') as dst: dst.write(src.read())
                print(f'  OK  {os.path.basename(dest_exe)}')
                found = True; break
    if not found: print(f'  AVISO: {suffix} nao encontrado no ZIP')
    try: os.remove(tmp)
    except: pass
    return found

# --- aria2c download (16 conexões reais) -------------------------------------
def dl_a2(url, dest, aria2c_bin, conn=16, label=''):
    """Baixa usando aria2c com múltiplas conexões simultâneas."""
    out_dir = os.path.dirname(dest) or '.'
    cmd = [
        aria2c_bin,
        f'--split={conn}',
        f'--max-connection-per-server={conn}',
        '--min-split-size=1M',
        '--file-allocation=none',
        '--console-log-level=warn',
        '--download-result=hide',
        '--summary-interval=1',
        '--show-console-readout=true',
        '-d', out_dir,
        '-o', os.path.basename(dest),
        url
    ]
    print(f'  {label} (aria2c x{conn})...')
    try:
        r = subprocess.run(cmd, timeout=600)
        if r.returncode == 0 and os.path.exists(dest):
            sz = os.path.getsize(dest) / 1048576
            print(f'  OK  {os.path.basename(dest)} ({sz:.1f} MB)')
            return True
        print(f'  AVISO: aria2c rc={r.returncode}, usando fallback...')
    except Exception as e:
        print(f'  AVISO: aria2c falhou ({e}), usando fallback...')
    return False

def best_dl(url, dest, aria2c_bin, label=''):
    """Usa aria2c se disponível, senão dl() paralelo."""
    if os.path.exists(aria2c_bin):
        if dl_a2(url, dest, aria2c_bin, conn=16, label=label):
            return
    dl(url, dest, parts=8, label=label)
    if os.path.exists(dest):
        sz = os.path.getsize(dest) / 1048576
        print(f'  OK  {os.path.basename(dest)} ({sz:.1f} MB)')

# --- FFmpeg -------------------------------------------------------------------
def dl_ffmpeg(bdir, need, a2c, tmp_dir):
    url     = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
    tmp_zip = os.path.join(tmp_dir, '_epi_ffmpeg.zip')
    tmp_ex  = os.path.join(tmp_dir, '_epi_ffmpeg_ex')
    print('  FFmpeg/ffprobe...')
    if not (dl_a2(url, tmp_zip, a2c, 16, 'ffmpeg.zip') if os.path.exists(a2c) else False):
        dl(url, tmp_zip, parts=8, label='ffmpeg.zip')
    print('  Extraindo...')
    if os.path.exists(tmp_ex): shutil.rmtree(tmp_ex)
    with zipfile.ZipFile(tmp_zip) as z: z.extractall(tmp_ex)
    for rt, _, fs in os.walk(tmp_ex):
        for n in fs:
            lo = n.lower()
            if lo == 'ffprobe.exe' and 'ffprobe' in need:
                shutil.copy2(os.path.join(rt, n), os.path.join(bdir, 'ffprobe.exe'))
                print('  OK  ffprobe.exe')
            if lo == 'ffmpeg.exe' and 'ffmpeg' in need:
                shutil.copy2(os.path.join(rt, n), os.path.join(bdir, 'ffmpeg.exe'))
                print('  OK  ffmpeg.exe')
    for p in [tmp_zip, tmp_ex]:
        try:
            (shutil.rmtree if os.path.isdir(p) else os.remove)(p)
        except: pass

# ================================================================================
# MAIN
# ================================================================================
bdir   = sys.argv[1]
need   = sys.argv[2:]
tmpdir = os.environ.get('TEMP', bdir)
a2c    = os.path.join(bdir, 'aria2c.exe')
os.makedirs(bdir, exist_ok=True)

print(f'  Destino : {bdir}')
print(f'  Itens   : {" ".join(need)}')
print()

# -- uv ------------------------------------------------------------------------
if 'uv' in need:
    print('  uv.exe (8 partes paralelas)...')
    dl_zip(
        'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip',
        os.path.join(bdir, 'uv.exe'), 'uv.exe', tmpdir, 'uv'
    )

# -- aria2c --------------------------------------------------------------------
if 'aria2c' in need:
    print('  aria2c.exe...')
    dl_zip(
        'https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip',
        a2c, 'aria2c.exe', tmpdir, 'aria2c'
    )

# -- cloudflared ---------------------------------------------------------------
if 'cloudflared' in need:
    best_dl(
        'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe',
        os.path.join(bdir, 'cloudflared.exe'), a2c, 'cloudflared.exe'
    )

# -- Modelos YOLO -------------------------------------------------------------
if 'modelos' in need:
    # pasta models/ fica na raiz do projeto (pai de bin/)
    root_dir   = os.path.dirname(os.path.abspath(bdir))
    models_dir = os.path.join(root_dir, 'models')
    os.makedirs(models_dir, exist_ok=True)
    print(f'  Modelos YOLO -> {models_dir}')

    # URLs oficiais do Ultralytics/YOLO
    # yolo26n -> YOLOv8n (nano, ~6MB), yolo26s -> YOLOv8s (small, ~22MB)
    MODELS = [
        ('yolo26n.pt', 'https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt'),
        ('yolo26s.pt', 'https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11s.pt'),
    ]

    # Baixar ambos em PARALELO com aria2c se disponível, senão sequencial
    if os.path.exists(a2c):
        threads = []
        for fname, url in MODELS:
            dest = os.path.join(models_dir, fname)
            if os.path.exists(dest):
                print(f'  {fname} ja existe, pulando.')
                continue
            t = threading.Thread(
                target=dl_a2,
                args=(url, dest, a2c, 8, fname),
                daemon=True
            )
            threads.append(t)
        for t in threads: t.start()
        for t in threads: t.join()
    else:
        # Sem aria2c: dl() paralelo por arquivo, sequencial entre arquivos
        for fname, url in MODELS:
            dest = os.path.join(models_dir, fname)
            if os.path.exists(dest):
                print(f'  {fname} ja existe, pulando.')
                continue
            dl(url, dest, parts=8, label=fname)
            if os.path.exists(dest):
                print(f'  OK  {fname} ({os.path.getsize(dest)/1048576:.1f} MB)')

# -- Extras opcionais ---------------------------------------------------------
if 'ffmpeg' in need or 'ffprobe' in need:
    dl_ffmpeg(bdir, need, a2c, tmpdir)

if 'ytdlp' in need:
    best_dl('https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe',
            os.path.join(bdir, 'yt-dlp.exe'), a2c, 'yt-dlp.exe')

if 'gallery-dl' in need:
    best_dl('https://github.com/mikf/gallery-dl/releases/latest/download/gallery-dl.exe',
            os.path.join(bdir, 'gallery-dl.exe'), a2c, 'gallery-dl.exe')

if 'jq' in need:
    best_dl('https://github.com/jqlang/jq/releases/latest/download/jq-windows-amd64.exe',
            os.path.join(bdir, 'jq.exe'), a2c, 'jq.exe')

if 'wget' in need:
    best_dl('https://eternallybored.org/misc/wget/1.21.4/64/wget.exe',
            os.path.join(bdir, 'wget.exe'), a2c, 'wget.exe')

if 'rhash' in need:
    dl_zip(
        'https://phoenixnap.dl.sourceforge.net/project/rhash/rhash/1.4.5/rhash-1.4.5-english-win64.zip?viasf=1',
        os.path.join(bdir, 'rhash.exe'), 'rhash.exe', tmpdir
    )

if 'exiftool' in need:
    dst   = os.path.join(bdir, 'exiftool.exe')
    src_k = os.path.join(bdir, 'exiftool(-k).exe')
    dl_zip('https://exiftool.org/exiftool-13.52_64.zip', dst, 'exiftool(-k).exe', tmpdir)
    if os.path.exists(src_k) and not os.path.exists(dst):
        shutil.move(src_k, dst)
        print('  OK  exiftool.exe (renomeado)')

if 'streamlink' in need:
    print('  streamlink.exe...')
    try:
        rq = urllib.request.Request('https://api.github.com/repos/streamlink/windows-builds/releases/latest')
        rq.add_header('User-Agent', 'Mozilla/5.0')
        with urllib.request.urlopen(rq, timeout=20) as r: rel = json.loads(r.read())
        asset_url = next((
            a['browser_download_url'] for a in rel.get('assets', [])
            if a['name'].lower().endswith('.zip')
            and ('x86_64' in a['name'] or 'x64' in a['name'])
            and 'source' not in a['name']
        ), None)
        if asset_url:
            dl_zip(asset_url, os.path.join(bdir, 'streamlink.exe'), 'streamlink.exe', tmpdir)
        else:
            print('  AVISO: asset .zip do streamlink nao encontrado')
    except Exception as e:
        print(f'  ERRO streamlink: {e}')

if 'magick' in need:
    tmp7z = os.path.join(tmpdir, '_epi_magick.7z')
    best_dl('https://imagemagick.org/archive/binaries/ImageMagick-7.1.2-17-portable-Q16-x64.7z',
            tmp7z, a2c, 'magick.7z')
    try:
        import py7zr
        with py7zr.SevenZipFile(tmp7z, mode='r') as z:
            targets = [n for n in z.getnames() if n.lower().endswith('magick.exe')]
            if targets:
                z.extract(path=bdir, targets=targets)
                for t in targets:
                    src = os.path.join(bdir, t); dst = os.path.join(bdir, 'magick.exe')
                    if os.path.exists(src) and src != dst: shutil.move(src, dst)
                print('  OK  magick.exe')
            else: print('  AVISO: magick.exe nao encontrado no .7z')
    except ImportError: print('  AVISO: py7zr nao instalado')
    except Exception as e: print(f'  ERRO ao extrair magick: {e}')
    try: os.remove(tmp7z)
    except: pass

print()
print('  Todos os itens processados.')
