# /// script
# requires-python = ">=3.10"
# dependencies = ["boto3>=1.34"]
# ///
"""Sobe o Argos EPI Servidor (saida do empacotamento) para a pasta argosepi-discovery do Cloudflare R2.

Uso (no PowerShell):
    uv run publicar_r2.py [--saida D:\\argos-build\\saida] [--site ..\\..\\frontend]

As credenciais vem de variaveis de ambiente ou sao perguntadas na hora (a chave secreta sem aparecer na tela).
Nada e gravado em arquivo: crie um token de API do R2 com permissao "Object Read & Write" so para o bucket.
    R2_ACCOUNT_ID         id da conta Cloudflare (aparece no painel do R2)
    R2_ACCESS_KEY_ID      do token de API do R2
    R2_SECRET_ACCESS_KEY  do token de API do R2
    R2_BUCKET             nome do bucket ligado ao endereco publico pub-....r2.dev
    R2_PREFIXO            pasta dentro do bucket (padrao: argosepi-discovery/)

Ordem: primeiro os arquivos grandes (o que ja esta igual la e pulado), por ultimo o latest.json.
Com --site, copia o latest.json para frontend/downloads.json (o site le dali os tamanhos e a versao).
"""
import argparse
import getpass
import hashlib
import json
import os
import shutil
import sys

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError

TIPOS = {'.exe': 'application/vnd.microsoft.portable-executable', '.zip': 'application/zip',
         '.gz': 'application/gzip', '.json': 'application/json'}


def valor(nome, segredo=False, padrao=''):
    v = os.environ.get(nome, '').strip()
    if v:
        return v
    texto = f'{nome}' + (f' [{padrao}]' if padrao else '') + ': '
    v = (getpass.getpass(texto) if segredo else input(texto)).strip()
    return v or padrao


def sha256(caminho):
    h = hashlib.sha256()
    with open(caminho, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--saida', default=r'D:\argos-build\saida')
    ap.add_argument('--site', default='', help='pasta frontend: grava downloads.json depois de subir')
    ap.add_argument('--so-listar', action='store_true', help='mostra o que subiria, sem enviar')
    ap.add_argument('--limpar', action='store_true',
                    help='depois de subir, apaga da pasta do R2 os pacotes de versoes antigas (o que o latest.json nao usa mais)')
    a = ap.parse_args()

    manifesto = os.path.join(a.saida, 'latest.json')
    if not os.path.exists(manifesto):
        sys.exit(f'Nao achei {manifesto}. Rode o empacotar_windows.ps1 antes.')
    with open(manifesto, encoding='utf-8') as f:
        m = json.load(f)

    # o que o latest.json aponta (e as copias do instalador por placa, que o site baixa)
    w = m['windows']
    arquivos = [w['setup']['url'], w['base']['url']]
    if (w.get('midia') or {}).get('url'):       # video direto e voz dos avisos
        arquivos.append(w['midia']['url'])
    arquivos += [p['url'] for p in (w.get('motor') or {}).values()]
    arquivos += [p['url'] for p in (w.get('nucleo') or {}).values()]
    arquivos += [p['url'] for p in (w.get('programa2') or {}).values()]
    arquivos += [p['url'] for p in (m.get('linux') or {}).values() if isinstance(p, dict) and p.get('url')]
    for so in (w, m.get('linux') or {}):      # pacotes opcionais (TensorRT)
        for extra in ((so.get('extras') or {}) if isinstance(so, dict) else {}).values():
            arquivos += [p['url'] for p in extra.values() if isinstance(p, dict) and p.get('url')]
    arquivos += [f'ArgosEPI-Servidor-Setup-{n}.exe' for n in ('nvidia', 'amd-intel', 'cpu')]
    faltando = [r for r in arquivos if not os.path.exists(os.path.join(a.saida, r))]
    if faltando:
        sys.exit('Faltam arquivos na saida: ' + ', '.join(faltando))
    total = sum(os.path.getsize(os.path.join(a.saida, r)) for r in arquivos)
    print(f'Versao {m["versao"]}: {len(arquivos)} arquivos, {total / 1073741824:.1f} GB')
    if a.so_listar:
        for r in arquivos:
            print(f'  {r}  ({os.path.getsize(os.path.join(a.saida, r)) / 1048576:.0f} MB)')
        return

    conta = valor('R2_ACCOUNT_ID')
    chave = valor('R2_ACCESS_KEY_ID')
    segredo = valor('R2_SECRET_ACCESS_KEY', segredo=True)
    bucket = valor('R2_BUCKET')
    prefixo = valor('R2_PREFIXO', padrao='argosepi-discovery/').strip('/') + '/'
    s3 = boto3.client('s3', endpoint_url=f'https://{conta}.r2.cloudflarestorage.com',
                      aws_access_key_id=chave, aws_secret_access_key=segredo, region_name='auto',
                      config=Config(retries={'max_attempts': 8, 'mode': 'adaptive'}))
    cfg = TransferConfig(multipart_threshold=64 << 20, multipart_chunksize=64 << 20, max_concurrency=6)

    def subir(rel, cache='public, max-age=31536000, immutable'):
        local = os.path.join(a.saida, rel)
        chave_obj = prefixo + rel.replace('\\', '/')
        tam = os.path.getsize(local)
        soma = sha256(local)
        try:
            h = s3.head_object(Bucket=bucket, Key=chave_obj)
            if h['ContentLength'] == tam and h.get('Metadata', {}).get('sha256') == soma:
                print(f'  igual, pulando: {rel}')
                return
        except ClientError:
            pass
        feito = [0]

        def progresso(n):
            feito[0] += n
            print(f'\r  {rel}: {feito[0] * 100 // max(tam, 1)}%', end='', flush=True)
        s3.upload_file(local, bucket, chave_obj, Config=cfg, Callback=progresso, ExtraArgs={
            'ContentType': TIPOS.get(os.path.splitext(rel)[1].lower(), 'application/octet-stream'),
            'CacheControl': cache, 'Metadata': {'sha256': soma}})
        print(f'\r  enviado: {rel} ({tam / 1048576:.0f} MB)')

    # os nomes fixos (instaladores) mudam a cada versao: sem cache longo
    for rel in arquivos:
        fixo = rel.startswith('ArgosEPI-Servidor-Setup')
        subir(rel, cache='public, max-age=300' if fixo else 'public, max-age=31536000, immutable')
    subir('latest.json', cache='no-cache')
    print(f'Publicado em {prefixo} (bucket {bucket}).')

    # pacotes de versoes antigas que ficaram na pasta (so dentro do prefixo do Argos)
    atuais = {prefixo + r.replace('\\', '/') for r in arquivos} | {prefixo + 'latest.json'}
    sobras = []
    for pagina in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefixo):
        sobras += [(o['Key'], o['Size']) for o in pagina.get('Contents', []) if o['Key'] not in atuais]
    if sobras:
        print(f'Sobras de versoes antigas: {len(sobras)} arquivos, {sum(t for _, t in sobras) / 1073741824:.1f} GB')
        for chave_obj, tam in sobras:
            print(f'  {chave_obj}  ({tam / 1048576:.0f} MB)')
        if a.limpar:
            for i in range(0, len(sobras), 500):
                s3.delete_objects(Bucket=bucket, Delete={'Objects': [{'Key': k} for k, _ in sobras[i:i + 500]]})
            print('  apagadas.')
        else:
            print('  (rode de novo com --limpar para apagar)')

    if a.site:
        destino = os.path.join(a.site, 'downloads.json')
        shutil.copyfile(manifesto, destino)
        print(f'Site: {destino} atualizado (falta o git push do argosepi-frontend).')


if __name__ == '__main__':
    main()
