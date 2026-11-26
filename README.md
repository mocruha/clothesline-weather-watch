# clothesline-weather-watch

Relatório diário no Telegram dizendo se vale pendurar roupa no varal em
Osasco/SP, com janelas horárias e alerta de chuva. Cron horário no GitHub
Actions; dados de Open-Meteo, MET Norway e CPTEC/INPE (via BrasilAPI) no Neon.

Fronteiras do projeto (escopo, não-escopo, contrato de dados e decisões):
[`SPEC.md`](SPEC.md). Regras de trabalho para agentes: [`AGENTS.md`](AGENTS.md).

## Uso local

```sh
uv sync
uv run pytest
uv run watch --once --dry-run   # ingestão real, imprime a mensagem, sem DB/Telegram
uv run watch --once             # exige DATABASE_URL + Telegram
uv run watch --once --force-report   # envia os relatórios (manhã e tarde) fora da janela
```

O horário dos relatórios segue o relógio de São Paulo: o da manhã sai no
primeiro run entre 09:00 e 10:59 e o da tarde entre 16:00 e 17:59, um de
cada por dia. Os dois trazem o bloco "Amanhã" com o veredito de D+1.

`config.toml` guarda local, cityCode do CPTEC e todos os limiares (VPD, chuva,
rajada, água retida). Mudar de cidade é editar esse arquivo; o cityCode sai de
`https://brasilapi.com.br/api/cptec/v1/cidade/{nome}`.

## Setup único (uma vez)

1. **Banco (Neon)**: crie o projeto, copie a connection string (`postgresql://…`).
   O schema é aplicado sozinho no primeiro run (`apply_migrations`), ou à mão:

   ```sh
   DATABASE_URL='postgresql://…' uv run python -m clothesline.db migrate
   ```

2. **Bot do Telegram**: fale com [@BotFather](https://t.me/BotFather), `/newbot`,
   guarde o token. Mande uma mensagem para o bot e descubra o `chat_id`:

   ```sh
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | jq '.result[0].message.chat.id'
   ```

3. **Secrets do repositório** (Settings → Secrets and variables → Actions):

   | Secret | Valor |
   | --- | --- |
   | `DATABASE_URL` | connection string do Neon |
   | `TELEGRAM_BOT_TOKEN` | token do BotFather |
   | `TELEGRAM_CHAT_ID` | id do chat que recebe o relatório |

4. **Primeiro run**: Actions → `watch` → *Run workflow* (o cron leva até uma
   hora para começar a disparar). `--force-report` é o caminho manual quando
   você quiser ver a mensagem agora.

O cron é UTC e o GitHub atrasa runs em 5–30 min; as chaves `report:{dia}` e
`report-evening:{dia}` na tabela `notifications` garantem um relatório de cada
por dia mesmo com atraso ou re-run.

## Dados

`forecast_hourly` (fonte × hora × fetch, 90 dias de retenção),
`forecast_daily_cptec`, `hourly_score` (features em jsonb), `day_verdict`
(inclui `owner_feedback`, ainda não usado) e `notifications` (dedup de envio).
Feedback do dono — `sequei` / `molhou` / `nao_lavei` — é o insumo para
recalibrar os limiares ou treinar um modelo depois; o v1 é só regra física.