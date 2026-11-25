# AGENTS.md

## O projeto
3 APIs abertas → regras físicas (VPD/ET0) → relatório de varal no Telegram
(09–10h local) + alerta de chuva; tudo gravado no Neon.

## Comandos
    uv run watch --once --dry-run   # ingestão real, sem DB e sem Telegram
    uv run watch --once             # produção
    uv run python -m clothesline.db migrate

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

## Ações que exigem minha confirmação
- Migration em banco de produção, `--force-report` (mensagem real no Telegram),
  qualquer merge.

## Git
- Branch `<agente>/<tipo>/<resumo>`; PR para a main, sem merge.