"""
Cliente minimo pro Google Drive API, usando uma conta de servico (sem
usuario logado) -- adequado pra automacao sem ninguem logado, rodando
dentro do GitHub Actions.

Credenciais NUNCA ficam no codigo. Vem da variavel de ambiente
GOOGLE_SERVICE_ACCOUNT_JSON (o conteudo inteiro do JSON da chave, nao o
caminho de um arquivo), preenchida pelo GitHub Actions a partir do
"Secret" do repositorio.
"""
from __future__ import annotations

import io
import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


class DriveError(RuntimeError):
    pass


def _credenciais() -> service_account.Credentials:
    bruto = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not bruto:
        raise DriveError(
            "GOOGLE_SERVICE_ACCOUNT_JSON nao configurado (ver README.md)."
        )
    try:
        info = json.loads(bruto)
    except json.JSONDecodeError as exc:
        raise DriveError(f"GOOGLE_SERVICE_ACCOUNT_JSON invalido: {exc}") from exc
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def servico_drive():
    """Monta o cliente autenticado do Google Drive API (v3)."""
    return build("drive", "v3", credentials=_credenciais(), cache_discovery=False)


def listar_arquivos_pasta(servico, pasta_id: str) -> list[dict]:
    """Lista os arquivos (nao-lixeira) dentro de uma pasta do Google Drive.

    A pasta precisa ter sido compartilhada com o e-mail da conta de
    servico (como Leitor) -- sem isso a API devolve a pasta vazia, nao um
    erro (por design do Google, pra nao revelar se a pasta existe)."""
    arquivos: list[dict] = []
    pagina_token = None
    query = f"'{pasta_id}' in parents and trashed = false"
    while True:
        resposta = (
            servico.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, modifiedTime, md5Checksum)",
                pageToken=pagina_token,
            )
            .execute()
        )
        arquivos.extend(resposta.get("files", []))
        pagina_token = resposta.get("nextPageToken")
        if not pagina_token:
            break
    return arquivos


def baixar_arquivo(servico, arquivo_id: str) -> bytes:
    """Baixa o conteudo bruto (bytes) de um arquivo pelo ID."""
    requisicao = servico.files().get_media(fileId=arquivo_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, requisicao)
    concluido = False
    while not concluido:
        _, concluido = downloader.next_chunk()
    return buffer.getvalue()
