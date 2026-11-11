/**
 * Despertador do RotaCerta (Google Apps Script).
 *
 * O agendamento do GitHub ("schedule") perde ou atrasa em horas a maioria
 * das execucoes. Este script roda nos servidores do Google a cada 5 minutos,
 * olha a pasta "UP ROTAS ENTREGA" e, quando algum rastro_rotas*.txt foi
 * criado ou alterado, manda o GitHub rodar o workflow na hora
 * (workflow_dispatch). Sem arquivo novo, ele nao faz nada.
 *
 * O token do GitHub NAO fica neste arquivo: ele vai em
 * Configuracoes do projeto > Propriedades do script > GITHUB_TOKEN.
 */
const PASTA_ID = '1n7UT7SJrSxUCyaIIlcgQcngSkq6xGpzj';
const REPO = 'alvarodyego/rotacerta-googledrive';
const WORKFLOW = 'publicar.yml';
const HORA_INICIO = 5;  // so confere das 5h...
const HORA_FIM = 23;    // ...ate 23h59 (horario de Araguaina)

function conferirPasta() {
  const hora = Number(Utilities.formatDate(new Date(), 'America/Araguaina', 'H'));
  if (hora < HORA_INICIO || hora > HORA_FIM) return;

  const props = PropertiesService.getScriptProperties();
  const ultima = Number(props.getProperty('ULTIMA_MODIFICACAO') || 0);
  let maisNova = ultima;
  const arquivos = DriveApp.getFolderById(PASTA_ID).getFiles();
  while (arquivos.hasNext()) {
    const arquivo = arquivos.next();
    if (!/^rastro_rotas.*\.txt$/i.test(arquivo.getName())) continue;
    maisNova = Math.max(maisNova, arquivo.getLastUpdated().getTime());
  }
  if (maisNova <= ultima) return;  // nada novo

  const token = props.getProperty('GITHUB_TOKEN');
  if (!token) throw new Error('Falta a propriedade GITHUB_TOKEN nas configuracoes do projeto.');
  const resposta = UrlFetchApp.fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    {
      method: 'post',
      contentType: 'application/json',
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
      payload: JSON.stringify({ ref: 'main' }),
      muteHttpExceptions: true,
    });
  if (resposta.getResponseCode() === 204) {
    props.setProperty('ULTIMA_MODIFICACAO', String(maisNova));
    console.log('Arquivo novo na pasta: GitHub acionado.');
  } else {
    // Nao grava a marca: tenta de novo em 5 minutos.
    console.error('GitHub recusou: ' + resposta.getResponseCode() + ' ' + resposta.getContentText());
  }
}

/** Rode UMA vez, a mao, para ligar o despertador de 5 em 5 minutos. */
function instalarGatilho() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('conferirPasta').timeBased().everyMinutes(5).create();
  conferirPasta();
}
