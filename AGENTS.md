# AGENTS.md

## O projeto
3 APIs abertas → regras físicas (VPD/ET0) → relatório de varal no Telegram
(09–10h local) + alerta de chuva; tudo gravado no Neon.

## Comandos
    uv run watch --once --dry-run   # ingestão real, sem DB e sem Telegram
    uv run watch --once             # produção (aplica migrations sozinho)
    DATABASE_URL=<staging> uv run python scripts/sanity.py   # sanidade do staging

## Antes de dizer "pronto"
1. `uv run pytest && uv run watch --once --dry-run` — testes verdes e mensagem
   PT-BR com score, janela e a linha CPTEC vs modelos.

## Pegadinhas
- Cron do Actions é UTC: 09–10h local = 12–13h UTC; a chave `report:{dia}` é o
  que impede relatório duplicado quando o scheduler atrasa ou o run repete.
- MET Norway responde 403 sem o header `User-Agent` identificando o projeto.
- CPTEC/BrasilAPI devolve `clima[]` começando em D+1, então o dia atual fica
  sem trava CPTEC; é o comportamento esperado, não fonte fora do ar.
- `astral-sh/setup-uv` não publica tag móvel v10: pine a release (v10.4.0), ou o
  job morre em "Unable to resolve action".
- httpx em INFO loga a URL do `sendMessage`, que carrega o token do bot: por isso
  `configure_logging` rebaixa httpx/httpcore para WARNING (repo é público).
- Cron do Actions só dispara na branch default: watch-staging.yml mora na
  main e faz checkout de staging; editá-lo exige PR para main.

## Ações que exigem minha confirmação
- Qualquer merge na main (promoção staging→main ou hotfix), migration em
  banco de produção, `--force-report` no chat de produção.

## Fluxo staging → main
- Mudança comum: branch a partir de `staging`, PR para `staging`; o triage
  auto-mergeia low-mid com CI verde. Alto risco (arquivos em `.github/`,
  `.pem`, `.env`, nome contendo `secrets`, diff > 800 linhas) fica para o dono.
- staging roda horário contra Neon-staging e grupo Telegram de teste
  (watch-staging.yml, que mora na main). Nunca apontar staging para produção.
- Promoção: PR staging→main com saída de scripts/sanity.py + links dos runs
  de staging; o dono mergeia. Depois, PR de sync main→staging.
- Hotfix: PR direto para main (dono mergeia), depois sync staging.

## Git
- Branch `<agente>/<tipo>/<resumo>`; PR para staging (promoção/hotfix: main).
  Agente nunca mergeia main.