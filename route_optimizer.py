"""
Otimizacao de sequencia de entrega por proximidade geografica.

AVISO IMPORTANTE: este algoritmo otimiza SOMENTE por distancia (nearest
neighbor sobre a distancia real de estrada, nao linha reta -- ver mais
abaixo), partindo da coordenada fixa de saida do caminhao. Ele NAO considera
janela de horario, prazo de entrega combinado com o cliente, prioridade de
cliente, capacidade do veiculo por trecho, nem transito em tempo real. Se a
operacao precisar respeitar horarios de entrega combinados, essa restricao
precisa ser adicionada depois (ex: nearest neighbor com janela de tempo, ou
um solver de VRPTW). Use o resultado como um ponto de partida geografico,
nao como a rota final se houver compromissos de horario com os clientes.

DISTANCIA REAL DE ESTRADA, NAO LINHA RETA:
As distancias sao calculadas pela malha viaria de verdade usando o OSRM
(Open Source Routing Machine, http://project-osrm.org), atraves do servidor
de demonstracao publico e gratuito (router.project-osrm.org). Isso exige
conexao com a internet. Se o servico estiver fora do ar ou sem resposta, o
sistema cai automaticamente para distancia em linha reta (Haversine) so
para nao travar a geracao da rota -- mas o resultado nesse caso fica menos
preciso, e isso e' registrado no log do watcher.

O servidor publico do OSRM e' um servico de demonstracao, sem garantia de
disponibilidade para uso comercial pesado. Para uso serio e continuo, o
recomendado e' rodar uma instancia propria do OSRM (self-hosted) ou usar um
servico pago de matriz de distancias (Google, Mapbox, HERE).
"""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass

from parsing import Entrega

RAIO_TERRA_KM = 6371.0
OSRM_BASE_URL = "https://router.project-osrm.org"
OSRM_TIMEOUT_SEGUNDOS = 20


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia em linha reta (em km), considerando a curvatura da Terra.
    Usada apenas como reserva (fallback) quando o OSRM nao responde -- nao e'
    a distancia principal do sistema, que e' a de estrada (ver _matriz_osrm)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * RAIO_TERRA_KM * math.asin(math.sqrt(a))


@dataclass
class ParadaOtimizada:
    sequencia: int
    entrega: Entrega
    distancia_desde_anterior_km: float  # da saida (parada 1) ou da parada anterior, ate aqui
    distancia_ate_proxima_km: float | None  # None na ultima parada


@dataclass
class ResultadoRota:
    rota: str
    ordem_otimizada: list[ParadaOtimizada]
    distancia_origem_primeira_km: float  # saida do caminhao ate a 1a parada (ordem otimizada)
    distancia_retorno_km: float  # ultima parada de volta ate o ponto de partida (ordem otimizada)
    distancia_total_original_km: float  # ida (saida->1a...ultima) + volta, na ordem original do plano
    distancia_total_otimizada_km: float  # ida (saida->1a...ultima) + volta, na ordem otimizada
    usou_distancia_real: bool  # False se caiu para linha reta por falha do OSRM
    tracado_ida: list[tuple[float, float]] | None  # geometria real (lat,lon) da saida ate a ultima parada
    tracado_volta: list[tuple[float, float]] | None  # geometria real (lat,lon) da ultima parada de volta a saida

    @property
    def economia_km(self) -> float:
        return self.distancia_total_original_km - self.distancia_total_otimizada_km

    @property
    def economia_percentual(self) -> float:
        if self.distancia_total_original_km == 0:
            return 0.0
        return (self.economia_km / self.distancia_total_original_km) * 100


def _ordem_original(entregas: list[Entrega]) -> list[Entrega]:
    """Ordem real do plano original do PathFind.

    NAO usa a ordem em que as linhas aparecem no arquivo (essa ordem e'
    essencialmente arbitraria pro proposito de rota -- na pratica alterna
    entre bairros/cidades distantes sem nenhum criterio geografico, gerando
    uma distancia "original" absurdamente inflada). O PathFind grava a
    sequencia real de visita colada ao codigo do veiculo (ver parsing.py:
    Entrega.sequencia_original). Aqui so ordenamos por esse numero; entregas
    sem essa informacao (arquivo em formato diferente) vao para o final,
    mantendo a ordem relativa entre si.
    """

    def chave(e: Entrega) -> tuple[int, int]:
        se = e.sequencia_original
        return (0, se) if se is not None else (1, 0)

    return sorted(entregas, key=chave)


def _matriz_haversine(pontos: list[tuple[float, float]]) -> list[list[float]]:
    n = len(pontos)
    matriz = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                matriz[i][j] = haversine_km(*pontos[i], *pontos[j])
    return matriz


def _matriz_osrm(pontos: list[tuple[float, float]]) -> list[list[float]] | None:
    """Pede ao OSRM a matriz de distancia REAL DE ESTRADA (em km) entre todos
    os pontos de uma vez (1 requisicao HTTP por rota, nao uma por par).
    Retorna None se o servico falhar por qualquer motivo -- quem chamar deve
    cair para _matriz_haversine nesse caso."""
    # OSRM espera "longitude,latitude" (invertido em relacao a como guardamos
    # nossas coordenadas).
    coords = ";".join(f"{lon},{lat}" for lat, lon in pontos)
    url = f"{OSRM_BASE_URL}/table/v1/driving/{coords}?annotations=distance"
    try:
        with urllib.request.urlopen(url, timeout=OSRM_TIMEOUT_SEGUNDOS) as resp:
            dados = json.load(resp)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None

    if dados.get("code") != "Ok":
        return None
    distancias_m = dados.get("distances")
    if not distancias_m:
        return None

    n = len(pontos)
    matriz = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            valor = distancias_m[i][j]
            if valor is None:
                # Par sem rota conhecida pelo OSRM (raro): usa linha reta so
                # para esse par especifico, sem descartar o resto da matriz.
                matriz[i][j] = haversine_km(*pontos[i], *pontos[j])
            else:
                matriz[i][j] = valor / 1000.0
    return matriz


def obter_matriz_distancias(pontos: list[tuple[float, float]]) -> tuple[list[list[float]], bool]:
    """Matriz de distancia (em km) entre todos os pontos, por estrada de
    verdade via OSRM. Cai para linha reta (Haversine) se o OSRM falhar.
    Retorna (matriz, usou_distancia_real)."""
    matriz = _matriz_osrm(pontos)
    if matriz is not None:
        return matriz, True
    return _matriz_haversine(pontos), False


def _tracado_osrm(pontos: list[tuple[float, float]]) -> list[tuple[float, float]] | None:
    """Pede ao OSRM o tracado real (seguindo as ruas) passando pelos pontos
    na ordem dada, para desenhar no mapa. Diferente de _matriz_osrm (que so
    da a distancia), aqui pegamos a geometria do caminho de verdade. Retorna
    None se o servico falhar -- quem chamar deve desenhar uma linha reta
    entre os pontos como reserva nesse caso."""
    if len(pontos) < 2:
        return None
    coords = ";".join(f"{lon},{lat}" for lat, lon in pontos)
    url = f"{OSRM_BASE_URL}/route/v1/driving/{coords}?overview=simplified&geometries=geojson"
    try:
        with urllib.request.urlopen(url, timeout=OSRM_TIMEOUT_SEGUNDOS) as resp:
            dados = json.load(resp)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None

    if dados.get("code") != "Ok" or not dados.get("routes"):
        return None
    try:
        coords_geojson = dados["routes"][0]["geometry"]["coordinates"]
    except (KeyError, IndexError, TypeError):
        return None
    return [(lat, lon) for lon, lat in coords_geojson]


def otimizar_rota(
    rota: str,
    entregas: list[Entrega],
    origem_lat: float,
    origem_lon: float,
) -> ResultadoRota:
    """Aplica nearest neighbor (sobre distancia real de estrada, via OSRM) a
    uma rota, partindo da coordenada de saida do caminhao e voltando a ela no
    final, e compara com o plano original (a sequencia de visita que o
    proprio PathFind calculou)."""
    if not entregas:
        return ResultadoRota(rota, [], 0.0, 0.0, 0.0, 0.0, True, None, None)

    # indice 0 = origem; indices 1..N = entregas, na mesma ordem da lista
    # recebida (a otimizacao trabalha com indices para nao perder a
    # correspondencia com a matriz).
    pontos = [(origem_lat, origem_lon)] + [(e.latitude, e.longitude) for e in entregas]
    matriz, usou_distancia_real = obter_matriz_distancias(pontos)

    IDX_ORIGEM = 0

    def dist(i: int, j: int) -> float:
        return matriz[i][j]

    # --- distancia na ordem ORIGINAL (sequencia real do PathFind) ---
    ordem_original = _ordem_original(entregas)
    indice_por_entrega = {id(e): i + 1 for i, e in enumerate(entregas)}
    indices_originais = [IDX_ORIGEM] + [indice_por_entrega[id(e)] for e in ordem_original] + [IDX_ORIGEM]
    distancia_original = sum(
        dist(a, b) for a, b in zip(indices_originais, indices_originais[1:])
    )

    # --- nearest neighbor sobre a matriz real, partindo da origem ---
    restantes = set(range(1, len(entregas) + 1))
    atual = IDX_ORIGEM
    ordem_indices: list[int] = []
    while restantes:
        proximo = min(restantes, key=lambda j: dist(atual, j))
        restantes.remove(proximo)
        ordem_indices.append(proximo)
        atual = proximo

    caminho = [entregas[i - 1] for i in ordem_indices]

    distancia_origem_primeira = dist(IDX_ORIGEM, ordem_indices[0])
    distancia_retorno = dist(ordem_indices[-1], IDX_ORIGEM)

    paradas: list[ParadaOtimizada] = []
    for pos, idx in enumerate(ordem_indices):
        anterior = IDX_ORIGEM if pos == 0 else ordem_indices[pos - 1]
        d_anterior = dist(anterior, idx)
        if pos + 1 < len(ordem_indices):
            d_proxima = dist(idx, ordem_indices[pos + 1])
        else:
            d_proxima = None
        paradas.append(ParadaOtimizada(
            sequencia=pos + 1,
            entrega=caminho[pos],
            distancia_desde_anterior_km=d_anterior,
            distancia_ate_proxima_km=d_proxima,
        ))

    indices_otimizados = [IDX_ORIGEM] + ordem_indices + [IDX_ORIGEM]
    distancia_otimizada = sum(
        dist(a, b) for a, b in zip(indices_otimizados, indices_otimizados[1:])
    )

    # Tracado real pro mapa (nao apenas a distancia): pede ao OSRM o caminho
    # que realmente segue as ruas, separado em ida (saida -> ... -> ultima
    # parada) e volta (ultima parada -> saida), pra manter a mesma distincao
    # visual (linha solida / tracejada) que ja existia com a linha reta.
    pontos_ida = [(origem_lat, origem_lon)] + [(e.latitude, e.longitude) for e in caminho]
    tracado_ida = _tracado_osrm(pontos_ida) if usou_distancia_real else None
    pontos_volta = [(caminho[-1].latitude, caminho[-1].longitude), (origem_lat, origem_lon)]
    tracado_volta = _tracado_osrm(pontos_volta) if usou_distancia_real else None

    return ResultadoRota(
        rota=rota,
        ordem_otimizada=paradas,
        distancia_origem_primeira_km=distancia_origem_primeira,
        distancia_retorno_km=distancia_retorno,
        distancia_total_original_km=distancia_original,
        distancia_total_otimizada_km=distancia_otimizada,
        usou_distancia_real=usou_distancia_real,
        tracado_ida=tracado_ida,
        tracado_volta=tracado_volta,
    )


def otimizar_todas(
    rotas: dict[str, list[Entrega]],
    origem_lat: float,
    origem_lon: float,
) -> dict[str, ResultadoRota]:
    return {
        rota: otimizar_rota(rota, entregas, origem_lat, origem_lon)
        for rota, entregas in rotas.items()
    }
