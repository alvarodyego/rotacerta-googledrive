# RotaCerta (versão nuvem, via Google Drive)

Versão paralela e independente do sistema que roda em `og-rotas-motoristas/`.
A diferença: em vez de vigiar uma pasta local sincronizada (que exige o
computador ligado), esta versão consulta a API do Google Drive diretamente
de dentro do **GitHub Actions**, agendado por cron — funciona mesmo com
nenhum computador seu ligado.

**Este projeto não interfere em nada do sistema já publicado.** É um
repositório e um site GitHub Pages totalmente separados.

## Como funciona

```
GitHub Actions (roda na nuvem, a cada 15 min, de graça)
   -> autentica no Google Drive com a conta de servico
   -> lista os arquivos da pasta do Google Drive configurada
   -> se achar um arquivo novo (ou alterado), baixa o conteudo
   -> roda parsing.py + route_optimizer.py + site_generator.py
   -> comita e publica em docs/ (git commit + push, usando o token do Actions)
   -> GitHub Pages atualiza o site sozinho
```

## Configuração — passo a passo

### 1. Conta de serviço no Google Cloud (já feito)

1. Projeto criado no [console.cloud.google.com](https://console.cloud.google.com): `rotacertaog`.
2. Google Drive API ativada nesse projeto.
3. Conta de serviço criada: `rotacerta-drive-bot@rotacertaog.iam.gserviceaccount.com`.
4. Chave JSON baixada (guardada localmente, **nunca** commitada neste repositório).
5. Pasta do Google Drive (`UP ROTAS ENTREGA`) compartilhada com o e-mail da
   conta de serviço, como **Leitor**.
6. ID da pasta: `1n7UT7SJrSxUCyaIIlcgQcngSkq6xGpzj`.

Se um dia precisar recriar a chave (perdeu o arquivo, por exemplo): Google
Cloud Console → IAM e admin → Contas de serviço → clique na conta →
aba "Chaves" → "Adicionar chave" → "Criar nova chave" → JSON.

### 2. Criar o repositório no GitHub e ativar o GitHub Pages

Mesmo processo do `og-rotas-motoristas/` original:
1. Criar um repositório novo, público, sem README.
2. `git init && git add . && git commit -m "Primeira versao" && git branch -M main`
3. `git remote add origin <URL do repositorio> && git push -u origin main`
4. Settings → Pages → Deploy from a branch → main → `/docs` → Save.

### 3. Configurar Secrets e Variables no GitHub

No repositório → **Settings** → **Secrets and variables** → **Actions**:

**Secrets** (dados sensíveis, nunca aparecem nos logs):
- `GOOGLE_SERVICE_ACCOUNT_JSON` = conteúdo **inteiro** do arquivo `.json`
  da chave da conta de serviço (abra o arquivo num editor de texto, copie
  tudo, cole aqui).

**Variables** (não sensíveis, aparecem nos logs — aba "Variables" ao lado):
- `GOOGLE_DRIVE_FOLDER_ID` = `1n7UT7SJrSxUCyaIIlcgQcngSkq6xGpzj`
- `ORIGEM_LAT` = `-7.22722092594843`
- `ORIGEM_LON` = `-48.24978544427654`

### 4. Pronto

O workflow (`.github/workflows/publicar.yml`) já está configurado pra
rodar a cada 15 minutos automaticamente, e também pode ser disparado na
mão em **Actions** → **Publicar rotas a partir do Google Drive** → **Run
workflow**.

## Arquivos

- `parsing.py` — mesmo parser do projeto original, com uma função extra
  (`parse_conteudo`) que aceita o texto já em memória (sem precisar de um
  caminho de arquivo local), já que o conteúdo vem direto da API.
- `route_optimizer.py`, `site_generator.py` — cópia do projeto original
  (mesma versão usada em `og-rotas-motoristas/`).
- `google_drive_client.py` — cliente simples do Google Drive API
  (autenticação via conta de serviço, listagem de pasta, download de
  arquivo).
- `googledrive_watch_and_publish.py` — script principal, equivalente ao
  `watch_and_publish.py` original, mas puxando da API em vez de uma pasta
  local. Guarda o estado do que já foi processado em `.drive_state.json`
  (precisa ficar versionado no git, já que o GitHub Actions não mantém
  nada entre execuções a não ser o que está no repositório).
- `.github/workflows/publicar.yml` — o agendamento (cron) que roda tudo.

## Manutenção

Se `site_generator.py`/`parsing.py`/`route_optimizer.py` forem
atualizados no projeto original (`og-rotas-motoristas/`), copie as
mesmas mudanças pra cá pra manter as duas versões em sincronia — os dois
projetos não compartilham código automaticamente.
