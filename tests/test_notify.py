import pytest

from clothesline import notify


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def test_send_retries_5xx_then_succeeds(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(json["text"])
        return FakeResponse(500 if len(calls) < 3 else 200)

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    monkeypatch.setattr(notify.time, "sleep", lambda seconds: None)
    notify.send("token", "chat", "mensagem")
    assert len(calls) == 3


def test_send_fails_fast_on_4xx(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(1)
        return FakeResponse(400, "chat not found")

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    monkeypatch.setattr(notify.time, "sleep", lambda seconds: None)
    with pytest.raises(notify.NotifyError):
        notify.send("token", "chat", "mensagem")
    assert len(calls) == 1


def test_send_requires_credentials():
    with pytest.raises(notify.NotifyError):
        notify.send("", "chat", "mensagem")

def test_logging_never_leaks_the_bot_token():
    from clothesline import cli

    cli.configure_logging()
    assert notify.logging.getLogger("httpx").level == notify.logging.WARNING
    assert notify.logging.getLogger("httpcore").level == notify.logging.WARNING
