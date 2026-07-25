"""
Parser para relatorios de largura fixa do PathFind ("Cargas Otimizadas").

O arquivo nao tem separador de campo confiavel: o espacamento entre colunas
varia linha a linha. Por isso o parsing e feito com regex ancoradas em
padroes estaveis (marcador "O<data><pedido>", par de coordenadas seguido de
horario "HH:MM", bloco de 3 horarios colados + distancia) em vez de recortes
de posicao fixa de coluna. Isso deve funcionar para qualquer arquivo exportado
nesse mesmo formato, nao so para o exemplo usado durante o desenvolvimento.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Entrega:
    rota: str
    veiculo: str
    motorista_nome: str
    codigo_cliente: str
    cliente: str
    pedido_num: str
    peso_kg: float
    valor: float
    latitude: float
    longitude: float
    hora_chegada: str
    hora_duracao_parada: str
    hora_entrega_original: str
    endereco: str
    sequencia_original: int | None = None  # posicao da parada no plano original do PathFind
    linha_origem: int = field(repr=False, default=0)


# --- Anchors -----------------------------------------------------------

_ROTA_RE = re.compile(r"^\s*(RT\d+)")

# Marcador de pedido: "O2026-07-20114" -> data + numero do pedido colado.
_PEDIDO_RE = re.compile(r"O(\d{4}-\d{2}-\d{2})(\d+)")

# Codigo (8 digitos) e nome do cliente, antes do marcador "O<data>". A busca
# deve comecar depois do bloco de datas duplicadas, senao o regex de 8
# digitos pode casar por acidente com o final da 2a data colado a um codigo
# seguinte (ex: "...07-22" + "000559" = 8 digitos que nao sao o codigo do
# cliente).
_CLIENTE_RE = re.compile(r"(\d{8})\s+(.+?)\s{2,}O\d{4}-\d{2}-\d{2}\d+")

# Lat/lon seguidos do primeiro horario HH:MM (par de decimais negativos).
# O horario fica em lookahead (nao consome) para que o bloco de 3 horarios
# colados logo em seguida possa ser capturado por _TEMPOS_RE a partir daqui.
_LATLON_RE = re.compile(r"(-\d+\.\d+)\s+(-\d+\.\d+)(?=\s+\d{2}:\d{2})")

# Tres horarios HH:MM colados + distancia decimal (chegada, parada, entrega).
_TEMPOS_RE = re.compile(
    r"(\d{2}:\d{2})(\d{2}:\d{2})(\d{2}:\d{2})(\d+\.\d+)"
)

# Nome do motorista: texto (pode ser vazio, quando a rota nao tem motorista
# definido) apos o par numero-inteiro + numero-decimal que segue o bloco de
# horarios/distancia. O limite entre o nome e o endereco e um espacamento de
# preenchimento de coluna (10+ espacos); um nome de verdade nunca tem uma
# sequencia tao longa de espacos internos, entao o limiar de 10 evita
# confundir esse preenchimento com o espaco simples entre palavras do nome.
_MOTORISTA_NOME_RE = re.compile(
    r"\s*\d+\s+[\d.]+(.*?)\s{10,}(.*)$", re.DOTALL
)

# Linha de item de pedido: codigo de 6 digitos + 5 numeros decimais.
_ITEM_RE = re.compile(
    r"\d{6}\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+"
)

_NUM_RE = re.compile(r"-?\d+\.\d+|-?\d+")


def _extrair_peso_valor(trecho: str) -> tuple[float, float]:
    """Extrai valor e peso a partir do trecho entre o marcador de pedido e o lat/lon.

    A ordem observada no arquivo e sempre, terminando junto do lat/lon:
    ... motorista_cod  valor  <constante 0.0000>  peso  volume  lat  lon
    Por isso contamos a partir do FIM (tokens[-1] = volume, tokens[-2] = peso,
    tokens[-4] = valor), o que continua funcionando mesmo quando o codigo do
    motorista vem colado/zerado (linhas sem motorista definido).
    """
    tokens = _NUM_RE.findall(trecho)
    if len(tokens) < 4:
        return 0.0, 0.0
    valor = float(tokens[-4])
    peso = float(tokens[-2])
    return valor, peso


def _extrair_endereco(trecho: str) -> str:
    """Formata o bloco de endereco cru (rua/bairro/cidade/UF concatenados).

    O formato de origem nao usa nenhum separador entre rua, bairro, cidade e
    UF, e o bairro costuma aparecer duplicado (uma vez colado ao numero da
    rua, outra vez como campo isolado antes do CEP). Aqui aplicamos uma
    heuristica best-effort: isolamos o CEP (8 digitos) e a UF (2 letras
    maiusculas no fim do bloco) e devolvemos o restante como logradouro.
    Nao ha garantia de separacao perfeita rua/bairro/cidade porque a fonte
    nao delimita esses campos.
    """
    trecho = trecho.strip()
    m = re.search(r"(?P<antes>.*?)(?P<cep>\d{8})(?P<cidade>[A-ZÀ-Ú ]+?)\s*(?P<uf>[A-Z]{2})\s*$", trecho)
    if not m:
        return re.sub(r"\s{2,}", " - ", trecho)
    antes = re.sub(r"\s{2,}", " - ", m.group("antes").strip())
    cidade = m.group("cidade").strip()
    uf = m.group("uf").strip()
    cep = m.group("cep").strip()
    partes = [p for p in [antes, cidade] if p]
    endereco = " - ".join(partes)
    if uf:
        endereco = f"{endereco}/{uf}"
    if cep:
        endereco = f"{endereco} - CEP {cep}"
    return endereco


def parse_linha(linha: str, numero_linha: int = 0) -> Entrega | None:
    """Faz o parsing de uma linha de entrega. Retorna None se a linha nao
    tiver o formato esperado (ex: linhas de cabecalho/rodape do relatorio)."""

    rota_m = _ROTA_RE.match(linha)
    if not rota_m:
        return None
    rota = rota_m.group(1)

    pedido_m = _PEDIDO_RE.search(linha)
    latlon_m = _LATLON_RE.search(linha)
    if not pedido_m or not latlon_m:
        return None

    pedido_num = pedido_m.group(2)
    latitude = float(latlon_m.group(1))
    longitude = float(latlon_m.group(2))

    # veiculo: token com formato de placa (3 letras + hifen + 4 alfanumericos,
    # ex: TVA-5A26) colado a sequencia da parada no plano original do
    # PathFind, no formato "1<N>" (um digito fixo + o numero da parada, ex:
    # "115" = parada 15, "1"+"1" = parada 1). Confirmado batendo com o numero
    # de paradas de varias rotas reais (sempre forma um intervalo 1..N sem
    # buracos ou repeticao).
    #
    # Ancorado no FORMATO DA PLACA (nao mais em "primeiro token antes do
    # bloco de datas"): o espacamento entre esse token e as datas que vem
    # depois varia (arquivo de largura fixa, o preenchimento de espacos
    # muda conforme o tamanho da sequencia colada), e algumas rotas (as de
    # dia fixo/recorrente) trazem uma marca de dia da semana + codigo de
    # rota (ex: "WED -UL-22") em vez das duas datas coladas de costume,
    # antes da unica data que sobra. Sem esse ancoramento pela placa, o
    # codigo do cliente saia errado (mesmo valor pra toda a rota), o que
    # fazia a marcacao de entrega de todas as paradas ficar amarrada numa
    # unica chave -- ver historico de bug de status compartilhado entre
    # paradas.
    veic_m = re.search(r"([A-Z]{3}-[A-Z0-9]{4}\d+)\s+.*?\d{4}-\d{2}-\d{2}(?:\d{4}-\d{2}-\d{2})?", linha[rota_m.end():])
    veiculo = veic_m.group(1)[:8] if veic_m else ""
    fim_datas = rota_m.end() + veic_m.end() if veic_m else rota_m.end()

    sequencia_original = None
    if veic_m:
        sufixo = veic_m.group(1)[8:]
        if len(sufixo) >= 2 and sufixo.isdigit():
            sequencia_original = int(sufixo[1:])

    cliente_m = _CLIENTE_RE.search(linha, pos=fim_datas)
    codigo_cliente = cliente_m.group(1).strip() if cliente_m else ""
    cliente = cliente_m.group(2).strip() if cliente_m else ""

    tempos_m = _TEMPOS_RE.search(linha, pos=latlon_m.end())
    if tempos_m:
        hora_chegada = tempos_m.group(1)
        hora_duracao_parada = tempos_m.group(2)
        hora_entrega_original = tempos_m.group(3)
        fim_tempos = tempos_m.end()
    else:
        hora_chegada = hora_duracao_parada = hora_entrega_original = ""
        fim_tempos = latlon_m.end()

    valor, peso_kg = _extrair_peso_valor(linha[pedido_m.end():latlon_m.start()])

    nome_e_resto = _MOTORISTA_NOME_RE.match(linha, pos=fim_tempos)
    if nome_e_resto:
        motorista_nome = nome_e_resto.group(1).strip()
        resto = nome_e_resto.group(2)
    else:
        motorista_nome = ""
        resto = linha[fim_tempos:]

    primeiro_item = _ITEM_RE.search(resto)
    bloco_endereco = resto[: primeiro_item.start()] if primeiro_item else resto
    endereco = _extrair_endereco(bloco_endereco)

    return Entrega(
        rota=rota,
        veiculo=veiculo,
        motorista_nome=motorista_nome,
        codigo_cliente=codigo_cliente,
        cliente=cliente,
        pedido_num=pedido_num,
        peso_kg=peso_kg,
        valor=valor,
        latitude=latitude,
        longitude=longitude,
        hora_chegada=hora_chegada,
        hora_duracao_parada=hora_duracao_parada,
        hora_entrega_original=hora_entrega_original,
        endereco=endereco,
        sequencia_original=sequencia_original,
        linha_origem=numero_linha,
    )


def parse_conteudo(texto: str) -> dict[str, list[Entrega]]:
    """Mesma coisa que parse_arquivo, mas a partir de um texto ja em
    memoria (nao um caminho de arquivo local) -- usado quando o conteudo
    vem direto de uma API (Google Drive, OneDrive etc.), sem passar por
    disco."""
    rotas: dict[str, list[Entrega]] = {}
    for i, linha in enumerate(texto.splitlines(keepends=True), start=1):
        entrega = parse_linha(linha, numero_linha=i)
        if entrega is None:
            continue
        rotas.setdefault(entrega.rota, []).append(entrega)
    return rotas


def parse_arquivo(caminho: str, encoding: str = "latin-1") -> dict[str, list[Entrega]]:
    """Le o arquivo .txt do PathFind e devolve as entregas agrupadas por rota."""
    with open(caminho, "r", encoding=encoding, errors="replace") as f:
        return parse_conteudo(f.read())
