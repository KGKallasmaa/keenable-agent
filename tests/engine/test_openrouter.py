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
        with pytest.raises(OpenRouterError, match="503") as raised:
            await OpenRouterClient(http, "k", sleep=no_sleep).chat({"model": "m", "messages": []})
    assert len(calls) == 3
    assert raised.value.body == "down"


async def test_client_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {"message": "bad model"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(OpenRouterError, match="bad model") as raised:
            await OpenRouterClient(http, "k", sleep=no_sleep).chat({"model": "m", "messages": []})
    assert len(calls) == 1
    assert raised.value.body["error"]["message"] == "bad model"


# --- decide() / _parse_decision error reporting --------------------------------------------
#
# decide() only routes non-retryable statuses (not in {429,500,502,503,504}) through
# _parse_decision. When the body's `error` field is absent/None or a dict without a
# `message` key, the error string must fall back to the raw response body text rather
# than interpolating the Python literal `None` (regression of the "HTTP 422: None" bug).


def _decide_client(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenRouterClient(http, "k", retries=0, sleep=no_sleep)


@pytest.mark.parametrize(
    ("status", "text", "expected_message", "expected_body"),
    [
        # 422 with empty JSON object body: error={} -> message=None -> must fall back to text
        pytest.param(422, "{}", "HTTP 422: {}", {}, id="4xx-empty-object-body"),
        # 400 whose error dict carries a code but no message key (the common envelope shape)
        pytest.param(400, '{"error": {"code": "bad_request"}}',
                     "HTTP 400: {\"error\": {\"code\": \"bad_request\"}}",
                     {"error": {"code": "bad_request"}}, id="4xx-error-dict-without-message"),
        # 200 (success) but body missing the `answers` key -> triggers the error branch.
        # Must NOT emit "HTTP 200: None"; must carry the raw body text.
        pytest.param(200, '{"meta": "ok"}', "HTTP 200: {\"meta\": \"ok\"}",
                     {"meta": "ok"}, id="200-missing-answers"),
    ],
)
async def test_decide_error_falls_back_to_response_text(status, text, expected_message, expected_body):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/api/alpha/decisions"
        return httpx.Response(status, text=text)

    client = _decide_client(handler)
    try:
        with pytest.raises(OpenRouterError) as exc_info:
            await client.decide("typesafe/jev-router", {}, {})
    finally:
        await client.http.aclose()
    # The message must never interpolate the literal `None` when no error.message is present.
    assert ": None" not in str(exc_info.value)
    assert str(exc_info.value) == expected_message
    assert exc_info.value.body == expected_body
    # Non-retryable statuses must reach _parse_decision exactly once (no retries).
    assert len(calls) == 1


async def test_decide_error_dict_with_message_uses_message():
    def handler(request):
        assert request.url.path == "/api/alpha/decisions"
        return httpx.Response(422, json={"error": {"message": "Invalid model"}})

    client = _decide_client(handler)
    try:
        with pytest.raises(OpenRouterError, match="Invalid model") as raised:
            await client.decide("typesafe/jev-router", {}, {})
    finally:
        await client.http.aclose()
    assert str(raised.value) == "HTTP 422: Invalid model"
    assert raised.value.body == {"error": {"message": "Invalid model"}}
