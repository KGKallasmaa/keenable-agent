import httpx

from searchaudit.config import Settings
from searchaudit.providers.keenable import KeenableAPI, format_hits, parse_fetch, parse_search

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
    assert Settings().keenable_usd_per_request == 0.004
