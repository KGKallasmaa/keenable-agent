import time

import httpx

from app.cost import EXA_SEARCH_USD, KEENABLE_REQUEST_USD, tokens_from_text

EXA_URL = "https://api.exa.ai/search"
KEENABLE_SEARCH = "https://api.keenable.ai/v1/search"
KEENABLE_SEARCH_PUBLIC = "https://api.keenable.ai/v1/search/public"
KEENABLE_FETCH = "https://api.keenable.ai/v1/fetch"
KEENABLE_FETCH_PUBLIC = "https://api.keenable.ai/v1/fetch/public"
APP_TITLE = "keenable-agent"
TIMEOUT = 25.0


def search_exa(query: str, api_key: str) -> dict:
    started = time.perf_counter()
    if not api_key:
        return _fail("EXA_API_KEY is not set", started, "exa")
    try:
        response = httpx.post(
            EXA_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"query": query, "numResults": 5, "contents": {"highlights": True}},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        return _fail(str(exc), started, "exa")
    texts = []
    urls = []
    for item in body.get("results") or []:
        if item.get("url"):
            urls.append(item["url"])
        highlights = item.get("highlights") or []
        if isinstance(highlights, list):
            texts.extend(str(part) for part in highlights)
        elif item.get("text"):
            texts.append(str(item["text"]))
    text = "\n".join(texts).strip()
    reported = ((body.get("costDollars") or {}).get("total"))
    search_cost = float(reported) if reported is not None else EXA_SEARCH_USD
    priced = "exa costDollars.total" if reported is not None else "exa list price, response had no costDollars"
    return _ok(urls, text, search_cost, started, priced)


def search_keenable(query: str, api_key: str, usd_per_request: float) -> dict:
    """Search, then fetch the top URL with an extract instruction.

    SELECT's SQL tool is MCP-only and is not called here. This arm is the
    billable substitute: one search plus one fetch.
    """
    started = time.perf_counter()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
        search_url = KEENABLE_SEARCH
        fetch_url = KEENABLE_FETCH
        priced = "agent builder list price"
    else:
        headers["X-Keenable-Title"] = APP_TITLE
        search_url = KEENABLE_SEARCH_PUBLIC
        fetch_url = KEENABLE_FETCH_PUBLIC
        priced = "public endpoint, priced at agent builder list price"
    try:
        response = httpx.post(
            search_url,
            headers=headers,
            json={"query": query, "limit": 5},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        return _fail(str(exc), started, priced)
    results = _keenable_results(body)
    urls = [item["url"] for item in results if item.get("url")]
    snippets = [item["text"] for item in results if item.get("text")]
    requests_used = 1
    text = "\n".join(snippets).strip()
    if urls:
        fetched = _fetch_keenable(fetch_url, headers, urls[0], query)
        requests_used += 1
        if fetched:
            text = fetched
    return _ok(urls, text, usd_per_request * requests_used, started, priced)


def _fetch_keenable(url: str, headers: dict, page_url: str, query: str) -> str:
    try:
        response = httpx.get(
            url,
            headers=headers,
            params={
                "url": page_url,
                "live": "true",
                "max_characters": "4000",
                "extract": f"Answer this in one or two sentences and keep the fact: {query}",
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        content = response.json().get("content") or ""
        return str(content).strip()
    except (httpx.HTTPError, ValueError):
        return ""


def _keenable_results(body: dict) -> list[dict]:
    raw = body.get("results")
    if raw is None and isinstance(body.get("data"), dict):
        raw = body["data"].get("results") or body["data"].get("search")
    if raw is None:
        raw = body.get("data") if isinstance(body.get("data"), list) else []
    parsed = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        parsed.append(
            {
                "url": item.get("url") or item.get("link") or "",
                "text": item.get("snippet") or item.get("description") or item.get("content") or "",
            }
        )
    return parsed


def _ok(urls: list[str], text: str, search_cost: float, started: float, priced_as: str) -> dict:
    return {
        "ok": True,
        "urls": urls[:5],
        "text": text[:4000],
        "search_cost": round(search_cost, 6),
        "context_tokens": tokens_from_text(text),
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "error": None,
        "priced_as": priced_as,
    }


def _fail(message: str, started: float, priced_as: str) -> dict:
    return {
        "ok": False,
        "urls": [],
        "text": "",
        "search_cost": 0.0,
        "context_tokens": 0,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "error": message,
        "priced_as": priced_as,
    }
