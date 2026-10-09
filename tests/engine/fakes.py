"""Fakes shared by the engine tests: a scripted OpenRouter and Keenable served from probe snapshots."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx

from searchaudit.config import Settings
from searchaudit.providers.base import ProviderContext
from searchaudit.providers.openrouter import OpenRouterClient

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def completion(content: str | None = "", tool_calls: list[dict] | None = None, *, cost: float | None = 0.001,
               model: str = "anthropic/claude-sonnet-5.5", annotations: list | None = None) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if annotations:
        message["annotations"] = annotations
    usage: dict = {"prompt_tokens": 100, "completion_tokens": 20}
    if cost is not None:
        usage["cost"] = cost
    return {"model": model, "choices": [{"message": message}], "usage": usage}


def tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeOpenRouter:
    """Answers /chat/completions from a script (a list or a function of the request body)."""

    def __init__(self, script: list[dict] | Callable[[dict], dict]) -> None:
        self.script = script
        self.requests: list[dict] = []
        self.decision = "search"

    def __call__(self, body: dict) -> dict:
        self.requests.append(body)
        if callable(self.script):
            return self.script(body)
        return self.script.pop(0)


MODELS = {"data": [
    {"id": "anthropic/claude-sonnet-5.5", "pricing": {"prompt": "0.000003", "completion": "0.000015"}},
    {"id": "typesafe/jev-router", "pricing": {"prompt": "0", "completion": "0"}},
]}


def transport(openrouter: FakeOpenRouter | None = None, keenable_calls: list | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "openrouter.ai" and path.endswith("/models"):
            return httpx.Response(200, json=MODELS)
        if host == "openrouter.ai" and path.endswith("/decisions") and openrouter is not None:
            return httpx.Response(200, json={
                "model": "typesafe/jev-1.13",
                "answers": {"tool": {"type": "choice", "choice": openrouter.decision, "confidence": 0.9,
                                     "probabilities": {"search": 0.1, "select": 0.9}}},
                "usage": {"input_tokens": 20, "output_tokens": 0, "cost": 0.00001},
            })
        if host == "openrouter.ai" and openrouter is not None:
            return httpx.Response(200, json=openrouter(json.loads(request.content)))
        if host == "api.keenable.ai":
            if keenable_calls is not None:
                keenable_calls.append(request)
            name = "keenable_search_public.json" if "/search" in path else "keenable_fetch_public.json"
            return httpx.Response(200, json=fixture(name))
        return httpx.Response(404, json={"error": "unexpected request"})

    return httpx.MockTransport(handler)


async def no_sleep(_: float) -> None:
    return None


def context(http: httpx.AsyncClient, settings: Settings | None = None, max_tool_calls: int = 15) -> ProviderContext:
    settings = settings or Settings(openrouter_api_key="test-key")
    client = OpenRouterClient(http, settings.openrouter_api_key, sleep=no_sleep)
    return ProviderContext(settings, http, client, "anthropic/claude-sonnet-5.5", "typesafe/jev-router",
                           max_tool_calls)
