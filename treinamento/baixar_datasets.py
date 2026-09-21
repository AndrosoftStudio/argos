"""
baixar_datasets.py - Baixa e extrai os datasets publicos de EPI, todas as fontes em paralelo.

Fontes do Hugging Face sao baixadas com `git clone` + Git LFS quando o git esta instalado:
a API do HF limita cada IP a 3000 requisicoes a cada 5 min, e datasets com dezenas de
milhares de imagens pequenas ficam parados nesse limite. O clone baixa as imagens em lote.

Uso:
  python treinamento/baixar_datasets.py
  python treinamento/baixar_datasets.py --fontes sh17 jhboyo_ppe
  python treinamento/baixar_datasets.py --sem-nao-comercial
"""
import argparse
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote

import requests

import comum
from fontes import selecionar_fontes


def log(rotulo: str, msg: str):
    print(f"[{rotulo}] {msg}", flush=True)


def apagar(caminho: str):
    """rmtree que tambem remove arquivos somente-leitura (packs do .git no Windows)."""
    def forcar(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    if os.path.exists(caminho):
        shutil.rmtree(caminho, onexc=forcar)


def baixar_url(url: str, destino: str, rotulo: str, tentativas: int = 5):
    """Download com retomada (HTTP Range). O arquivo final so aparece quando completo."""
    nome = os.path.basename(destino)
    if os.path.exists(destino):
        if not destino.lower().endswith('.zip') or zipfile.is_zipfile(destino):
            log(rotulo, f"ja existe: {nome}")
            return
        os.remove(destino)  # zip incompleto de um download anterior
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    parcial = destino + '.parcial'
    for tentativa in range(1, tentativas + 1):
        feito = os.path.getsize(parcial) if os.path.exists(parcial) else 0
        headers = {'Range': f'bytes={feito}-'} if feito else {}
        try:
            with requests.get(url, headers=headers, stream=True, timeout=60) as r:
                if r.status_code == 416:  # o parcial ja estava completo
                    break
                if r.status_code == 429:  # limite de requisicoes do HF: espera a janela abrir
                    espera = int(r.headers.get('Retry-After', 60)) + 1
                    log(rotulo, f"limite de requisicoes; esperando {espera}s")
                    time.sleep(espera)
                    continue
                r.raise_for_status()
                if feito and r.status_code != 206:
                    feito = 0
                total = int(r.headers.get('Content-Length', 0)) + feito
                ultimo = time.time()
                with open(parcial, 'ab' if feito else 'wb') as f:
                    for bloco in r.iter_content(chunk_size=1 << 20):
                        f.write(bloco)
                        feito += len(bloco)
                        if time.time() - ultimo > 15:
                            pct = f" ({feito * 100 / total:.0f}%)" if total else ''
                            log(rotulo, f"{nome}: {feito / 1048576:,.0f} MB{pct}")
                            ultimo = time.time()
            break
        except (requests.RequestException, OSError) as ex:
            log(rotulo, f"falha em {nome} ({ex}); tentativa {tentativa}/{tentativas}")
            time.sleep(3 * tentativa)
    else:
        raise RuntimeError(f"nao foi possivel baixar {url}")
    os.replace(parcial, destino)
    log(rotulo, f"{nome} completo")


def extrair(arquivo: str, pasta_destino: str, rotulo: str):
    marca = os.path.join(pasta_destino, '.extraido_ok')
    if os.path.exists(marca):
        return
    log(rotulo, f"extraindo {os.path.basename(arquivo)}...")
    if arquivo.lower().endswith(('.tar.gz', '.tgz', '.tar')):
        with tarfile.open(arquivo) as t:
            t.extractall(pasta_destino, filter='data')
    else:
        with zipfile.ZipFile(arquivo) as z:
            z.extractall(pasta_destino)
    open(marca, 'w').close()


def git_lfs_disponivel() -> bool:
    if not shutil.which('git'):
        return False
    try:
        return subprocess.run(['git', 'lfs', 'version'], capture_output=True).returncode == 0
    except OSError:
        return False


def ponteiros_lfs(pasta: str) -> int:
    """Arquivos que ficaram como ponteiro LFS (texto de ~130 bytes) em vez do conteudo."""
    n = 0
    for raiz, dirs, arquivos in os.walk(pasta):
        dirs[:] = [d for d in dirs if d != '.git']
        for a in arquivos:
            p = os.path.join(raiz, a)
            if os.path.getsize(p) < 200:
                with open(p, 'rb') as f:
                    n += f.read(24) == b'version https://git-lfs.'
    return n


def clonar_hf(repo: str, destino: str, rotulo: str, tentativas: int = 3):
    """Clone raso numa pasta temporaria; so substitui o destino quando esta completo."""
    temp = destino + '.clonando'
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    for tentativa in range(1, tentativas + 1):
        apagar(temp)
        t0 = time.time()
        r = subprocess.run(['git', 'clone', '--depth', '1', '--quiet',
                            f'https://huggingface.co/datasets/{repo}', temp],
                           env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if r.returncode == 0:
            faltando = ponteiros_lfs(temp)
            if faltando:
                subprocess.run(['git', 'lfs', 'pull'], cwd=temp, env=env, capture_output=True)
                faltando = ponteiros_lfs(temp)
            if not faltando:
                break
            log(rotulo, f"{faltando} arquivos LFS nao vieram; tentativa {tentativa}/{tentativas}")
        else:
            log(rotulo, f"git clone falhou: {r.stderr.strip()[-300:]}; tentativa {tentativa}/{tentativas}")
        time.sleep(60)
    else:
        apagar(temp)
        raise RuntimeError(f"git clone de {repo} falhou")
    apagar(os.path.join(temp, '.git'))  # o .git duplica o tamanho das imagens
    apagar(destino)  # sobra de um download anterior incompleto
    os.replace(temp, destino)
    log(rotulo, f"clone concluido em {(time.time() - t0) / 60:.1f} min")


def baixar_roboflow(base: str, fonte: dict, rotulo: str):
    """Exportacao de uma versao de dataset do Roboflow Universe. Precisa de ROBOFLOW_API_KEY no ambiente."""
    ws, projeto, versao = fonte['workspace'], fonte['projeto'], fonte['versao']
    formato = fonte.get('exportacao', fonte['formato'])
    nome = f"{projeto}-v{versao}-{formato}.zip"
    arquivo = os.path.join(comum.pastas(base)['raw'], fonte['id'], nome)
    if not os.path.exists(arquivo):
        chave = os.environ.get('ROBOFLOW_API_KEY', '').strip()
        if not chave:
            raise RuntimeError('defina ROBOFLOW_API_KEY (chave gratuita em roboflow.com > Settings > API Keys)')
        url = f"https://api.roboflow.com/{ws}/{projeto}/{versao}/{formato}"
        for _ in range(60):  # a exportacao e gerada sob demanda
            try:
                r = requests.get(url, params={'api_key': chave}, timeout=60)
            except requests.RequestException as ex:  # a mensagem traria a URL com a chave
                raise RuntimeError(f"falha ao pedir a exportacao de {ws}/{projeto}: {type(ex).__name__}") from None
            if not r.ok:
                raise RuntimeError(f"Roboflow respondeu HTTP {r.status_code} para {ws}/{projeto}/{versao}")
            link = (r.json().get('export') or {}).get('link')
            if link:
                break
            time.sleep(5)
        else:
            raise RuntimeError(f"a exportacao de {ws}/{projeto} nao ficou pronta")
        baixar_url(link, arquivo, rotulo)
    extrair(arquivo, os.path.join(comum.pasta_fonte(base, fonte), os.path.splitext(nome)[0]), rotulo)


def baixar_fonte(base: str, fonte: dict, usar_git: bool):
    rotulo = fonte['id']
    log(rotulo, f"{fonte['nome']} ({fonte['licenca']})")
    if fonte['tipo'] == 'url_zip':
        for url in fonte['arquivos']:
            nome = unquote(url.rsplit('/', 1)[-1])
            arquivo = os.path.join(comum.pastas(base)['raw'], fonte['id'], nome)
            baixar_url(url, arquivo, rotulo)
            pasta = nome[:-len('.tar.gz')] if nome.lower().endswith('.tar.gz') else os.path.splitext(nome)[0]
            extrair(arquivo, os.path.join(comum.pasta_fonte(base, fonte), pasta), rotulo)
    elif fonte['tipo'] == 'roboflow':
        baixar_roboflow(base, fonte, rotulo)
    elif fonte['tipo'] == 'local':
        pasta = comum.pasta_fonte(base, fonte)
        log(rotulo, f"fonte local em {pasta}" + ('' if os.path.isdir(pasta) else ' (pasta ainda nao existe)'))
    elif fonte['tipo'] == 'hf_snapshot':
        destino = comum.pasta_fonte(base, fonte)
        marca = os.path.join(destino, '.baixado_ok')
        if os.path.exists(marca):
            log(rotulo, "ja baixado")
            return
        clonado = False
        if usar_git:
            try:
                clonar_hf(fonte['repo'], destino, rotulo)
                clonado = True
            except (RuntimeError, OSError) as ex:
                log(rotulo, f"{ex}; usando a API do Hugging Face")
        if not clonado:
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id=fonte['repo'], repo_type='dataset', local_dir=destino, max_workers=8)
        open(marca, 'w').close()
    else:
        raise ValueError(f"tipo de fonte desconhecido: {fonte['tipo']}")
    log(rotulo, "pronto")


def main():
    ap = argparse.ArgumentParser(description='Baixa os datasets publicos de EPI')
    ap.add_argument('--base', default=comum.base_padrao())
    ap.add_argument('--fontes', nargs='*', help='ids das fontes (padrao: todas)')
    ap.add_argument('--sem-nao-comercial', action='store_true', help='ignora fontes CC BY-NC (ex.: SH17)')
    ap.add_argument('--estrito', action='store_true', help='falha se qualquer fonte falhar')
    ap.add_argument('--paralelo', type=int, default=0, help='fontes ao mesmo tempo (padrao: todas)')
    ap.add_argument('--sem-git', action='store_true', help='usa a API do HF mesmo com git instalado')
    args = ap.parse_args()

    fontes = selecionar_fontes(args.fontes, args.sem_nao_comercial)
    usar_git = not args.sem_git and git_lfs_disponivel()
    print('Fontes do Hugging Face via ' + ('git clone + LFS' if usar_git else 'API do HF (git/git-lfs ausente)'),
          flush=True)
    falhas = []
    with ThreadPoolExecutor(max_workers=args.paralelo or len(fontes)) as pool:
        futuros = {pool.submit(baixar_fonte, args.base, fonte, usar_git): fonte for fonte in fontes}
        for futuro in as_completed(futuros):
            fonte = futuros[futuro]
            try:
                futuro.result()
            except Exception as ex:
                log(fonte['id'], f"ERRO: {ex}")
                falhas.append(fonte['id'])
    if falhas:
        print('AVISO: fontes com falha (o dataset sera montado sem elas): ' + ', '.join(falhas))
        if args.estrito or len(falhas) == len(fontes):
            sys.exit(1)


if __name__ == '__main__':
    main()
