# AGENTS.md

## O projeto
3 APIs abertas de clima → regras físicas (VPD/ET0) → relatório diário de varal
no Telegram (09–10h local) + alerta de chuva; tudo gravado no Neon.

## Comandos
    uv sync
    uv run watch --once --dry-run   # ingest real, sem DB e sem Telegram
    uv run watch --once             # produção
    uv run python -m clothesline.db migrate

## Antes de dizer "pronto"
1. `uv run pytest && uv run watch --once --dry-run` — testes verdes e mensagem
   PT-BR com score, janela e a linha CPTEC vs modelos.

## Pegadinhas
- Cron do Actions é UTC: 09–10h local = 12–13h UTC. A chave `report:{dia}` é o
  que impede relatório duplicado quando o scheduler atrasa ou o run repete.
- MET Norway responde 403 sem o header `User-Agent` identificando o projeto.
<!-- TODO: colher na fase 4 -->

## Ações que exigem minha confirmação
- Migration em banco de produção, mensagem real no Telegram (`--force-report`),
  qualquer merge.

## Git
- Branch `<agente>/<tipo>/<resumo>`; PR para a main, sem merge.