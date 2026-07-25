"""
Versao "nuvem" do watch_and_publish.py, ligada no Google Drive: em vez de
vigiar uma pasta local sincronizada (que exige o computador ligado),
consulta a API do Google Drive diretamente. Pensado pra rodar dentro do
GitHub Actions, agendado (cron), SEM depender de nenhum computador seu
ligado.

Variaveis de ambiente esperadas (configuradas como Secrets/Variables no
GitHub Actions -- ver README.md):
  GOOGLE_SERVICE_ACCOUNT_JSON -- conteudo do JSON da chave da conta de
      servico (Secret, nunca aparece nos logs).
  GOOGLE_DRIVE_FOLDER_ID       -- ID da pasta do Drive vigiada (Variable).
  ORIGEM_LAT, ORIGEM_LON        -- coordenada de saida do caminhao (Variables).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date, datetime

from google_drive_client import DriveError, baixar_arquivo, listar_arquivos_pasta, servico_drive
from parsing import parse_conteudo
from route_optimizer import otimizar_todas
from site_generator import gerar_site

PADRAO_NOME = re.compile(r"rastro_rotas.*\.txt$", re.IGNORECASE)
_DATA_NO_NOME_RE = re.compile(r"(\d{2})-(\d{2})-(\d{4})")
ARQUIVO_ESTADO = ".drive_state.json"


def log(mensagem: str) -> None:
    print(f"[{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}] {mensagem}", flush=True)


def _data_do_nome(nome_arquivo: str) -> date:
    m = _DATA_NO_NOME_RE.search(nome_arquivo)
    if m:
        dia, mes, ano = (int(x) for x in m.groups())
        try:
            return date(ano, mes, dia)
        except ValueError:
            pass
    return date.today()


def _carregar_estado(caminho: str) -> dict:
    if os.path.isfile(caminho):
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _salvar_estado(caminho: str, estado: dict) -> None:
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


def _publicar_no_git(repo_dir: str, mensagem: str) -> None:
    def rodar(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo_dir, check=True)

    rodar("add", "docs", ARQUIVO_ESTADO)
    resultado = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo_dir)
    if resultado.returncode == 0:
        log("Nada mudou, pulando commit.")
        return
    rodar("commit", "-m", mensagem)
    rodar("push")
    log("Publicado no GitHub Pages.")


def processar_item(item: dict, servico, repo_dir: str, origem_lat: float, origem_lon: float) -> None:
    nome_arquivo = item["name"]
    data_ref = _data_do_nome(nome_arquivo)
    log(f"Processando {nome_arquivo} (data de referencia: {data_ref.strftime('%d/%m/%Y')}) ...")

    conteudo_bytes = baixar_arquivo(servico, item["id"])
    texto = conteudo_bytes.decode("latin-1", errors="replace")

    rotas = parse_conteudo(texto)
    if not rotas:
        log("  Nenhuma entrega reconhecida nesse arquivo, ignorando.")
        return

    total = sum(len(v) for v in rotas.values())
    log(f"  {len(rotas)} rota(s) / {total} entrega(s).")

    resultados = otimizar_todas(rotas, origem_lat, origem_lon)
    sem_distancia_real = [r for r in resultados.values() if not r.usou_distancia_real]
    if sem_distancia_real:
        log(f"  AVISO: {len(sem_distancia_real)} rota(s) caiu(ram) para linha reta (OSRM indisponivel).")

    docs_dir = os.path.join(repo_dir, "docs")
    arquivos_gerados = gerar_site(resultados, docs_dir, origem_lat, origem_lon, data_referencia=data_ref)
    if "index.html" not in arquivos_gerados:
        log(f"  Data {data_ref.strftime('%d/%m/%Y')} e' mais antiga que a mais recente ja processada; "
            f"so' o historico foi atualizado.")

    _publicar_no_git(repo_dir, f"Atualiza rotas de {data_ref.strftime('%d/%m/%Y')} a partir de {nome_arquivo}")


def main() -> None:
    repo_dir = os.environ.get("REPO_DIR", os.path.dirname(os.path.abspath(__file__)))
    pasta_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")
    origem_lat = float(os.environ.get("ORIGEM_LAT", "0"))
    origem_lon = float(os.environ.get("ORIGEM_LON", "0"))

    if not pasta_id:
        sys.exit("GOOGLE_DRIVE_FOLDER_ID precisa estar configurado (ver README.md).")

    try:
        servico = servico_drive()
    except DriveError as exc:
        sys.exit(f"Erro de autenticacao: {exc}")

    log(f"Consultando pasta '{pasta_id}' no Google Drive ...")
    try:
        itens = listar_arquivos_pasta(servico, pasta_id)
    except Exception as exc:  # erro de rede/permissao da API
        sys.exit(f"Erro ao consultar o Google Drive: {exc}")

    candidatos = [i for i in itens if PADRAO_NOME.search(i.get("name", ""))]
    candidatos.sort(key=lambda i: _data_do_nome(i["name"]))
    log(f"{len(candidatos)} arquivo(s) candidato(s) encontrados na pasta.")

    caminho_estado = os.path.join(repo_dir, ARQUIVO_ESTADO)
    estado = _carregar_estado(caminho_estado)

    algo_processado = False
    for item in candidatos:
        chave = item["id"]
        # md5Checksum muda toda vez que o conteudo do arquivo e' alterado --
        # usamos isso (em vez de data de modificacao) pra saber se ja
        # processamos essa versao exata do arquivo.
        marca_atual = item.get("md5Checksum") or item.get("modifiedTime", "")
        if estado.get(chave) == marca_atual:
            continue
        try:
            processar_item(item, servico, repo_dir, origem_lat, origem_lon)
        except Exception as exc:  # nao derruba o job inteiro por um arquivo ruim
            log(f"  Erro ao processar {item.get('name')}: {exc}")
            continue
        estado[chave] = marca_atual
        _salvar_estado(caminho_estado, estado)
        algo_processado = True

    if not algo_processado:
        log("Nenhum arquivo novo pra processar.")


if __name__ == "__main__":
    main()
