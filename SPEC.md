# SPEC.md — clothesline-weather-watch

## Resultado

Todo dia entre 09h e 10h e de novo entre 16h e 17h (America/Sao_Paulo) o dono
recebe no Telegram um veredito de varal para Osasco/SP — "bom dia de varal" /
"dá, com janela curta" / "melhor não" — com score, janelas horárias ("pendure
até HH, seca ~HH"), aviso noturno e o bloco "Amanhã" com o veredito de D+1. Se
chover nas próximas 3 h com roupa no varal, chega um alerta pedindo para
recolher. Tudo vem de 3 APIs abertas e é gravado no Neon.

## Escopo

- Ingestão horária de Open-Meteo (série horária), MET Norway (consenso horário e
  chuva) e CPTEC/INPE via BrasilAPI (condição diária BR).
- Classificador por regras físicas (VPD, ET0 Penman-Monteith, rajada) → score,
  veredito e janelas de varal configuradas em `config.toml`.
- Relatórios diários idempotentes (manhã 09–10h e tarde 16–17h, um de cada por
  dia) e alerta de chuva idempotente por evento; re-execução nunca duplica
  mensagem.
- Persistência das horas cruas, do score por hora (com features em jsonb) e do
  veredito do dia para calibração futura.

## Não-escopo

- Sem ML no v1: nenhum modelo é treinado; `hourly_score.features` e
  `day_verdict.owner_feedback` guardam dados para treinar depois, offline.
- Sem INMET (API observacional exige token e não dá previsão horária).
- Sem dashboard, web app, gráfico ou histórico consultável.
- Sem multi-cidade: um local, fixo em `config.toml`.
- Sem comandos de bot, respostas interativas ou notificação push além do
  `sendMessage`.
- Sem grade horária própria: cadência é o cron do GitHub Actions (UTC, com
  atraso tolerado pelo dedup).

## Entradas e saídas

| | O quê | Formato / destino |
| --- | --- | --- |
| Entrada | Open-Meteo forecast horário (T, RH, ponto de orvalho, prob/quantidade chuva, vento, rajada, UV, radiação, nuvem, VPD, ET0, dia/noite) | `GET https://api.open-meteo.com/v1/forecast` JSON, sem chave |
| Entrada | MET Norway locationforecast 2.0 compact (T, RH, vento m/s, nuvem, chuva 1h, symbol_code) | `GET https://api.met.no/weatherapi/locationforecast/2.0/compact` JSON, header `User-Agent` obrigatório |
| Entrada | CPTEC/INPE via BrasilAPI (condição e min/max por dia) | `GET https://brasilapi.com.br/api/cptec/v1/clima/previsao/3656/4` JSON |
| Saída | Mensagem de relatório / alerta de chuva em PT-BR | Telegram `sendMessage` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) |
| Saída | Séries horárias, scores, vereditos, notificações enviadas | Neon Postgres, schema `public` |

## Contrato central

- `forecast_hourly`: grão = **fonte × hora-alvo (UTC) × fetched_at**. Append-only
  por fetch; retenção 90 dias.
- `forecast_daily_cptec`: grão = **city_code × dia × fetched_at**.
- `hourly_score`: grão = **hora** (última computação vence).
- `day_verdict`: grão = **dia**.
- `notifications.key`: grão = evento de mensagem — `report:{YYYY-MM-DD}`,
  `report-evening:{YYYY-MM-DD}` ou `rain:{YYYY-MM-DD}T{HH}` (hora local do
  início da chuva). Chave só é gravada **depois** de o Telegram aceitar; falha
  de envio permite reenvio no run seguinte.
- Fonte indisponível não derruba o run: warning e segue com as outras. Run só
  aborta se **nenhuma** fonte horária responder.
- Campo numérico fisicamente impossível (RH > 100, T < −20 ou > 55, vento < 0)
  é descartado com warning e vira `NULL`, nunca zero; hora sem temperatura é
  descartada inteira.

## Comando "pronto"

```sh
uv run pytest && uv run watch --once --dry-run
# esperado: testes verdes; dois relatórios PT-BR impressos (manhã e fim de
# tarde) com score, min/max, janelas, bloco "Amanhã" e a linha de concordância
# entre CPTEC e os modelos horários, calculada das 3 APIs ao vivo (sem
# credenciais, sem tocar DB nem Telegram).
```

## Fora do repositório

- `DATABASE_URL` (Neon), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — só em env ou
  GitHub Secrets; nenhum `.env` versionado.
- Banco de produção no Neon; `migrations/001_init.sql` é a fonte do schema.
- Chave privada do GitHub App em `~/.config/github-app/` (fluxo de PR).

## Decisões já tomadas

| Decisão | Por quê | Descartado |
| --- | --- | --- |
| Regras físicas (VPD + ET0) em vez de ML | Sem ground truth de "roupa secou", ML treinaria contra a própria heurística | Modelo supervisionado no v1 |
| Open-Meteo como motor horário | Já entrega VPD e ET0 Penman-Monteith prontos, sem chave | Calcular ET0 à mão de GHI/temperatura |
| MET Norway no consenso de chuva | Segunda opinião horária independente + nowcast | Só Open-Meteo (contra o requisito de ≥3 fontes) |
| CPTEC via BrasilAPI, não XML do INPE | JSON UTF-8 estável; XML direto deu encoding errado e endpoints com erro nos probes | `servicos.cptec.inpe.br` XML |
| Postgres no Neon + psycopg3 | Idempotência por chave precisa de storage transacional | SQLite local, arquivo JSON |
| Cron horário no GitHub Actions (UTC) | Infra grátis, sem servidor; atraso absorvido pelo dedup | VPS com systemd timer |

## Riscos conhecidos

- Cron do Actions atrasa 5–30 min (às vezes mais): cada relatório sai no primeiro
  run dentro da sua janela (09:00–10:59 e 16:00–17:59) e não repete depois. Run
  que perde a janela não tem catch-up — mesmo tradeoff nas duas.
- Open-Meteo e MET mudam nomes de campo sem aviso: parse tolerante a ausência,
  mas campo obrigatório ausente vira fonte vazia + warning, não zero.
- CPTEC retorna só condição diária (sigla): a trava é grosseira e pode rebaixar
  um dia bom; o relatório mostra a discordância em vez de esconder.
- Limiares do modelo são chute inicial calibrado a `config.toml`; feedback do
  dono entra em `day_verdict.owner_feedback`.