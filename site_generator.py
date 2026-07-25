"""
Gera o site estatico publicado no GitHub Pages: uma pagina HTML por rota
(link individual para cada motorista, com mapa Leaflet + lista de paradas)
e uma pagina inicial (docs/index.html) listando as rotas do dia.

Alem da pagina "de hoje" (docs/index.html e docs/rotas/*.html, que e o que
os motoristas devem usar no link fixo do dia a dia), cada execucao tambem
grava uma copia arquivada em docs/historico/<AAAA-MM-DD>/, para permitir o
filtro por data na pagina inicial. O historico nunca é apagado
automaticamente; ele so cresce um dia por vez.

Nao existe backend nem senha: qualquer pessoa com o link de uma rota
consegue abri-la (decisao do usuario). Por isso os arquivos NAO devem conter
nada alem do que e necessario para a entrega (sem dados financeiros da
empresa, por exemplo, alem do que ja constava no relatorio original).
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime

from route_optimizer import ResultadoRota

# Nota interna (nao exibida na pagina): o algoritmo de otimizacao usa apenas
# distancia geografica (Haversine + vizinho mais proximo) e NAO considera
# janela de horario nem prazo de entrega combinado com o cliente. Ver
# route_optimizer.py para detalhes -- essa limitacao continua valendo mesmo
# sem o aviso aparecer para o motorista.

MARCA = "Distribuidora OG de Bebidas &middot; Revendedor Autorizado Heineken"

# Config publica do Firebase (projeto "rotacertaog") -- NAO e' segredo, e'
# seguro embutir no HTML/JS publico. A seguranca de verdade vem das regras
# do Firestore (configuradas direto no console do Firebase), nao desses
# valores. Usado pra sincronizar o status de entrega (Entregue/Devolucao/
# Fechado) entre o celular do motorista e o painel do supervisor.
FIREBASE_CONFIG = {
    "apiKey": "AIzaSyDZpcc1GgdbknHdkuI4dOx1myDR6PsLPic",
    "authDomain": "rotacertaog.firebaseapp.com",
    "projectId": "rotacertaog",
    "storageBucket": "rotacertaog.firebasestorage.app",
    "messagingSenderId": "1048075742585",
    "appId": "1:1048075742585:web:424f99f9a29fa0f23593a8",
}
FIRESTORE_COLECAO_STATUS = "status_entregas"
FIRESTORE_COLECAO_MOTORISTAS = "motoristas"
FIRESTORE_COLECAO_AJUSTES = "ajustes_rotas"

# Faixa de numeros de motorista da empresa (720 a 731). Usado pra pre-gerar
# senhas padrao na pagina de administracao. NAO e' derivado dos dados do
# dia -- e' o quadro fixo de motoristas, independente de quem estiver
# escalado numa rota especifica hoje.
NUMERO_MOTORISTA_MIN = 720
NUMERO_MOTORISTA_MAX = 731


def senha_padrao(numero: int) -> str:
    """Gera a senha padrao de um motorista no formato pedido (ex: 720 ->
    '720OG0', 731 -> '731OG1'): o numero do motorista + 'OG' + o ultimo
    digito do numero."""
    return f"{numero}OG{numero % 10}"

_ESTILO = """
:root { --azul: #1f4e78; --azul-claro: #eaf1f8; --texto: #1a1a1a; --borda: #d9dfe6; }
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; font-family: Arial, Helvetica, sans-serif; color: var(--texto); background: #f4f6f8; -webkit-tap-highlight-color: transparent; animation: entrada 0.15s ease-out; }
@keyframes entrada { from { opacity: 0; } to { opacity: 1; } }
header { background: var(--azul); color: #fff; padding: 12px 16px; }
header h1 { margin: 0; font-size: 1.1rem; }
header .marca { font-size: 0.75rem; opacity: 0.9; margin-top: 3px; }
header .sub { font-size: 0.72rem; opacity: 0.75; margin-top: 2px; }
.filtro-data { display: flex; gap: 8px; align-items: center; padding: 10px 16px; background: #fff; border-bottom: 1px solid var(--borda); flex-wrap: wrap; }
.filtro-data label { font-size: 0.85rem; }
.filtro-data select { font-size: 1rem; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--borda); flex: 1; min-width: 160px; }
.filtro-data select.compacta { flex: 0 1 100px; min-width: 90px; }
.filtro-data a { font-size: 0.8rem; color: var(--azul); text-decoration: none; white-space: nowrap; }
.filtro-motorista { display: flex; gap: 8px; align-items: center; padding: 10px 16px; background: #fff; border-bottom: 1px solid var(--borda); flex-wrap: wrap; }
.filtro-motorista label { font-size: 0.85rem; }
.filtro-motorista select { font-size: 1rem; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--borda); flex: 1; min-width: 200px; }
.mapa-geral-details { background: #fff; border-bottom: 1px solid var(--borda); }
.mapa-geral-details summary { padding: 12px 16px; font-size: 0.85rem; font-weight: bold; color: var(--azul); cursor: pointer; }
.mapa-geral { width: 100%; height: 60vh; min-height: 400px; }
.legenda-motoristas { display: flex; flex-wrap: wrap; gap: 8px 14px; padding: 10px 16px; font-size: 0.72rem; }
.legenda-item { display: flex; align-items: center; gap: 5px; }
.legenda-cor { display: inline-block; width: 11px; height: 11px; border-radius: 50%; border: 1px solid rgba(0,0,0,.2); }
.aviso-linha-reta { background: #fff3cd; color: #664d03; font-size: 0.75rem; padding: 6px 16px; border-bottom: 1px solid #ffe69c; }
.aviso-ajuste { background: var(--azul-claro); color: var(--azul); font-size: 0.75rem; padding: 6px 16px; border-bottom: 1px solid var(--borda); }
.resumo { font-size: 0.72rem; background: var(--azul-claro); display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; padding: 10px 12px; border-radius: 8px; margin-top: 4px; }
.resumo > div { display: flex; flex-direction: column; }
.resumo span { color: #555; }
.resumo b { color: var(--azul); font-size: 0.8rem; }
#mapa { width: 100%; height: 68vh; min-height: 460px; }
main { max-width: 720px; margin: 0 auto; padding: 0 4px; }
ol.paradas { list-style: none; margin: 0; padding: 8px; }
.parada { display: flex; gap: 10px; background: #fff; border: 1px solid var(--borda); border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; }
.parada .num { background: var(--azul); color: #fff; border-radius: 50%; width: 26px; height: 26px; min-width: 26px; display: flex; align-items: center; justify-content: center; font-size: 0.85rem; font-weight: bold; align-self: flex-start; }
.parada .info { flex: 1; }
.parada .codigo { font-size: 0.68rem; color: #888; font-weight: bold; }
.parada .cliente { font-weight: bold; font-size: 0.85rem; }
.parada .endereco { font-size: 0.72rem; color: #444; margin-top: 2px; }
.parada .meta { font-size: 0.68rem; color: var(--azul); margin-top: 4px; }
.parada .btn-maps { display: inline-block; margin-top: 8px; padding: 6px 12px; background: var(--azul); color: #fff; border-radius: 6px; text-decoration: none; font-size: 0.78rem; font-weight: bold; }
.parada .btn-maps:visited { color: #fff; }
.parada.status-entregue { border-left: 4px solid #1e7e34; }
.parada.status-devolucao { border-left: 4px solid #c00000; }
.parada.status-fechado { border-left: 4px solid #b8860b; }
.parada.tem-mensagem { border: 2px solid #ff6d00; background: #fff8f0; }
.mensagem-admin { background: #ff6d00; color: #fff; border-radius: 6px; padding: 8px 10px; font-size: 0.78rem; font-weight: bold; margin-top: 6px; }
.status-botoes { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
.status-btn { flex: 1; min-width: 84px; padding: 6px 8px; border-radius: 6px; font-size: 0.7rem; font-weight: bold; background: #fff; cursor: pointer; text-align: center; }
.status-btn.entregue { border: 1.5px solid #1e7e34; color: #1e7e34; }
.status-btn.devolucao { border: 1.5px solid #c00000; color: #c00000; }
.status-btn.fechado { border: 1.5px solid #b8860b; color: #b8860b; }
.status-btn.entregue.ativo { background: #1e7e34; color: #fff; }
.status-btn.devolucao.ativo { background: #c00000; color: #fff; }
.status-btn.fechado.ativo { background: #b8860b; color: #fff; }
footer { text-align: center; font-size: 0.72rem; color: #888; padding: 16px; }
footer a { color: var(--azul); }
ul.lista-rotas { list-style: none; margin: 0; padding: 12px; max-width: 480px; margin: 0 auto; }
ul.lista-rotas li { margin-bottom: 10px; }
ul.lista-rotas a { display: block; background: #fff; border: 1px solid var(--borda); border-radius: 8px; padding: 14px 16px; text-decoration: none; color: var(--texto); font-weight: bold; }
ul.lista-rotas a small { display: block; font-weight: normal; color: #666; margin-top: 4px; }
.link-painel { display: block; text-align: center; padding: 10px 16px; background: #fff; border-bottom: 1px solid var(--borda); font-size: 0.85rem; }
.painel-rota { background: #fff; border: 1px solid var(--borda); border-radius: 8px; margin: 10px; padding: 12px; }
.painel-rota h2 { margin: 0 0 8px 0; font-size: 0.95rem; }
.painel-contagem { display: flex; gap: 10px; flex-wrap: wrap; font-size: 0.72rem; font-weight: bold; margin-bottom: 8px; }
.painel-contagem .c-entregue { color: #1e7e34; }
.painel-contagem .c-devolucao { color: #c00000; }
.painel-contagem .c-fechado { color: #b8860b; }
.painel-contagem .c-pendente { color: #888; }
.painel-linha { display: flex; justify-content: space-between; gap: 8px; font-size: 0.75rem; padding: 5px 0; border-top: 1px solid #f0f0f0; }
.painel-linha .p-status { font-weight: bold; white-space: nowrap; }
.painel-linha.status-entregue .p-status { color: #1e7e34; }
.painel-linha.status-devolucao .p-status { color: #c00000; }
.painel-linha.status-fechado .p-status { color: #b8860b; }
.painel-linha.status-pendente .p-status { color: #bbb; }
.btn-aviso { padding: 3px 8px; border-radius: 5px; border: 1.5px solid #ff6d00; color: #ff6d00; background: #fff; font-size: 0.66rem; font-weight: bold; cursor: pointer; white-space: nowrap; }
.painel-atualizado { text-align: center; font-size: 0.7rem; color: #888; padding: 8px; }
.bloqueio { display: none; position: fixed; inset: 0; background: var(--azul); color: #fff; align-items: center; justify-content: center; z-index: 9999; padding: 20px; }
body.bloqueado .bloqueio { display: flex; }
body.bloqueado > *:not(.bloqueio) { display: none !important; }
.bloqueio-caixa { max-width: 320px; width: 100%; text-align: center; }
.bloqueio-caixa h2 { margin: 0 0 4px 0; font-size: 1.1rem; }
.bloqueio-caixa p { font-size: 0.8rem; opacity: 0.85; margin: 0 0 16px 0; }
.bloqueio-caixa input { width: 100%; padding: 12px; border-radius: 8px; border: none; font-size: 1rem; text-align: center; margin-bottom: 10px; box-sizing: border-box; }
.bloqueio-caixa button { width: 100%; padding: 12px; border-radius: 8px; border: none; background: #fff; color: var(--azul); font-weight: bold; font-size: 1rem; cursor: pointer; }
.bloqueio-erro { color: #ffd6d6; font-size: 0.78rem; margin-top: 10px; min-height: 1em; }
.admin-linha { display: flex; align-items: center; gap: 8px; padding: 8px 12px; background: #fff; border: 1px solid var(--borda); border-radius: 8px; margin: 0 10px 8px 10px; }
.admin-linha span { min-width: 40px; font-weight: bold; font-size: 0.85rem; }
.admin-linha input { flex: 1; padding: 8px; border-radius: 6px; border: 1px solid var(--borda); font-size: 0.9rem; }
.admin-linha button { padding: 8px 12px; border-radius: 6px; border: none; background: var(--azul); color: #fff; font-size: 0.78rem; font-weight: bold; cursor: pointer; }
.admin-topo { display: flex; gap: 8px; padding: 10px; flex-wrap: wrap; }
.admin-topo button { padding: 10px 14px; border-radius: 6px; border: none; background: var(--azul); color: #fff; font-size: 0.8rem; font-weight: bold; cursor: pointer; }
.admin-msg { text-align: center; font-size: 0.8rem; padding: 8px; min-height: 1.2em; }
.secao-titulo { font-size: 1rem; margin: 18px 12px 6px 12px; color: var(--azul); }
.ajuste-rota { background: #fff; border: 1px solid var(--borda); border-radius: 8px; margin: 0 10px 10px 10px; padding: 10px 12px; }
.ajuste-rota summary { font-weight: bold; cursor: pointer; }
.ajuste-rota summary small { font-weight: normal; color: #666; }
.ajuste-campos { display: flex; gap: 10px; flex-wrap: wrap; margin: 12px 0; }
.ajuste-campo { display: flex; flex-direction: column; gap: 3px; font-size: 0.78rem; flex: 1; min-width: 160px; }
.ajuste-campo select, .ajuste-campo input[type=text] { padding: 8px; border-radius: 6px; border: 1px solid var(--borda); font-size: 0.9rem; }
.ajuste-paradas { max-height: 320px; overflow-y: auto; border: 1px solid var(--borda); border-radius: 6px; margin-bottom: 10px; }
.ajuste-parada { display: flex; align-items: center; gap: 8px; padding: 6px 8px; font-size: 0.78rem; border-bottom: 1px solid #f0f0f0; }
.ajuste-parada:last-child { border-bottom: none; }
.ajuste-parada { flex-wrap: wrap; }
.ajuste-parada .ap-nome { flex: 1; min-width: 140px; }
.ajuste-parada .ap-pos { width: 52px; padding: 5px; border-radius: 5px; border: 1px solid var(--borda); text-align: center; }
.ajuste-parada .ap-mensagem { flex: 1 1 100%; padding: 6px 8px; border-radius: 5px; border: 1px solid var(--borda); font-size: 0.78rem; margin-top: 4px; }
.ajuste-parada.ap-excluida { opacity: 0.5; text-decoration: line-through; }
.ajuste-botoes { display: flex; gap: 8px; flex-wrap: wrap; }
.ajuste-botoes button { padding: 8px 12px; border-radius: 6px; border: none; font-size: 0.78rem; font-weight: bold; cursor: pointer; }
.ajuste-botoes .btn-salvar { background: var(--azul); color: #fff; }
.ajuste-botoes .btn-limpar { background: #fff; border: 1.5px solid #c00000 !important; color: #c00000; }
.ajuste-status { font-size: 0.75rem; margin-top: 6px; min-height: 1.1em; }
.tag-ajustada { display: inline-block; background: var(--azul); color: #fff; font-size: 0.62rem; font-weight: bold; padding: 2px 6px; border-radius: 4px; margin-left: 6px; vertical-align: middle; }
.tag-mensagem { display: inline-block; background: #ff6d00; color: #fff; font-size: 0.62rem; font-weight: bold; padding: 2px 6px; border-radius: 4px; margin-left: 6px; vertical-align: middle; }
"""

_ROTA_TEMPLATE = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="theme-color" content="#1f4e78">
<title>{rotulo} - Rota de entrega</title>
<link rel="preconnect" href="https://unpkg.com">
<link rel="preconnect" href="https://tile.openstreetmap.org" crossorigin>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>{estilo}</style>
</head>
<body>
{bloqueio_html}<header>
  <h1>{rotulo}</h1>
  <div class="marca">{marca}</div>
  <div class="sub">Atualizado em {gerado_em}</div>
</header>
{aviso_linha_reta}<div id="avisoAjuste"></div><div id="mapa"></div>
<main>
  <ol class="paradas" id="listaParadas"></ol>
  <div class="resumo">
    <div><span>Original (ida/volta)</span><b>{dist_original:.2f} km</b></div>
    <div><span>Otimizada (ida/volta)</span><b>{dist_otimizada:.2f} km</b></div>
    <div><span>Economia</span><b>{economia_km:.2f} km ({economia_pct:.1f}%)</b></div>
    <div><span>Saida &rarr; 1a parada</span><b>{dist_origem_primeira:.2f} km</b></div>
    <div><span>Ultima parada &rarr; volta</span><b>{dist_retorno:.2f} km</b></div>
  </div>
</main>
<footer><a href="../index.html">Ver todas as rotas desta data</a></footer>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore-compat.js"></script>
<script>
const PARADAS = {paradas_json};
const ORIGEM = {origem_json};
const TRACADO_IDA = {tracado_ida_json};
const TRACADO_VOLTA = {tracado_volta_json};
const DATA_ISO = '{data_atual_iso}';
const ROTULO_ROTA = {rotulo_json};
const NUMERO_MOTORISTA = {numero_motorista_json};
const ROTA_ID = {rota_id_json};
const VEICULO_ORIGINAL = {veiculo_json};

// Status de entrega marcado pelo motorista (Entregue / Devolucao / Voltar
// depois-Fechado). Fica salvo em 2 lugares: localStorage (instantaneo,
// funciona ate' sem internet no momento do clique) e no Firestore (nuvem,
// pra sincronizar entre o celular do motorista e o painel do supervisor).
// Escopado por dia (chave inclui DATA_ISO) para que a marcacao de hoje nao
// apareca, por engano, numa rota de um dia futuro com o mesmo cliente.
const CORES_STATUS = {{ '': '#1f4e78', entregue: '#1e7e34', devolucao: '#c00000', fechado: '#b8860b' }};

let paradasEfetivas = PARADAS;

// Aplica um ajuste manual (feito pelo administrador) por cima da lista de
// paradas original: oculta as removidas, reordena pela posicao escolhida
// (codigo nao listado mantem a ordem relativa original, vai pro fim) e
// renumera a sequencia de 1..N -- a numeracao mostrada ao motorista e' sempre
// a "efetiva", nunca a original do PathFind quando ha ajuste.
function aplicarAjustePartadas(paradas, ajuste) {{
  let lista = paradas.slice();
  if (ajuste && ajuste.removidos && ajuste.removidos.length) {{
    const removidos = new Set(ajuste.removidos);
    lista = lista.filter(p => !removidos.has(p.codigo));
  }}
  if (ajuste && ajuste.ordem && ajuste.ordem.length) {{
    const posicao = new Map(ajuste.ordem.map((c, i) => [c, i]));
    lista.sort((a, b) => {{
      const pa = posicao.has(a.codigo) ? posicao.get(a.codigo) : Infinity;
      const pb = posicao.has(b.codigo) ? posicao.get(b.codigo) : Infinity;
      if (pa !== pb) return pa - pb;
      return a.seq - b.seq;
    }});
  }}
  const mensagens = (ajuste && ajuste.mensagens) || {{}};
  return lista.map((p, i) => Object.assign({{}}, p, {{ seq: i + 1, mensagem: mensagens[p.codigo] || null }}));
}}

let db = null;
try {{
  firebase.initializeApp({firebase_config_json});
  db = firebase.firestore();
}} catch (e) {{
  console.error('Firebase nao inicializou (marcacao vai funcionar so neste aparelho):', e);
}}

function chaveStatus(codigo) {{
  return 'status_' + DATA_ISO + '_' + codigo;
}}
function lerStatus(codigo) {{
  return localStorage.getItem(chaveStatus(codigo)) || '';
}}
function salvarStatusRemoto(p, status) {{
  if (!db) return;
  const ref = db.collection('{firestore_colecao}').doc(DATA_ISO + '_' + p.codigo);
  if (status) {{
    ref.set({{
      data: DATA_ISO,
      rotulo: ROTULO_ROTA,
      codigo: p.codigo,
      cliente: p.cliente,
      seq: p.seq,
      status: status,
      atualizado_em: firebase.firestore.FieldValue.serverTimestamp()
    }}).catch(err => console.error('Falha ao sincronizar status:', err));
  }} else {{
    ref.delete().catch(err => console.error('Falha ao limpar status remoto:', err));
  }}
}}
function iconeNumerado(numero, cor) {{
  cor = cor || '#1f4e78';
  return L.divIcon({{
    className: 'icone-parada',
    html: '<div style="background:' + cor + ';color:#fff;border-radius:50%;width:26px;height:26px;' +
          'display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:bold;' +
          'border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.4);">' + numero + '</div>',
    iconSize: [26, 26],
    iconAnchor: [13, 13]
  }});
}}

function aplicarStatus(li, status, marker, numero, temMensagem) {{
  li.classList.remove('status-entregue', 'status-devolucao', 'status-fechado');
  li.querySelectorAll('.status-btn').forEach(b => b.classList.remove('ativo'));
  if (status) {{
    li.classList.add('status-' + status);
    const btn = li.querySelector('.status-btn.' + status);
    if (btn) btn.classList.add('ativo');
  }}
  if (marker) {{
    const cor = status ? CORES_STATUS[status] : (temMensagem ? '#ff6d00' : CORES_STATUS['']);
    marker.setIcon(iconeNumerado(numero, cor));
  }}
}}

function iniciarPagina() {{
const listaEl = document.getElementById('listaParadas');
const mapa = L.map('mapa', {{ preferCanvas: true }});
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 19,
  updateWhenZooming: false,
  keepBuffer: 3,
  attribution: '&copy; OpenStreetMap contributors'
}}).addTo(mapa);

const iconeOrigem = L.divIcon({{
  className: 'icone-origem',
  html: '<div style="background:#c00000;color:#fff;border-radius:50%;width:26px;height:26px;' +
        'display:flex;align-items:center;justify-content:center;font-size:14px;' +
        'border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.4);">&#128666;</div>',
  iconSize: [26, 26],
  iconAnchor: [13, 13]
}});

const pontos = [[ORIGEM.lat, ORIGEM.lon]];
L.marker([ORIGEM.lat, ORIGEM.lon], {{ icon: iconeOrigem }})
  .bindPopup('<b>Saida do caminhao</b>')
  .addTo(mapa);

const referencias = {{}};  // codigo -> {{ li, marker }}, usado pra aplicar o status vindo do Firestore

paradasEfetivas.forEach(p => {{
  const li = document.createElement('li');
  li.className = 'parada' + (p.mensagem ? ' tem-mensagem' : '');
  const origemTxt = (p.seq === 1) ? 'saida' : 'parada anterior';
  const chegadaTxt = 'de ' + origemTxt + ': ' + p.dist_anterior_km.toFixed(2) + ' km';
  const proximaTxt = (p.dist_proxima_km === null) ? 'ultima parada'
    : ('proxima: ' + p.dist_proxima_km.toFixed(2) + ' km');
  const mapsUrl = 'https://www.google.com/maps/dir/?api=1&destination=' + p.lat + ',' + p.lon;
  const mensagemHtml = p.mensagem ? '<div class="mensagem-admin">Aviso: ' + p.mensagem + '</div>' : '';
  li.innerHTML =
    '<div class="num">' + p.seq + '</div>' +
    '<div class="info">' +
      '<div class="codigo">Pedido ' + parseInt(p.pedido, 10) + ' &middot; ' + p.codigo + '</div>' +
      '<div class="cliente">' + p.cliente + '</div>' +
      '<div class="endereco">' + p.endereco + '</div>' +
      mensagemHtml +
      '<div class="meta">' + chegadaTxt + ' &middot; ' + proximaTxt + '</div>' +
      '<div class="status-botoes">' +
        '<div class="status-btn entregue" data-status="entregue">Entregue</div>' +
        '<div class="status-btn devolucao" data-status="devolucao">Devolucao</div>' +
        '<div class="status-btn fechado" data-status="fechado">Voltar depois/Fechado</div>' +
      '</div>' +
      '<a class="btn-maps" href="' + mapsUrl + '" target="_blank" rel="noopener">Abrir no Google Maps</a>' +
    '</div>';
  listaEl.appendChild(li);
  pontos.push([p.lat, p.lon]);
  const statusInicial = lerStatus(p.codigo);
  const marker = L.marker([p.lat, p.lon], {{ icon: iconeNumerado(p.seq, CORES_STATUS[statusInicial]) }})
    .bindPopup('<b>' + p.seq + '. ' + p.codigo + ' - ' + p.cliente + '</b><br>' + p.endereco)
    .addTo(mapa);
  aplicarStatus(li, statusInicial, marker, p.seq, !!p.mensagem);
  referencias[p.codigo] = {{ li, marker, mensagem: !!p.mensagem }};
  li.querySelectorAll('.status-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      const status = btn.dataset.status;
      const novo = btn.classList.contains('ativo') ? '' : status;
      if (novo) {{
        localStorage.setItem(chaveStatus(p.codigo), novo);
      }} else {{
        localStorage.removeItem(chaveStatus(p.codigo));
      }}
      aplicarStatus(li, novo, marker, p.seq, !!p.mensagem);
      salvarStatusRemoto(p, novo);
    }});
  }});
}});

// Ao abrir a pagina, busca no Firestore o status mais recente de cada
// parada (pode ter sido marcado de outro aparelho) e sobrescreve o que
// veio do localStorage, que e' so' um cache instantaneo local.
if (db) {{
  db.collection('{firestore_colecao}').where('data', '==', DATA_ISO).get().then(snapshot => {{
    snapshot.forEach(doc => {{
      const dados = doc.data();
      const ref = referencias[dados.codigo];
      if (ref && dados.status && dados.status !== lerStatus(dados.codigo)) {{
        localStorage.setItem(chaveStatus(dados.codigo), dados.status);
        const seqNum = parseInt(ref.li.querySelector('.num').textContent, 10);
        aplicarStatus(ref.li, dados.status, ref.marker, seqNum, ref.mensagem);
      }}
    }});
  }}).catch(err => console.error('Falha ao buscar status remoto:', err));
}}

if (pontos.length > 1) {{
  // TRACADO_IDA/TRACADO_VOLTA vem do OSRM (segue as ruas de verdade). Se o
  // servico falhou na hora de gerar a pagina, caimos para uma linha reta
  // entre os pontos, so pra sempre ter algo desenhado no mapa.
  const linhaIda = (TRACADO_IDA && TRACADO_IDA.length > 1) ? TRACADO_IDA : pontos;
  const linhaVolta = (TRACADO_VOLTA && TRACADO_VOLTA.length > 1)
    ? TRACADO_VOLTA
    : [pontos[pontos.length - 1], [ORIGEM.lat, ORIGEM.lon]];
  L.polyline(linhaIda, {{ color: '#1f4e78', weight: 4, opacity: 0.75 }}).addTo(mapa);
  L.polyline(linhaVolta, {{ color: '#c00000', weight: 4, opacity: 0.65, dashArray: '6 8' }}).addTo(mapa);
}}
if (pontos.length) {{
  mapa.fitBounds(pontos, {{ padding: [30, 30] }});
}}
}}  // fim de iniciarPagina()

// --- controle de acesso por senha (por motorista) ---------------------
// Protecao simples: impede abertura casual do link. Nao e' seguranca forte
// (o codigo roda todo no navegador, alguem tecnico poderia contornar), mas
// evita que quem receba o link por engano veja dados do cliente. A senha
// fica guardada no Firestore (colecao "{firestore_colecao_motoristas}"),
// nunca no codigo desta pagina.
// Antes de decidir a senha/rotulo, busca se ha algum ajuste manual salvo
// pelo administrador pra essa rota+dia (troca de motorista/veiculo, ordem
// ou paradas removidas). Se houver, ele manda: inclusive a senha exigida
// passa a ser a do motorista definido no ajuste, nao a original do PathFind.
function iniciarComAjuste(ajuste) {{
  paradasEfetivas = aplicarAjustePartadas(PARADAS, ajuste);
  const numeroEfetivo = (ajuste && ajuste.motorista_numero) || NUMERO_MOTORISTA;
  const placaEfetiva = (ajuste && ajuste.veiculo) || VEICULO_ORIGINAL;
  if (ajuste) {{
    const rotuloEfetivo = numeroEfetivo
      ? (placaEfetiva || 'Veiculo') + ' - Motorista ' + numeroEfetivo
      : (placaEfetiva || 'Veiculo') + ' - Sem motorista definido';
    document.title = rotuloEfetivo + ' - Rota de entrega';
    const h1 = document.querySelector('header h1');
    if (h1) h1.textContent = rotuloEfetivo;
    const avisoEl = document.getElementById('avisoAjuste');
    if (avisoEl) {{
      avisoEl.className = 'aviso-ajuste';
      avisoEl.textContent = 'Esta rota foi ajustada manualmente pelo administrador.';
    }}
  }}

  if (!numeroEfetivo) {{
    iniciarPagina();  // sem motorista definido (original nem ajustado): nao da pra proteger, abre direto
    return;
  }}
  const chaveDesbloqueio = 'desbloqueado_motorista_' + numeroEfetivo;
  if (localStorage.getItem(chaveDesbloqueio) === 'sim') {{
    iniciarPagina();
    return;
  }}
  const form = document.getElementById('formSenha');
  if (!form) {{ iniciarPagina(); return; }}
  document.body.classList.add('bloqueado');
  const erroEl = document.getElementById('bloqueioErro');
  form.addEventListener('submit', ev => {{
    ev.preventDefault();
    if (!db) {{
      erroEl.textContent = 'Sem conexao com o servidor de senhas. Confira sua internet e tente de novo.';
      return;
    }}
    const senhaDigitada = document.getElementById('campoSenha').value;
    erroEl.textContent = 'Verificando...';
    db.collection('{firestore_colecao_motoristas}').doc(numeroEfetivo).get().then(doc => {{
      if (doc.exists && doc.data().senha === senhaDigitada) {{
        localStorage.setItem(chaveDesbloqueio, 'sim');
        document.body.classList.remove('bloqueado');
        iniciarPagina();
      }} else {{
        erroEl.textContent = 'Senha incorreta.';
      }}
    }}).catch(err => {{
      erroEl.textContent = 'Erro ao verificar a senha. Confira sua internet.';
      console.error(err);
    }});
  }});
}}

if (db && ROTA_ID) {{
  db.collection('{firestore_colecao_ajustes}').doc(DATA_ISO + '_' + ROTA_ID).get()
    .then(doc => iniciarComAjuste(doc.exists ? doc.data() : null))
    .catch(err => {{ console.error('Falha ao buscar ajuste manual:', err); iniciarComAjuste(null); }});
}} else {{
  iniciarComAjuste(null);
}}
</script>
</body>
</html>
"""

# --- filtro de Ano/Mes/Dia, reaproveitado em index/painel/admin/analista ---
# HTML e JS ficam concatenados diretamente no texto de cada template (nao
# via .format() separado, ja que os placeholders -- manifest_rel,
# historico_base_rel, data_atual_iso/br, arquivo_destino -- so' tem valor
# real no momento em que o template inteiro e' formatado).
_HTML_FILTRO_DATA = """<div class="filtro-data">
  <label for="seletorAno">Ver rotas de:</label>
  <select id="seletorAno" class="compacta"></select>
  <select id="seletorMes" class="compacta"></select>
  <select id="seletorData"><option value="{data_atual_iso}">{data_atual_br} (hoje)</option></select>
  <a href="{hoje_rel}">Ir para hoje</a>
</div>
"""

_JS_FILTRO_DATA = """
const NOMES_MESES = ['Janeiro','Fevereiro','Marco','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro'];
fetch('{manifest_rel}').then(r => r.json()).then(dias => {{
  const selAno = document.getElementById('seletorAno');
  const selMes = document.getElementById('seletorMes');
  const selDia = document.getElementById('seletorData');

  dias.forEach(d => {{
    d._ano = d.data.slice(0, 4);
    d._mes = d.data.slice(5, 7);
  }});

  function popularAnos(anoSelecionado) {{
    const anos = [...new Set(dias.map(d => d._ano))].sort().reverse();
    selAno.innerHTML = '';
    anos.forEach(a => {{
      const opt = document.createElement('option');
      opt.value = a;
      opt.textContent = a;
      selAno.appendChild(opt);
    }});
    selAno.value = anos.includes(anoSelecionado) ? anoSelecionado : anos[0];
  }}

  function popularMeses(ano, mesSelecionado) {{
    const mesesDoAno = [...new Set(dias.filter(d => d._ano === ano).map(d => d._mes))].sort().reverse();
    selMes.innerHTML = '';
    mesesDoAno.forEach(m => {{
      const opt = document.createElement('option');
      opt.value = m;
      opt.textContent = NOMES_MESES[parseInt(m, 10) - 1];
      selMes.appendChild(opt);
    }});
    selMes.value = mesesDoAno.includes(mesSelecionado) ? mesSelecionado : mesesDoAno[0];
  }}

  function popularDias(ano, mes) {{
    const diasDoMes = dias.filter(d => d._ano === ano && d._mes === mes);
    selDia.innerHTML = '';
    diasDoMes.forEach(d => {{
      const opt = document.createElement('option');
      opt.value = d.data;
      opt.textContent = d.data_br + ' (' + d.rotas + ' rota(s))';
      selDia.appendChild(opt);
    }});
    if (diasDoMes.length) selDia.value = diasDoMes[0].data;
  }}

  const anoAtual = '{data_atual_iso}'.slice(0, 4);
  const mesAtual = '{data_atual_iso}'.slice(5, 7);
  popularAnos(anoAtual);
  popularMeses(selAno.value, mesAtual);
  popularDias(selAno.value, selMes.value);
  selDia.value = '{data_atual_iso}';

  selAno.addEventListener('change', () => {{
    popularMeses(selAno.value, selMes.value);
    popularDias(selAno.value, selMes.value);
  }});
  selMes.addEventListener('change', () => {{
    popularDias(selAno.value, selMes.value);
  }});
  selDia.addEventListener('change', () => {{
    if (selDia.value) window.location.href = '{historico_base_rel}' + selDia.value + '/{arquivo_destino}';
  }});
}}).catch(() => {{}});
"""

# --- mapa combinado (todas as entregas do dia, coloridas por motorista) --
# Reaproveitado em painel/admin/analista. Cada pagina monta sua propria
# lista de grupos ([{{ rotulo, paradas: [{{lat,lon,seq,cliente}}] }}], ja
# filtrada pelo motorista selecionado) e chama construirMapaGeral(grupos).
# So' inicializa o Leaflet quando o <details> e' aberto pela 1a vez (o mapa
# nao renderiza direito dentro de um container escondido, e nao faz sentido
# pagar o custo de centenas de marcadores se ninguem abrir).
_HTML_MAPA_GERAL = """<details class="mapa-geral-details" id="detalhesMapaGeral">
  <summary>Ver mapa de todas as entregas de hoje</summary>
  <div id="mapaGeral" class="mapa-geral"></div>
  <div id="legendaMotoristas" class="legenda-motoristas"></div>
</details>
"""

_JS_MAPA_GERAL = """
const PALETA_CORES = ['#1f4e78','#c00000','#1e7e34','#b8860b','#6a1b9a','#008b8b','#d2691e','#4169e1','#a0522d','#2e8b57','#dc143c','#4682b4','#8b008b','#556b2f'];
var mapaGeralInstancia = null;

function corMotorista(indice) {{
  return PALETA_CORES[indice % PALETA_CORES.length];
}}

function construirMapaGeral(grupos) {{
  const mapa = L.map('mapaGeral', {{ preferCanvas: true }});
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors'
  }}).addTo(mapa);

  const iconeOrigem = L.divIcon({{
    className: 'icone-origem',
    html: '<div style="background:#c00000;color:#fff;border-radius:50%;width:22px;height:22px;' +
          'display:flex;align-items:center;justify-content:center;font-size:12px;' +
          'border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.4);">&#128666;</div>',
    iconSize: [22, 22],
    iconAnchor: [11, 11]
  }});

  const pontos = [[ORIGEM_GERAL.lat, ORIGEM_GERAL.lon]];
  L.marker([ORIGEM_GERAL.lat, ORIGEM_GERAL.lon], {{ icon: iconeOrigem }})
    .bindPopup('<b>Saida do caminhao</b>')
    .addTo(mapa);

  const legendaEl = document.getElementById('legendaMotoristas');
  legendaEl.innerHTML = '';
  grupos.forEach((grupo, i) => {{
    const cor = corMotorista(i);
    const item = document.createElement('div');
    item.className = 'legenda-item';
    item.innerHTML = '<span class="legenda-cor" style="background:' + cor + '"></span>' + grupo.rotulo;
    legendaEl.appendChild(item);
    grupo.paradas.forEach(p => {{
      pontos.push([p.lat, p.lon]);
      L.circleMarker([p.lat, p.lon], {{
        radius: 7, color: '#fff', weight: 1.5, fillColor: cor, fillOpacity: 0.9
      }}).bindPopup('<b>' + grupo.rotulo + '</b><br>' + p.seq + '. ' + p.cliente).addTo(mapa);
    }});
  }});
  if (pontos.length) mapa.fitBounds(pontos, {{ padding: [30, 30] }});
  return mapa;
}}

function atualizarMapaGeral() {{
  try {{
    const detalhes = document.getElementById('detalhesMapaGeral');
    if (!detalhes || !detalhes.open) return;
    if (mapaGeralInstancia) {{
      mapaGeralInstancia.remove();
      mapaGeralInstancia = null;
    }}
    mapaGeralInstancia = construirMapaGeral(gruposParaMapa(motoristaFiltroAtual()));
  }} catch (e) {{
    console.error('Falha ao montar o mapa combinado:', e);
  }}
}}

const detalhesMapaGeralEl = document.getElementById('detalhesMapaGeral');
if (detalhesMapaGeralEl) {{
  detalhesMapaGeralEl.addEventListener('toggle', atualizarMapaGeral);
}}
"""

# --- filtro de motorista, reaproveitado em painel/admin/analista ---------
# So' cuida do <select> e de guardar o valor escolhido; quem decide o que
# esconder/mostrar com esse valor e' cada pagina, numa funcao propria
# chamada aplicarFiltroMotorista(id) (declarada com "function", nao arrow,
# pra contar com hoisting e poder vir depois deste bloco no <script>).
_HTML_FILTRO_MOTORISTA = """<div class="filtro-motorista">
  <label for="seletorMotorista">Motorista:</label>
  <select id="seletorMotorista"><option value="">Todos</option></select>
</div>
"""

_JS_FILTRO_MOTORISTA = """
var motoristaFiltroValor = '';
function motoristaFiltroAtual() {{ return motoristaFiltroValor; }}

function popularFiltroMotorista(rotas) {{
  const sel = document.getElementById('seletorMotorista');
  if (!sel) return;
  rotas.forEach(r => {{
    const opt = document.createElement('option');
    opt.value = r.id;
    opt.textContent = r.rotulo;
    sel.appendChild(opt);
  }});
  sel.addEventListener('change', () => {{
    motoristaFiltroValor = sel.value;
    aplicarFiltroMotorista(motoristaFiltroValor);
    atualizarMapaGeral();
  }});
}}
"""

_INDEX_TEMPLATE = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="theme-color" content="#1f4e78">
<title>{titulo_pagina} - PathFind</title>
<style>{estilo}</style>
</head>
<body>
<header>
  <h1>{titulo_pagina}</h1>
  <div class="marca">{marca}</div>
  <div class="sub">Atualizado em {gerado_em}</div>
</header>
""" + _HTML_FILTRO_DATA + """{link_painel_index}<main>
  <ul class="lista-rotas">
    {itens}
  </ul>
</main>
<footer>Gerado automaticamente a partir do relatorio do dia.</footer>

<script>""" + _JS_FILTRO_DATA + """
</script>
</body>
</html>
"""

_PAINEL_TEMPLATE = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="theme-color" content="#1f4e78">
<title>Painel do supervisor - {data_atual_br}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>{estilo}</style>
</head>
<body class="bloqueado">
<div class="bloqueio"><div class="bloqueio-caixa">
  <h2>Painel do supervisor</h2>
  <p>Digite a senha administrativa pra continuar.</p>
  <form id="formSenhaAdmin">
    <input type="password" id="campoSenhaAdmin" placeholder="Senha administrativa" autocomplete="current-password" required>
    <button type="submit">Entrar</button>
  </form>
  <div class="bloqueio-erro" id="bloqueioErroAdmin"></div>
</div></div>
<header>
  <h1>Painel do supervisor</h1>
  <div class="marca">{marca}</div>
  <div class="sub">Rotas de {data_atual_br}</div>
</header>
""" + _HTML_FILTRO_DATA + _HTML_FILTRO_MOTORISTA + _HTML_MAPA_GERAL + """<main id="painel"></main>
<div class="painel-atualizado" id="statusConexao">Conectando ao painel em tempo real...</div>
<footer><a href="index.html">Ver lista de rotas</a></footer>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore-compat.js"></script>
<script>
const ROTAS = {rotas_json};
const DATA_ISO = '{data_atual_iso}';
const ORIGEM_GERAL = {origem_json};
const painelEl = document.getElementById('painel');
const statusConexaoEl = document.getElementById('statusConexao');

let db = null;
try {{
  firebase.initializeApp({firebase_config_json});
  db = firebase.firestore();
}} catch (e) {{
  statusConexaoEl.textContent = 'Nao foi possivel conectar ao painel em tempo real.';
  console.error(e);
}}

// Controle de acesso: pede a senha administrativa (guardada no Firestore,
// colecao "{firestore_colecao_motoristas}", documento "admin"). Fica
// desbloqueado so' durante esta aba/sessao (sessionStorage), nao para
// sempre -- ao fechar o navegador, pede a senha de novo.
if (sessionStorage.getItem('painel_desbloqueado') === 'sim') {{
  document.body.classList.remove('bloqueado');
}} else {{
  document.getElementById('formSenhaAdmin').addEventListener('submit', ev => {{
    ev.preventDefault();
    const erroEl = document.getElementById('bloqueioErroAdmin');
    if (!db) {{
      erroEl.textContent = 'Sem conexao. Confira sua internet e tente de novo.';
      return;
    }}
    const senhaDigitada = document.getElementById('campoSenhaAdmin').value;
    erroEl.textContent = 'Verificando...';
    db.collection('{firestore_colecao_motoristas}').doc('admin').get().then(doc => {{
      if (doc.exists && doc.data().senha === senhaDigitada) {{
        sessionStorage.setItem('painel_desbloqueado', 'sim');
        document.body.classList.remove('bloqueado');
      }} else {{
        erroEl.textContent = 'Senha incorreta.';
      }}
    }}).catch(err => {{
      erroEl.textContent = 'Erro ao verificar a senha.';
      console.error(err);
    }});
  }});
}}

const RESPOSTA_STATUS = {{ entregue: 'Entregue', devolucao: 'Devolucao', fechado: 'Voltar depois/Fechado', '': 'Pendente' }};

// Mesma logica de _ROTA_TEMPLATE: aplica um ajuste manual (feito pelo
// administrador) por cima das paradas de uma rota -- oculta removidas,
// reordena e renumera. Duplicada aqui porque este gerador estatico nao tem
// um modulo JS compartilhado entre paginas.
function aplicarAjustePartadas(paradas, ajuste) {{
  let lista = paradas.slice();
  if (ajuste && ajuste.removidos && ajuste.removidos.length) {{
    const removidos = new Set(ajuste.removidos);
    lista = lista.filter(p => !removidos.has(p.codigo));
  }}
  if (ajuste && ajuste.ordem && ajuste.ordem.length) {{
    const posicao = new Map(ajuste.ordem.map((c, i) => [c, i]));
    lista.sort((a, b) => {{
      const pa = posicao.has(a.codigo) ? posicao.get(a.codigo) : Infinity;
      const pb = posicao.has(b.codigo) ? posicao.get(b.codigo) : Infinity;
      if (pa !== pb) return pa - pb;
      return a.seq - b.seq;
    }});
  }}
  const mensagens = (ajuste && ajuste.mensagens) || {{}};
  return lista.map((p, i) => Object.assign({{}}, p, {{ seq: i + 1, mensagem: mensagens[p.codigo] || null }}));
}}

function rotuloComAjuste(rota, ajuste) {{
  if (!ajuste) return rota.rotulo;
  const numero = ajuste.motorista_numero || rota.motorista_numero;
  const placa = ajuste.veiculo || rota.veiculo;
  return numero
    ? (placa || 'Veiculo') + ' - Motorista ' + numero
    : (placa || 'Veiculo') + ' - Sem motorista definido';
}}

let ultimoStatus = {{}};
let ultimosAjustes = {{}};

function renderizar() {{
  painelEl.innerHTML = '';
  ROTAS.forEach(rota => {{
    const ajuste = ultimosAjustes[rota.id] || null;
    const paradasEfetivas = aplicarAjustePartadas(rota.paradas, ajuste);
    const contagem = {{ entregue: 0, devolucao: 0, fechado: 0, pendente: 0 }};
    const linhas = paradasEfetivas.map(p => {{
      const status = ultimoStatus[p.codigo] || '';
      contagem[status || 'pendente']++;
      return '<div class="painel-linha status-' + (status || 'pendente') + '">' +
        '<span class="p-seq">' + p.seq + '. ' + p.cliente + (p.mensagem ? ' <span class="tag-mensagem">aviso</span>' : '') + '</span>' +
        '<span class="p-status">' + RESPOSTA_STATUS[status] + '</span>' +
        '<button class="btn-aviso" data-rota="' + rota.id + '" data-codigo="' + p.codigo + '">' + (p.mensagem ? 'Editar aviso' : 'Lancar aviso') + '</button>' +
        '</div>';
    }}).join('');
    const card = document.createElement('div');
    card.className = 'painel-rota';
    card.dataset.rotaId = rota.id;
    card.innerHTML =
      '<h2>' + rotuloComAjuste(rota, ajuste) + (ajuste ? ' <span class="tag-ajustada">ajustada</span>' : '') + '</h2>' +
      '<div class="painel-contagem">' +
        '<span class="c-entregue">Entregues: ' + contagem.entregue + '</span>' +
        '<span class="c-devolucao">Devolucoes: ' + contagem.devolucao + '</span>' +
        '<span class="c-fechado">Fechados: ' + contagem.fechado + '</span>' +
        '<span class="c-pendente">Pendentes: ' + contagem.pendente + '</span>' +
      '</div>' +
      '<div class="painel-linhas">' + linhas + '</div>';
    painelEl.appendChild(card);
  }});
  aplicarFiltroMotorista(motoristaFiltroAtual());
}}

// Esconde/mostra os cards de rota que nao batem com o motorista escolhido
// no filtro (chamada toda vez que a lista e' redesenhada, ja que
// renderizar() reconstroi o HTML do zero a cada atualizacao em tempo real).
function aplicarFiltroMotorista(id) {{
  document.querySelectorAll('.painel-rota').forEach(card => {{
    card.style.display = (!id || card.dataset.rotaId === id) ? '' : 'none';
  }});
}}

// Monta a lista de grupos (rotulo + paradas com lat/lon) pro mapa
// combinado, aplicando o mesmo ajuste manual (motorista/ordem/exclusao)
// que os cards da lista ja mostram -- e respeitando o filtro de motorista.
function gruposParaMapa(idFiltro) {{
  return ROTAS.filter(r => !idFiltro || r.id === idFiltro).map(r => {{
    const ajuste = ultimosAjustes[r.id] || null;
    return {{ rotulo: rotuloComAjuste(r, ajuste), paradas: aplicarAjustePartadas(r.paradas, ajuste) }};
  }});
}}

// Permite ao supervisor lancar (ou editar) um aviso pra um pedido direto do
// painel, sem precisar entrar no admin. Escuta cliques no botao "Lancar
// aviso" via delegacao (o HTML das linhas e' recriado a cada renderizar()),
// e grava so' o campo "mensagens" desse pedido, com merge:true -- assim nao
// apaga um ajuste de motorista/veiculo/ordem que o admin/analista ja tenha
// salvo nessa mesma rota.
painelEl.addEventListener('click', ev => {{
  const btn = ev.target.closest('.btn-aviso');
  if (!btn || !db) return;
  const rotaId = btn.dataset.rota;
  const codigo = btn.dataset.codigo;
  const ajusteAtual = ultimosAjustes[rotaId];
  const mensagemAtual = (ajusteAtual && ajusteAtual.mensagens && ajusteAtual.mensagens[codigo]) || '';
  const novaMensagem = window.prompt('Aviso para o motorista sobre esse pedido:', mensagemAtual);
  if (novaMensagem === null) return;
  db.collection('{firestore_colecao_ajustes}').doc(DATA_ISO + '_' + rotaId).set({{
    data: DATA_ISO,
    rota_id: rotaId,
    mensagens: {{ [codigo]: novaMensagem.trim() }}
  }}, {{ merge: true }}).catch(err => {{
    alert('Erro ao salvar o aviso. Confira sua internet e tente de novo.');
    console.error(err);
  }});
}});

popularFiltroMotorista(ROTAS);

// Ja renderiza a lista completa (todos "pendente") antes mesmo do Firestore
// responder, pra pagina nao ficar em branco -- e depois atualiza sozinha,
// em tempo real, toda vez que algum motorista marcar algo ou o
// administrador salvar um ajuste manual (onSnapshot nas duas colecoes).
renderizar();

if (db) {{
  db.collection('{firestore_colecao}').where('data', '==', DATA_ISO)
    .onSnapshot(snapshot => {{
      ultimoStatus = {{}};
      snapshot.forEach(doc => {{
        const d = doc.data();
        ultimoStatus[d.codigo] = d.status;
      }});
      renderizar();
      const agora = new Date().toLocaleTimeString('pt-BR', {{ hour: '2-digit', minute: '2-digit', second: '2-digit' }});
      statusConexaoEl.textContent = 'Atualizado automaticamente às ' + agora;
    }}, err => {{
      statusConexaoEl.textContent = 'Erro ao conectar ao painel em tempo real.';
      console.error(err);
    }});

  db.collection('{firestore_colecao_ajustes}').where('data', '==', DATA_ISO)
    .onSnapshot(snapshot => {{
      ultimosAjustes = {{}};
      snapshot.forEach(doc => {{
        const d = doc.data();
        ultimosAjustes[d.rota_id] = d;
      }});
      renderizar();
      atualizarMapaGeral();
    }}, err => console.error('Falha ao acompanhar ajustes manuais:', err));
}}
""" + _JS_FILTRO_DATA + _JS_FILTRO_MOTORISTA + _JS_MAPA_GERAL + """
</script>
</body>
</html>
"""


# Bloco de JS reaproveitado tanto em _ADMIN_TEMPLATE quanto em
# _ANALISTA_TEMPLATE (edicao de motorista/veiculo/ordem/exclusao/mensagem de
# uma rota do dia). Formatado uma unica vez (ver _JS_AJUSTE_ROTAS_RENDERED,
# mais abaixo) e embutido como texto pronto nos dois templates, ja que este
# gerador estatico nao tem um modulo JS compartilhado de verdade.
_JS_AJUSTE_ROTAS = """
// --- ajuste manual de rotas (motorista, veiculo, ordem, exclusao, mensagem) ---

function montarCardAjuste(rota) {{
  const card = document.createElement('details');
  card.className = 'ajuste-rota';
  card.dataset.rotaId = rota.id;
  card.innerHTML =
    '<summary>' + rota.rotulo + ' <small>(' + rota.paradas.length + ' paradas)</small></summary>' +
    '<div class="ajuste-campos">' +
      '<div class="ajuste-campo">' +
        '<label for="ajusteMotorista-' + rota.id + '">Motorista</label>' +
        '<select id="ajusteMotorista-' + rota.id + '"></select>' +
      '</div>' +
      '<div class="ajuste-campo">' +
        '<label for="ajusteVeiculo-' + rota.id + '">Veiculo (placa)</label>' +
        '<input type="text" id="ajusteVeiculo-' + rota.id + '" placeholder="' + (rota.veiculo || 'sem veiculo') + '">' +
      '</div>' +
    '</div>' +
    '<div class="ajuste-paradas" id="ajusteParadas-' + rota.id + '"></div>' +
    '<div class="ajuste-botoes">' +
      '<button class="btn-salvar">Salvar ajustes</button>' +
      '<button class="btn-limpar">Limpar ajustes (voltar ao original)</button>' +
    '</div>' +
    '<div class="ajuste-status" id="ajusteStatus-' + rota.id + '"></div>';

  const selMotorista = card.querySelector('#ajusteMotorista-' + rota.id);
  const optManter = document.createElement('option');
  optManter.value = '';
  optManter.textContent = 'Manter original (' + (rota.motorista_numero || 'sem motorista') + ')';
  selMotorista.appendChild(optManter);
  for (let n = NUMERO_MIN; n <= NUMERO_MAX; n++) {{
    const opt = document.createElement('option');
    opt.value = String(n);
    opt.textContent = String(n);
    selMotorista.appendChild(opt);
  }}

  const paradasEl = card.querySelector('#ajusteParadas-' + rota.id);
  rota.paradas.forEach(p => {{
    const linha = document.createElement('div');
    linha.className = 'ajuste-parada';
    linha.dataset.codigo = p.codigo;
    linha.innerHTML =
      '<input type="number" class="ap-pos" value="' + p.seq + '">' +
      '<span class="ap-nome">' + p.seq + '. ' + p.cliente + ' <small>(' + p.codigo + ')</small></span>' +
      '<label><input type="checkbox" class="ap-excluir"> excluir</label>' +
      '<input type="text" class="ap-mensagem" placeholder="Mensagem pro motorista sobre esse pedido (opcional)">';
    linha.querySelector('.ap-excluir').addEventListener('change', ev => {{
      linha.classList.toggle('ap-excluida', ev.target.checked);
    }});
    paradasEl.appendChild(linha);
  }});

  card.querySelector('.btn-salvar').addEventListener('click', () => salvarAjuste(rota));
  card.querySelector('.btn-limpar').addEventListener('click', () => limparAjuste(rota));

  return card;
}}

function salvarAjuste(rota) {{
  const statusEl = document.getElementById('ajusteStatus-' + rota.id);
  const motoristaEl = document.getElementById('ajusteMotorista-' + rota.id);
  const veiculoEl = document.getElementById('ajusteVeiculo-' + rota.id);
  const linhas = Array.from(document.querySelectorAll('#ajusteParadas-' + rota.id + ' .ajuste-parada'));

  const removidos = [];
  const comPosicao = [];
  const mensagens = {{}};
  linhas.forEach((linha, i) => {{
    const codigo = linha.dataset.codigo;
    const mensagem = linha.querySelector('.ap-mensagem').value.trim();
    if (mensagem) mensagens[codigo] = mensagem;
    if (linha.querySelector('.ap-excluir').checked) {{ removidos.push(codigo); return; }}
    const pos = parseFloat(linha.querySelector('.ap-pos').value);
    comPosicao.push({{ codigo: codigo, pos: isNaN(pos) ? i : pos, i: i }});
  }});
  comPosicao.sort((a, b) => (a.pos - b.pos) || (a.i - b.i));
  const ordem = comPosicao.map(x => x.codigo);

  const doc = {{
    data: DATA_ISO,
    rota_id: rota.id,
    motorista_numero: motoristaEl.value || null,
    veiculo: veiculoEl.value.trim() || null,
    removidos: removidos,
    ordem: ordem,
    mensagens: mensagens,
    atualizado_em: firebase.firestore.FieldValue.serverTimestamp()
  }};
  statusEl.textContent = 'Salvando...';
  statusEl.style.color = '';
  db.collection('{firestore_colecao_ajustes}').doc(DATA_ISO + '_' + rota.id).set(doc)
    .then(() => {{ statusEl.textContent = 'Ajustes salvos.'; statusEl.style.color = '#1e7e34'; }})
    .catch(err => {{ statusEl.textContent = 'Erro ao salvar os ajustes.'; statusEl.style.color = '#c00000'; console.error(err); }});
}}

function limparAjuste(rota) {{
  const statusEl = document.getElementById('ajusteStatus-' + rota.id);
  db.collection('{firestore_colecao_ajustes}').doc(DATA_ISO + '_' + rota.id).delete()
    .then(() => {{
      statusEl.textContent = 'Ajustes removidos, rota voltou ao original do PathFind.';
      statusEl.style.color = '#1e7e34';
      document.getElementById('ajusteMotorista-' + rota.id).value = '';
      document.getElementById('ajusteVeiculo-' + rota.id).value = '';
      document.querySelectorAll('#ajusteParadas-' + rota.id + ' .ajuste-parada').forEach((linha, i) => {{
        linha.querySelector('.ap-excluir').checked = false;
        linha.classList.remove('ap-excluida');
        linha.querySelector('.ap-pos').value = i + 1;
        linha.querySelector('.ap-mensagem').value = '';
      }});
    }})
    .catch(err => {{ statusEl.textContent = 'Erro ao limpar os ajustes.'; statusEl.style.color = '#c00000'; console.error(err); }});
}}

function carregarAjustes() {{
  const listaEl = document.getElementById('listaAjustes');
  listaEl.innerHTML = '';
  if (!ROTAS_HOJE.length) {{
    listaEl.innerHTML = '<p style="text-align:center; font-size:0.8rem; color:#888;">Nenhuma rota gerada ainda hoje.</p>';
    return;
  }}
  ROTAS_HOJE.forEach(rota => {{
    const card = montarCardAjuste(rota);
    listaEl.appendChild(card);
    db.collection('{firestore_colecao_ajustes}').doc(DATA_ISO + '_' + rota.id).get().then(doc => {{
      if (!doc.exists) return;
      const d = doc.data();
      if (d.motorista_numero) document.getElementById('ajusteMotorista-' + rota.id).value = d.motorista_numero;
      if (d.veiculo) document.getElementById('ajusteVeiculo-' + rota.id).value = d.veiculo;
      const removidos = new Set(d.removidos || []);
      const posicao = new Map((d.ordem || []).map((c, i) => [c, i]));
      const mensagens = d.mensagens || {{}};
      document.querySelectorAll('#ajusteParadas-' + rota.id + ' .ajuste-parada').forEach(linha => {{
        const codigo = linha.dataset.codigo;
        if (removidos.has(codigo)) {{
          linha.querySelector('.ap-excluir').checked = true;
          linha.classList.add('ap-excluida');
        }}
        if (posicao.has(codigo)) {{
          linha.querySelector('.ap-pos').value = posicao.get(codigo) + 1;
        }}
        if (mensagens[codigo]) {{
          linha.querySelector('.ap-mensagem').value = mensagens[codigo];
        }}
      }});
      card.open = true;
      card.querySelector('summary').insertAdjacentHTML('beforeend', ' <span class="tag-ajustada">ajustada</span>');
    }}).catch(err => console.error('Falha ao carregar ajuste existente:', err));
  }});
}}
"""
_JS_AJUSTE_ROTAS_RENDERED = _JS_AJUSTE_ROTAS.format(firestore_colecao_ajustes=FIRESTORE_COLECAO_AJUSTES)


_ADMIN_TEMPLATE = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="theme-color" content="#1f4e78">
<title>Administracao de senhas</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>{estilo}</style>
</head>
<body class="bloqueado">
<div class="bloqueio"><div class="bloqueio-caixa">
  <h2>Administracao</h2>
  <p>Digite a senha administrativa pra continuar.</p>
  <form id="formSenhaAdmin">
    <input type="password" id="campoSenhaAdmin" placeholder="Senha administrativa" autocomplete="current-password" required>
    <button type="submit">Entrar</button>
  </form>
  <div class="bloqueio-erro" id="bloqueioErroAdmin"></div>
</div></div>
<header>
  <h1>Administracao de senhas</h1>
  <div class="marca">{marca}</div>
</header>
""" + _HTML_FILTRO_DATA + _HTML_FILTRO_MOTORISTA + _HTML_MAPA_GERAL + """<main>
  <h2 class="secao-titulo">Ajustar rotas de hoje</h2>
  <p style="font-size:0.78rem; color:#666; margin: 0 12px 10px 12px;">
    Corrija motorista, veiculo, ordem de visita ou tire uma parada de uma rota
    de hoje sem esperar um novo arquivo do PathFind. O km mostrado ao
    motorista continua sendo o original; a pagina dele so' avisa que a rota
    foi ajustada manualmente.
  </p>
  <div id="listaAjustes"></div>

  <h2 class="secao-titulo">Senhas dos motoristas</h2>
  <div class="admin-topo">
    <button id="btnGerarPadrao">Gerar senha padrao pra todo mundo</button>
  </div>
  <div class="admin-msg" id="adminMsg"></div>
  <div id="listaMotoristas"></div>
  <div class="admin-linha" style="margin-top:16px;">
    <span>Admin</span>
    <input type="text" id="novaSenhaAdmin" placeholder="Nova senha administrativa">
    <button id="btnSalvarAdmin">Salvar</button>
  </div>
</main>
<footer><a href="index.html">Ver lista de rotas</a></footer>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore-compat.js"></script>
<script>
const NUMERO_MIN = {numero_min};
const NUMERO_MAX = {numero_max};
const DATA_ISO = '{data_atual_iso}';
const ROTAS_HOJE = {rotas_hoje_json};
const ORIGEM_GERAL = {origem_json};
const adminMsgEl = document.getElementById('adminMsg');

let db = null;
try {{
  firebase.initializeApp({firebase_config_json});
  db = firebase.firestore();
}} catch (e) {{
  console.error(e);
}}

// Filtro de motorista: esconde os cards de "Ajustar rotas de hoje" que nao
// batem, e filtra o que aparece no mapa combinado (que aqui usa sempre as
// paradas originais do PathFind, sem juntar ajuste manual -- o objetivo do
// mapa aqui e' so' ajudar a enxergar onde fica tudo enquanto edita).
function aplicarFiltroMotorista(id) {{
  document.querySelectorAll('.ajuste-rota').forEach(card => {{
    card.style.display = (!id || card.dataset.rotaId === id) ? '' : 'none';
  }});
}}
function gruposParaMapa(idFiltro) {{
  return ROTAS_HOJE.filter(r => !idFiltro || r.id === idFiltro).map(r => ({{ rotulo: r.rotulo, paradas: r.paradas }}));
}}
popularFiltroMotorista(ROTAS_HOJE);

function senhaPadrao(numero) {{
  return numero + 'OG' + (numero % 10);
}}

function mensagem(texto, ehErro) {{
  adminMsgEl.textContent = texto;
  adminMsgEl.style.color = ehErro ? '#c00000' : '#1e7e34';
}}

function montarLinha(numero) {{
  const linha = document.createElement('div');
  linha.className = 'admin-linha';
  linha.innerHTML =
    '<span>' + numero + '</span>' +
    '<input type="text" id="senha-' + numero + '" placeholder="(sem senha definida)">' +
    '<button data-numero="' + numero + '">Salvar</button>';
  linha.querySelector('button').addEventListener('click', () => {{
    const valor = document.getElementById('senha-' + numero).value.trim();
    if (!valor) {{ mensagem('Digite uma senha antes de salvar (motorista ' + numero + ').', true); return; }}
    db.collection('{firestore_colecao_motoristas}').doc(String(numero)).set({{ senha: valor }})
      .then(() => mensagem('Senha do motorista ' + numero + ' salva.', false))
      .catch(err => {{ mensagem('Erro ao salvar a senha do motorista ' + numero + '.', true); console.error(err); }});
  }});
  return linha;
}}

{js_ajuste_rotas}

function carregarMotoristas() {{
  const listaEl = document.getElementById('listaMotoristas');
  listaEl.innerHTML = '';
  for (let numero = NUMERO_MIN; numero <= NUMERO_MAX; numero++) {{
    listaEl.appendChild(montarLinha(numero));
    db.collection('{firestore_colecao_motoristas}').doc(String(numero)).get().then(doc => {{
      if (doc.exists && doc.data().senha) {{
        const campo = document.getElementById('senha-' + numero);
        if (campo) campo.value = doc.data().senha;
      }}
    }}).catch(err => console.error(err));
  }}
}}

document.getElementById('btnGerarPadrao').addEventListener('click', () => {{
  if (!confirm('Isso substitui a senha de TODOS os motoristas (' + NUMERO_MIN + ' a ' + NUMERO_MAX + ') pelo padrao. Continuar?')) return;
  let pendentes = 0;
  for (let numero = NUMERO_MIN; numero <= NUMERO_MAX; numero++) {{
    pendentes++;
    const senha = senhaPadrao(numero);
    db.collection('{firestore_colecao_motoristas}').doc(String(numero)).set({{ senha: senha }})
      .then(() => {{
        const campo = document.getElementById('senha-' + numero);
        if (campo) campo.value = senha;
        pendentes--;
        if (pendentes === 0) mensagem('Senhas padrao geradas para todos os motoristas.', false);
      }})
      .catch(err => {{ mensagem('Erro ao gerar as senhas padrao.', true); console.error(err); }});
  }}
}});

document.getElementById('btnSalvarAdmin').addEventListener('click', () => {{
  const valor = document.getElementById('novaSenhaAdmin').value.trim();
  if (!valor) {{ mensagem('Digite a nova senha administrativa antes de salvar.', true); return; }}
  db.collection('{firestore_colecao_motoristas}').doc('admin').set({{ senha: valor }})
    .then(() => {{ mensagem('Senha administrativa atualizada.', false); document.getElementById('novaSenhaAdmin').value = ''; }})
    .catch(err => {{ mensagem('Erro ao salvar a senha administrativa.', true); console.error(err); }});
}});

if (sessionStorage.getItem('painel_desbloqueado') === 'sim') {{
  document.body.classList.remove('bloqueado');
  carregarMotoristas();
  carregarAjustes();
}} else {{
  document.getElementById('formSenhaAdmin').addEventListener('submit', ev => {{
    ev.preventDefault();
    const erroEl = document.getElementById('bloqueioErroAdmin');
    if (!db) {{
      erroEl.textContent = 'Sem conexao. Confira sua internet e tente de novo.';
      return;
    }}
    const senhaDigitada = document.getElementById('campoSenhaAdmin').value;
    erroEl.textContent = 'Verificando...';
    db.collection('{firestore_colecao_motoristas}').doc('admin').get().then(doc => {{
      if (doc.exists && doc.data().senha === senhaDigitada) {{
        sessionStorage.setItem('painel_desbloqueado', 'sim');
        document.body.classList.remove('bloqueado');
        carregarMotoristas();
        carregarAjustes();
      }} else {{
        erroEl.textContent = 'Senha incorreta.';
      }}
    }}).catch(err => {{
      erroEl.textContent = 'Erro ao verificar a senha.';
      console.error(err);
    }});
  }});
}}
""" + _JS_FILTRO_DATA + _JS_FILTRO_MOTORISTA + _JS_MAPA_GERAL + """
</script>
</body>
</html>
"""


_ANALISTA_TEMPLATE = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="theme-color" content="#1f4e78">
<title>Painel do analista</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>{estilo}</style>
</head>
<body class="bloqueado">
<div class="bloqueio"><div class="bloqueio-caixa">
  <h2>Painel do analista</h2>
  <p>Digite a senha pra continuar.</p>
  <form id="formSenhaAnalista">
    <input type="password" id="campoSenhaAnalista" placeholder="Senha" autocomplete="current-password" required>
    <button type="submit">Entrar</button>
  </form>
  <div class="bloqueio-erro" id="bloqueioErroAnalista"></div>
</div></div>
<header>
  <h1>Painel do analista</h1>
  <div class="marca">{marca}</div>
</header>
""" + _HTML_FILTRO_DATA + _HTML_FILTRO_MOTORISTA + _HTML_MAPA_GERAL + """<main>
  <h2 class="secao-titulo">Ajustar rotas de hoje</h2>
  <p style="font-size:0.78rem; color:#666; margin: 0 12px 10px 12px;">
    Corrija motorista, veiculo, ordem de visita ou tire uma parada de uma rota
    de hoje sem esperar um novo arquivo do PathFind. O km mostrado ao
    motorista continua sendo o original; a pagina dele so' avisa que a rota
    foi ajustada manualmente.
  </p>
  <div id="listaAjustes"></div>
</main>
<footer><a href="index.html">Ver lista de rotas</a></footer>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore-compat.js"></script>
<script>
const NUMERO_MIN = {numero_min};
const NUMERO_MAX = {numero_max};
const DATA_ISO = '{data_atual_iso}';
const ROTAS_HOJE = {rotas_hoje_json};
const ORIGEM_GERAL = {origem_json};

let db = null;
try {{
  firebase.initializeApp({firebase_config_json});
  db = firebase.firestore();
}} catch (e) {{
  console.error(e);
}}

function aplicarFiltroMotorista(id) {{
  document.querySelectorAll('.ajuste-rota').forEach(card => {{
    card.style.display = (!id || card.dataset.rotaId === id) ? '' : 'none';
  }});
}}
function gruposParaMapa(idFiltro) {{
  return ROTAS_HOJE.filter(r => !idFiltro || r.id === idFiltro).map(r => ({{ rotulo: r.rotulo, paradas: r.paradas }}));
}}
popularFiltroMotorista(ROTAS_HOJE);

{js_ajuste_rotas}

if (sessionStorage.getItem('analista_desbloqueado') === 'sim') {{
  document.body.classList.remove('bloqueado');
  carregarAjustes();
}} else {{
  document.getElementById('formSenhaAnalista').addEventListener('submit', ev => {{
    ev.preventDefault();
    const erroEl = document.getElementById('bloqueioErroAnalista');
    if (!db) {{
      erroEl.textContent = 'Sem conexao. Confira sua internet e tente de novo.';
      return;
    }}
    const senhaDigitada = document.getElementById('campoSenhaAnalista').value;
    erroEl.textContent = 'Verificando...';
    db.collection('{firestore_colecao_motoristas}').doc('analista').get().then(doc => {{
      if (doc.exists && doc.data().senha === senhaDigitada) {{
        sessionStorage.setItem('analista_desbloqueado', 'sim');
        document.body.classList.remove('bloqueado');
        carregarAjustes();
      }} else {{
        erroEl.textContent = 'Senha incorreta.';
      }}
    }}).catch(err => {{
      erroEl.textContent = 'Erro ao verificar a senha.';
      console.error(err);
    }});
  }});
}}
""" + _JS_FILTRO_DATA + _JS_FILTRO_MOTORISTA + _JS_MAPA_GERAL + """
</script>
</body>
</html>
"""


def _rotas_hoje_json(resultados: dict[str, ResultadoRota]) -> list[dict]:
    """Estrutura das rotas do dia usada pelas telas de gestao (painel, admin
    e analista): id estavel da rota, rotulo, veiculo/motorista originais e a
    lista de paradas (seq/codigo/cliente/lat/lon) -- inclui coordenadas pra
    poder desenhar tanto o formulario de ajuste quanto o mapa combinado."""
    return [
        {
            "id": resultado.rota,
            "rotulo": rotulo_rota(resultado),
            "veiculo": veiculo_atual(resultado),
            "motorista_numero": numero_motorista(resultado),
            "paradas": [
                {
                    "seq": p.sequencia,
                    "codigo": p.entrega.codigo_cliente,
                    "cliente": p.entrega.cliente,
                    "lat": p.entrega.latitude,
                    "lon": p.entrega.longitude,
                }
                for p in resultado.ordem_otimizada
            ],
        }
        for _, resultado in sorted(resultados.items())
    ]


def gerar_admin(
    resultados: dict[str, ResultadoRota],
    docs_dir: str,
    data_iso: str,
    data_br: str,
    origem_lat: float,
    origem_lon: float,
    manifest_rel: str,
    historico_base_rel: str,
    hoje_rel: str,
) -> None:
    """Gera admin.html: pagina protegida pela senha administrativa, onde da
    pra ver/trocar a senha de cada motorista (720 a 731), gerar as senhas
    padrao de uma vez, e ajustar manualmente (motorista, veiculo, ordem de
    visita, exclusao de parada, mensagem) as rotas do dia. Escrita tanto na
    copia "de hoje" quanto arquivada, por isso recebe os mesmos parametros
    de caminho relativo que _gerar_paginas ja usa pro filtro de data."""
    html_admin = _ADMIN_TEMPLATE.format(
        estilo=_ESTILO,
        marca=MARCA,
        numero_min=NUMERO_MOTORISTA_MIN,
        numero_max=NUMERO_MOTORISTA_MAX,
        data_atual_iso=data_iso,
        data_atual_br=data_br,
        rotas_hoje_json=json.dumps(_rotas_hoje_json(resultados), ensure_ascii=False),
        origem_json=json.dumps({"lat": origem_lat, "lon": origem_lon}),
        firebase_config_json=json.dumps(FIREBASE_CONFIG),
        firestore_colecao_motoristas=FIRESTORE_COLECAO_MOTORISTAS,
        js_ajuste_rotas=_JS_AJUSTE_ROTAS_RENDERED,
        manifest_rel=manifest_rel,
        historico_base_rel=historico_base_rel,
        hoje_rel=hoje_rel,
        arquivo_destino="admin.html",
    )
    with open(os.path.join(docs_dir, "admin.html"), "w", encoding="utf-8") as f:
        f.write(html_admin)


def gerar_analista(
    resultados: dict[str, ResultadoRota],
    docs_dir: str,
    data_iso: str,
    data_br: str,
    origem_lat: float,
    origem_lon: float,
    manifest_rel: str,
    historico_base_rel: str,
    hoje_rel: str,
) -> None:
    """Gera analista.html: mesma tela de ajuste de rotas do admin.html
    (motorista, veiculo, ordem, exclusao, mensagem), protegida por uma senha
    separada (colecao de motoristas, documento "analista"), sem acesso a
    gestao de senhas. Escrita tanto na copia "de hoje" quanto arquivada,
    mesmo padrao de _gerar_paginas."""
    html_analista = _ANALISTA_TEMPLATE.format(
        estilo=_ESTILO,
        marca=MARCA,
        numero_min=NUMERO_MOTORISTA_MIN,
        numero_max=NUMERO_MOTORISTA_MAX,
        data_atual_iso=data_iso,
        data_atual_br=data_br,
        rotas_hoje_json=json.dumps(_rotas_hoje_json(resultados), ensure_ascii=False),
        origem_json=json.dumps({"lat": origem_lat, "lon": origem_lon}),
        firebase_config_json=json.dumps(FIREBASE_CONFIG),
        firestore_colecao_motoristas=FIRESTORE_COLECAO_MOTORISTAS,
        js_ajuste_rotas=_JS_AJUSTE_ROTAS_RENDERED,
        manifest_rel=manifest_rel,
        historico_base_rel=historico_base_rel,
        hoje_rel=hoje_rel,
        arquivo_destino="analista.html",
    )
    with open(os.path.join(docs_dir, "analista.html"), "w", encoding="utf-8") as f:
        f.write(html_analista)


def gerar_painel(
    resultados: dict[str, ResultadoRota],
    docs_dir: str,
    data_iso: str,
    data_br: str,
    gerado_em: str,
    origem_lat: float,
    origem_lon: float,
    manifest_rel: str,
    historico_base_rel: str,
    hoje_rel: str,
) -> None:
    """Gera painel.html: uma pagina so' pro supervisor, com o status de
    entrega de TODAS as rotas do dia, atualizando sozinha em tempo real
    (Firestore onSnapshot) conforme os motoristas forem marcando. Escrita
    tanto na copia "de hoje" (docs/) quanto arquivada
    (docs/historico/<data>/), por isso recebe os mesmos parametros de
    caminho relativo que _gerar_paginas ja usa pro filtro de data."""
    html_painel = _PAINEL_TEMPLATE.format(
        estilo=_ESTILO,
        marca=MARCA,
        data_atual_iso=data_iso,
        data_atual_br=data_br,
        rotas_json=json.dumps(_rotas_hoje_json(resultados), ensure_ascii=False),
        origem_json=json.dumps({"lat": origem_lat, "lon": origem_lon}),
        firebase_config_json=json.dumps(FIREBASE_CONFIG),
        firestore_colecao=FIRESTORE_COLECAO_STATUS,
        firestore_colecao_motoristas=FIRESTORE_COLECAO_MOTORISTAS,
        firestore_colecao_ajustes=FIRESTORE_COLECAO_AJUSTES,
        manifest_rel=manifest_rel,
        historico_base_rel=historico_base_rel,
        hoje_rel=hoje_rel,
        arquivo_destino="painel.html",
    )
    with open(os.path.join(docs_dir, "painel.html"), "w", encoding="utf-8") as f:
        f.write(html_painel)


_CODIGO_MOTORISTA_RE = re.compile(r"(\d+)")


def rotulo_rota(resultado: ResultadoRota) -> str:
    """Rotulo mostrado ao motorista: placa do veiculo + codigo do motorista
    (ex: 'TVA-5A26 - Motorista 729'), em vez do codigo interno da rota
    (RTxxxxxx), que nao significa nada pra quem esta dirigindo."""
    if not resultado.ordem_otimizada:
        return resultado.rota
    primeira = resultado.ordem_otimizada[0].entrega
    placa = primeira.veiculo or "Veiculo"
    codigo_m = _CODIGO_MOTORISTA_RE.search(primeira.motorista_nome or "")
    if codigo_m:
        return f"{placa} - Motorista {codigo_m.group(1)}"
    return f"{placa} - Sem motorista definido"


def numero_motorista(resultado: ResultadoRota) -> str | None:
    """Numero do motorista dessa rota (ex: '720'), usado pra saber qual
    senha checar. None se a rota nao tiver motorista definido -- nesse caso
    nao da pra proteger a pagina com senha (nao ha dono definido)."""
    if not resultado.ordem_otimizada:
        return None
    primeira = resultado.ordem_otimizada[0].entrega
    codigo_m = _CODIGO_MOTORISTA_RE.search(primeira.motorista_nome or "")
    return codigo_m.group(1) if codigo_m else None


def veiculo_atual(resultado: ResultadoRota) -> str | None:
    """Placa do veiculo original dessa rota (segundo o PathFind), usada como
    base pro ajuste manual poder mostrar/trocar a partir do valor atual."""
    if not resultado.ordem_otimizada:
        return None
    return resultado.ordem_otimizada[0].entrega.veiculo or None


def _slug(texto: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", texto).strip("-")
    return slug or "rota"


def _resultado_para_paradas(resultado: ResultadoRota) -> list[dict]:
    return [
        {
            "seq": p.sequencia,
            "codigo": p.entrega.codigo_cliente,
            "pedido": p.entrega.pedido_num,
            "cliente": p.entrega.cliente,
            "endereco": p.entrega.endereco,
            "lat": p.entrega.latitude,
            "lon": p.entrega.longitude,
            "dist_anterior_km": p.distancia_desde_anterior_km,
            "dist_proxima_km": p.distancia_ate_proxima_km,
        }
        for p in resultado.ordem_otimizada
    ]


def _gerar_paginas(
    resultados: dict[str, ResultadoRota],
    base_dir: str,
    origem_lat: float,
    origem_lon: float,
    gerado_em: str,
    titulo_pagina: str,
    manifest_rel: str,
    historico_base_rel: str,
    hoje_rel: str,
    data_atual_iso: str,
    data_atual_br: str,
    painel_rel: str | None = None,
) -> list[str]:
    """Escreve <base_dir>/index.html e <base_dir>/rotas/*.html. Usada tanto
    para a pagina 'de hoje' (docs/) quanto para a copia arquivada
    (docs/historico/<data>/)."""
    rotas_dir = os.path.join(base_dir, "rotas")
    os.makedirs(rotas_dir, exist_ok=True)

    # Limpa paginas de rotas de execucoes anteriores nesta mesma pasta: como
    # o nome do arquivo depende da placa/motorista (que pode mudar de rota
    # pra rota), sem isso paginas antigas ficariam "orfas" (publicadas, mas
    # sem link nenhum apontando pra elas).
    for nome in os.listdir(rotas_dir):
        if nome.endswith(".html"):
            os.remove(os.path.join(rotas_dir, nome))

    arquivos_gerados = []
    origem_json = json.dumps({"lat": origem_lat, "lon": origem_lon})

    itens_index = []
    slugs_usados: dict[str, int] = {}
    for rota in sorted(resultados):
        resultado = resultados[rota]
        rotulo = rotulo_rota(resultado)
        slug_base = _slug(rotulo)
        contagem = slugs_usados.get(slug_base, 0)
        slugs_usados[slug_base] = contagem + 1
        slug = slug_base if contagem == 0 else f"{slug_base}-{contagem + 1}"
        paradas = _resultado_para_paradas(resultado)

        aviso_linha_reta = (
            '<div class="aviso-linha-reta">Nao foi possivel calcular a distancia real de '
            'estrada agora (servico indisponivel); os valores abaixo sao em linha reta, '
            'menos precisos que o normal.</div>\n'
        ) if not resultado.usou_distancia_real else ""

        numero_mot = numero_motorista(resultado)
        veiculo_orig = veiculo_atual(resultado)
        # Sempre gera o bloco de senha, mesmo se a rota nao tiver motorista
        # definido no PathFind: um ajuste manual no admin pode atribuir um
        # motorista depois, e a decisao de mostrar ou nao o bloqueio passa a
        # ser feita no navegador (ver iniciarComAjuste no template).
        bloqueio_html = (
            '<div class="bloqueio"><div class="bloqueio-caixa">'
            '<h2>Rota protegida</h2>'
            '<p>Digite a senha do motorista pra ver essa rota.</p>'
            '<form id="formSenha">'
            '<input type="password" id="campoSenha" placeholder="Senha" autocomplete="current-password" required>'
            '<button type="submit">Entrar</button>'
            '</form>'
            '<div class="bloqueio-erro" id="bloqueioErro"></div>'
            '</div></div>\n'
        )

        html_rota = _ROTA_TEMPLATE.format(
            rotulo=rotulo,
            rotulo_json=json.dumps(rotulo, ensure_ascii=False),
            estilo=_ESTILO,
            marca=MARCA,
            gerado_em=gerado_em,
            aviso_linha_reta=aviso_linha_reta,
            dist_original=resultado.distancia_total_original_km,
            dist_otimizada=resultado.distancia_total_otimizada_km,
            economia_km=resultado.economia_km,
            economia_pct=resultado.economia_percentual,
            dist_origem_primeira=resultado.distancia_origem_primeira_km,
            dist_retorno=resultado.distancia_retorno_km,
            paradas_json=json.dumps(paradas, ensure_ascii=False),
            origem_json=origem_json,
            tracado_ida_json=json.dumps(resultado.tracado_ida),
            tracado_volta_json=json.dumps(resultado.tracado_volta),
            data_atual_iso=data_atual_iso,
            firebase_config_json=json.dumps(FIREBASE_CONFIG),
            firestore_colecao=FIRESTORE_COLECAO_STATUS,
            firestore_colecao_motoristas=FIRESTORE_COLECAO_MOTORISTAS,
            firestore_colecao_ajustes=FIRESTORE_COLECAO_AJUSTES,
            numero_motorista_json=json.dumps(numero_mot),
            rota_id_json=json.dumps(resultado.rota, ensure_ascii=False),
            veiculo_json=json.dumps(veiculo_orig, ensure_ascii=False),
            bloqueio_html=bloqueio_html,
        )
        caminho_rota = os.path.join(rotas_dir, f"{slug}.html")
        with open(caminho_rota, "w", encoding="utf-8") as f:
            f.write(html_rota)
        arquivos_gerados.append(os.path.join("rotas", f"{slug}.html"))

        itens_index.append(
            f'<li><a href="rotas/{slug}.html">{rotulo} '
            f'<small>{len(paradas)} paradas &middot; '
            f'{resultado.distancia_total_otimizada_km:.1f} km</small></a></li>'
        )

    link_painel_index = (
        '<a class="link-painel" href="painel.html">Ver painel do supervisor (todas as rotas)</a>'
        if painel_rel else ""
    )
    html_index = _INDEX_TEMPLATE.format(
        estilo=_ESTILO,
        marca=MARCA,
        gerado_em=gerado_em,
        titulo_pagina=titulo_pagina,
        itens="\n    ".join(itens_index),
        manifest_rel=manifest_rel,
        historico_base_rel=historico_base_rel,
        hoje_rel=hoje_rel,
        data_atual_iso=data_atual_iso,
        data_atual_br=data_atual_br,
        link_painel_index=link_painel_index,
        arquivo_destino="index.html",
    )
    caminho_index = os.path.join(base_dir, "index.html")
    with open(caminho_index, "w", encoding="utf-8") as f:
        f.write(html_index)
    arquivos_gerados.append("index.html")

    return arquivos_gerados


def _ler_manifest(historico_dir: str) -> list[dict]:
    caminho = os.path.join(historico_dir, "manifest.json")
    if not os.path.isfile(caminho):
        return []
    with open(caminho, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def _atualizar_manifest(historico_dir: str, data_iso: str, data_br: str, gerado_em: str, total_rotas: int) -> None:
    caminho = os.path.join(historico_dir, "manifest.json")
    dias = _ler_manifest(historico_dir)
    dias = [d for d in dias if d.get("data") != data_iso]
    dias.append({"data": data_iso, "data_br": data_br, "rotas": total_rotas, "gerado_em": gerado_em})
    dias.sort(key=lambda d: d["data"], reverse=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dias, f, ensure_ascii=False, indent=2)


def gerar_site(
    resultados: dict[str, ResultadoRota],
    docs_dir: str,
    origem_lat: float,
    origem_lon: float,
    data_referencia: date | None = None,
) -> list[str]:
    """Gera a pagina 'de hoje' (docs/index.html e docs/rotas/*.html -- o link
    fixo que os motoristas devem usar todo dia) e uma copia arquivada em
    docs/historico/<AAAA-MM-DD>/, alimentando o filtro de data da pagina
    inicial. Retorna a lista de arquivos gerados (paths relativos a
    docs_dir), util para o watcher decidir o que commitar.

    `data_referencia` e' a data REAL a que os dados se referem (normalmente
    extraida do nome do arquivo do PathFind pelo watcher, ex:
    "rastro_rotas_22-07-2026.txt" -> 22/07/2026) -- nao a data em que o
    script rodou. Isso importa quando dois arquivos de dias diferentes sao
    processados no mesmo dia (ex: um atrasado); sem essa distincao, os dois
    cairiam no mesmo dia do historico e um sobrescreveria o outro. Se nao
    for informada, usa a data de hoje (uso direto/testes).

    A pagina "de hoje" (docs/index.html, o link fixo que os motoristas usam)
    SO e' atualizada se `data_referencia` for a maior data ja vista ate agora
    (olhando o manifest do historico). Isso evita que, ao processar um
    arquivo atrasado/antigo depois de um mais recente, a pagina "de hoje"
    volte a mostrar dados velhos -- ela sempre reflete a data mais nova
    conhecida, nao "o ultimo arquivo processado"."""
    agora = datetime.now()
    gerado_em = agora.strftime("%d/%m/%Y %H:%M")
    data_ref = data_referencia or agora.date()
    data_iso = data_ref.strftime("%Y-%m-%d")
    data_br = data_ref.strftime("%d/%m/%Y")

    historico_dir = os.path.join(docs_dir, "historico")
    dia_dir = os.path.join(historico_dir, data_iso)
    os.makedirs(historico_dir, exist_ok=True)

    manifest_atual = _ler_manifest(historico_dir)
    maior_data_existente = max((d.get("data", "") for d in manifest_atual), default="")
    e_a_mais_recente = data_iso >= maior_data_existente

    arquivos_gerados = []

    # Pagina "de hoje": link fixo que nao muda de endereco dia a dia. So
    # atualiza se esta for a data mais recente conhecida (ver docstring).
    if e_a_mais_recente:
        arquivos_hoje = _gerar_paginas(
            resultados, docs_dir, origem_lat, origem_lon, gerado_em,
            titulo_pagina="Cargas otimizadas",
            manifest_rel="historico/manifest.json",
            historico_base_rel="historico/",
            hoje_rel="index.html",
            data_atual_iso=data_iso,
            data_atual_br=data_br,
            painel_rel="../painel.html",
        )
        arquivos_gerados.extend(arquivos_hoje)

        gerar_painel(
            resultados, docs_dir, data_iso, data_br, gerado_em, origem_lat, origem_lon,
            manifest_rel="historico/manifest.json",
            historico_base_rel="historico/",
            hoje_rel="painel.html",
        )
        arquivos_gerados.append("painel.html")

        gerar_admin(
            resultados, docs_dir, data_iso, data_br, origem_lat, origem_lon,
            manifest_rel="historico/manifest.json",
            historico_base_rel="historico/",
            hoje_rel="admin.html",
        )
        arquivos_gerados.append("admin.html")

        gerar_analista(
            resultados, docs_dir, data_iso, data_br, origem_lat, origem_lon,
            manifest_rel="historico/manifest.json",
            historico_base_rel="historico/",
            hoje_rel="analista.html",
        )
        arquivos_gerados.append("analista.html")

    # Copia arquivada do dia, para o filtro de data poder consultar depois.
    # painel/admin/analista tambem passam a existir por dia (nao so' "de
    # hoje"), pra dar pro supervisor/admin/analista olhar um dia anterior.
    arquivos_dia = _gerar_paginas(
        resultados, dia_dir, origem_lat, origem_lon, gerado_em,
        titulo_pagina=f"Rotas de {data_br}",
        manifest_rel="../manifest.json",
        historico_base_rel="../",
        hoje_rel="../../index.html",
        data_atual_iso=data_iso,
        data_atual_br=data_br,
    )
    arquivos_gerados.extend(os.path.join("historico", data_iso, a) for a in arquivos_dia)

    gerar_painel(
        resultados, dia_dir, data_iso, data_br, gerado_em, origem_lat, origem_lon,
        manifest_rel="../manifest.json",
        historico_base_rel="../",
        hoje_rel="../../painel.html",
    )
    arquivos_gerados.append(os.path.join("historico", data_iso, "painel.html"))

    gerar_admin(
        resultados, dia_dir, data_iso, data_br, origem_lat, origem_lon,
        manifest_rel="../manifest.json",
        historico_base_rel="../",
        hoje_rel="../../admin.html",
    )
    arquivos_gerados.append(os.path.join("historico", data_iso, "admin.html"))

    gerar_analista(
        resultados, dia_dir, data_iso, data_br, origem_lat, origem_lon,
        manifest_rel="../manifest.json",
        historico_base_rel="../",
        hoje_rel="../../analista.html",
    )
    arquivos_gerados.append(os.path.join("historico", data_iso, "analista.html"))

    _atualizar_manifest(historico_dir, data_iso, data_br, gerado_em, len(resultados))
    arquivos_gerados.append(os.path.join("historico", "manifest.json"))

    return arquivos_gerados
