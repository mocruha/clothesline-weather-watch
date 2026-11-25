"""Envio pelo Telegram: retry em 5xx/timeout, falha alta em 4xx."""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
MAX_TEXT = 4096
TIMEOUT_S = 20.0
RETRY_BACKOFF_S = (1.0, 3.0)


class NotifyError(RuntimeError):
    """Telegram recusou a mensagem; a chave de dedup NÃO deve ser gravada."""


def send(token: str, chat_id: str, text: str, *, parse_mode: str = "Markdown") -> None:
    if not token or not chat_id:
        raise NotifyError("TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID são obrigatórios")
    body = text[: MAX_TEXT - 3] + "..." if len(text) > MAX_TEXT else text
    attempts = len(RETRY_BACKOFF_S) + 1
    for attempt in range(attempts):
        retry = attempt < len(RETRY_BACKOFF_S)
        try:
            resp = httpx.post(
                TELEGRAM_URL.format(token=token),
                json={"chat_id": chat_id, "text": body, "parse_mode": parse_mode},
                timeout=TIMEOUT_S,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            problem: Exception = exc
        else:
            if resp.status_code < 400:
                return
            if resp.status_code < 500:
                raise NotifyError(
                    f"telegram HTTP {resp.status_code}: {resp.text[:300]}"
                )
            problem = NotifyError(f"telegram HTTP {resp.status_code}")
        if retry:
            log.warning("telegram: %s — retry em %ss", problem, RETRY_BACKOFF_S[attempt])
            time.sleep(RETRY_BACKOFF_S[attempt])
        else:
            raise NotifyError(f"telegram: falhou após {attempts} tentativas ({problem})")


def _redact(text: str) -> str:
    return text.replace(_NEVERIZE, "...")