import httpx

from searchaudit.config import Settings
from searchaudit.providers.base import ToolOutcome
from searchaudit.providers.keenable import (
    KeenableAPI,
    KeenableSearchSession,
    format_hits,
    parse_fetch,
    parse_search,
)
from .fakes import fixture, transport


def test_parse_search_snapshot():
    hits = parse_search(fixture("keenable_search_public.json"))
    assert len(hits) == 3
    assert hits[0]["url"] == "https://getmacha.com/blog/stack-ai-complete-guide"
    assert hits[0]["published_at"] == "2026-09-16T13:50:00Z"
    assert hits[2]["published_at"] == ""
    text = format_hits(hits)
    assert "1. Features, Pricing" in text and "URL: https://agenticindex.io/vendors/stackai" in text


def test_parse_fetch_snapshot():
    page = parse_fetch(fixture("keenable_fetch_public.json"))
    assert page["title"].startswith("Get Started with Workflow Builder")
    assert page["content"]


async def test_public_endpoints_without_key_and_private_with_key():
    calls: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=transport(keenable_calls=calls)) as http:
        await KeenableAPI(http, "").search("stackai")
        await KeenableAPI(http, "secret").fetch("https://example.com")
    public, private = calls
    assert public.url.path == "/v1/search/public"
    assert public.headers["X-Keenable-Title"] == "searchaudit"
    assert b'"max_results": 5' in public.content or b'"max_results":5' in public.content
    assert private.url.path == "/v1/fetch" and private.headers["X-API-Key"] == "secret"
    assert "live" not in private.url.params
    assert Settings().keenable_usd_per_request == 0.004


async def test_search_sends_query_time_only_when_asked():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=fixture("keenable_search_public.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = KeenableAPI(http, "secret", sleep=_no_sleep)
        await api.search("artemis accords")
        await api.search("artemis accords", query_time="2026-05-01")
    plain, pinned = calls
    assert b"query_time" not in plain.content
    assert b'"query_time":"2026-05-01"' in pinned.content or b'"query_time": "2026-05-01"' in pinned.content


async def test_search_retries_rate_limit_then_uses_the_ok_body():
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path.count("search"))
        if len(seen) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "slow down"})
        return httpx.Response(200, json=fixture("keenable_search_public.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        hits = await KeenableAPI(http, "secret", sleep=_no_sleep).search("stackai")
    assert len(seen) == 2 and hits[0]["url"].startswith("https://")


async def test_fetch_miss_is_retried_live():
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if request.url.params.get("live") != "true":
            return httpx.Response(404, json={"error": "not indexed"})
        return httpx.Response(200, json=fixture("keenable_fetch_public.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        page = await KeenableAPI(http, "secret", sleep=_no_sleep).fetch("https://pubmed.ncbi.nlm.nih.gov/1/")
    assert page["content"] and "live=true" in urls[1] and "live" not in urls[0]


async def _no_sleep(_: float) -> None:
    return None


class _SearchStub:
    def __init__(self) -> None:
        self.when = None

    async def search(self, query: str, query_time: str | None = None) -> ToolOutcome:
        self.when = query_time
        return ToolOutcome(text="ok", calls=[])


async def test_harness_query_time_overrides_the_model():
    stub = _SearchStub()
    session = KeenableSearchSession(stub)  # type: ignore[arg-type]
    session.bind_query_time("2026-05-01")
    await session.call_tool("search", {"query": "artemis", "query_time": "2026-01-01"})
    assert stub.when == "2026-05-01"


async def test_model_cannot_set_query_time():
    stub = _SearchStub()
    session = KeenableSearchSession(stub)  # type: ignore[arg-type]
    await session.call_tool("search", {"query": "artemis", "query_time": "2026-01-01"})
    assert stub.when is None
    schema = session.function_tools()[0]["function"]["parameters"]["properties"]
    assert "query_time" not in schema


def test_huge_titles_are_capped():
    from searchaudit.providers.keenable import TITLE_CHARS, format_hits, parse_search

    hits = parse_search({"results": [{"url": "https://x.gov/a.pdf", "title": "T" * 1_000_000, "snippet": "s"}]})
    assert len(hits[0]["title"]) == TITLE_CHARS
    assert len(format_hits(hits)) < 1000
