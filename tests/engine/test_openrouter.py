import httpx
import pytest

from searchaudit.providers.openrouter import OpenRouterClient, OpenRouterError

from .fakes import completion, no_sleep


async def test_retries_429_and_5xx_then_succeeds():
    statuses = [429, 502]

    def handler(request):
        if statuses:
            return httpx.Response(statuses.pop(0), text="busy")
        return httpx.Response(200, json=completion("ok"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        body = await OpenRouterClient(http, "k", sleep=no_sleep).chat({"model": "m", "messages": []})
    assert body["choices"][0]["message"]["content"] == "ok"


async def test_gives_up_after_two_retries():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, text="down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(OpenRouterError, match="503"):
            await OpenRouterClient(http, "k", sleep=no_sleep).chat({"model": "m", "messages": []})
    assert len(calls) == 3


async def test_client_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {"message": "bad model"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(OpenRouterError, match="bad model"):
            await OpenRouterClient(http, "k", sleep=no_sleep).chat({"model": "m", "messages": []})
    assert len(calls) == 1
