# Search Audit

Search Audit runs one company's real prompts through several web-search providers, with **the same
pinned agent model, system prompt and budget** for each. A judge model grades every answer against a
checked reference answer. The result is a neutral report that ranks the providers on accuracy, cost
and speed.

The first audit is **StackAI**, an enterprise AI-agent platform that uses Exa today. It has 60 prompts
across five types: lookup, freshness, multi-hop, list-building and regulation.

| Provider | id | How it is called |
|---|---|---|
| Exa | `exa` | OpenRouter server tool `openrouter:web_search` (engine=exa) |
| Parallel | `parallel` | OpenRouter server tool (engine=parallel) |
| Keenable search | `keen` | Keenable REST search and fetch as function tools in our own agent loop |
| Keenable SELECT | `select` | Keenable SELECT MCP. The agent writes one SQL query per call |
| Keenable routed | `routed` | A router model picks SELECT or Keenable search for each call |

The design is in `docs/designs/search-audit.md`, and the approved report UI is in
`docs/prototype/index.html`.

## Layout

```
searchaudit/
  models.py        shared contract: AuditResults (meta, providers, prompts, runs, judgments)
  cli.py           probe / run / judge / assemble
  agent.py         the agent loop, concurrency limits, cost cap
  judge.py         judge prompt, JSON schema, rule checks
  notes.py         methodology caveats written into meta.notes
  providers/       exa, parallel (server tools), keen, select, routed
  stats.py         Wilson intervals, McNemar + Holm, bootstrap, scoring
  report/          view model for the report
  web/             FastAPI app, templates, static assets
audits/<slug>/     audit.yaml, prompts.json, then runs.jsonl, judgments.jsonl, results.json, report.pdf
scripts/           fake_results.py (synthetic fixture), make_pdf.py (PDF of the print view)
tests/             engine/ and report/, all offline with fakes
```

## Setup

```
uv sync
cp .env.example .env            # then fill in OPENROUTER_API_KEY and KEENABLE_API_KEY
uv run playwright install chromium   # only needed for make_pdf.py
```

## Running an audit

Every step reads `audits/<slug>/audit.yaml` and writes its output into the same directory. Each step
can be resumed: `run` skips (prompt, provider) pairs that are already in `runs.jsonl`, and `judge`
skips runs it has already judged.

```
A=audits/stackai/audit.yaml

# 1. Probe: one snapshot per provider of its tool list and response shape -> audits/stackai/probes/
uv run python -m searchaudit probe $A

# 2. Smoke test: first 2 prompts, every provider (costs well under $1)
uv run python -m searchaudit run $A --limit 2

# 3. Full run. Pins openrouter/auto to one model in pinned.json; stops launching runs at cost_cap_usd.
uv run python -m searchaudit run $A
uv run python -m searchaudit run $A --retry-errors        # re-run pairs that errored
uv run python -m searchaudit run $A --providers exa,keen  # a subset of providers

# 4. Judge every run that has not been judged yet -> judgments.jsonl
uv run python -m searchaudit judge $A

# 5. Assemble results.json, the file the report reads
uv run python -m searchaudit assemble $A

# 6. Render the PDF (starts the app itself) -> audits/stackai/report.pdf
uv run python scripts/make_pdf.py stackai
```

The audit's directory name is its URL slug, so `audits/stackai/` is served at `/r/stackai`.

Settings live in `audit.yaml`: agent model, judge model, router model, budget (15 tool calls and 180 s
by default), cost cap, concurrency and the provider list. `KEENABLE_USD_PER_REQUEST` sets the
Keenable list price used for cost (default $0.004).

## Serving the report locally

```
uv run uvicorn searchaudit.web.app:app --reload
SHOW_FIXTURE=1 uv run uvicorn searchaudit.web.app:app   # also list the synthetic fixture
```

| Route | What |
|---|---|
| `/` | list of audits |
| `/r/<slug>` | the report |
| `/r/<slug>/q/<id>` | one prompt with every provider's answer |
| `/r/<slug>/print` | print view (source of the PDF) |
| `/r/<slug>.pdf` | the stored `report.pdf` |
| `/r/<slug>/results.json` | raw results |
| `/methodology` | how audits are run |
| `/health` | health check |

`audits/fixture/` is synthetic data from `scripts/fake_results.py`, for development only. To
regenerate it, run `uv run python scripts/fake_results.py`.

## Tests

```
uv run pytest -q
uv run ruff check .
```

All tests run offline with fake OpenRouter and Keenable transports. `tests/report/test_js_parity.py`
checks that the browser's slider recompute matches `stats.py`. It needs `node`, and is skipped without
it.

## Deploy (Railway)

The Railway service only serves stored reports. It needs no API keys, because the engine runs
locally. To publish an audit:

1. Run the steps above and check the report locally.
2. Commit `audits/<slug>/results.json`, `report.pdf` and `prompts.json`.
3. Deploy. You can push to the branch Railway watches, or run `railway up`.

The `Dockerfile` (python:3.12-slim + uv, runtime dependencies only) copies `searchaudit/` and only the
`results.json`, `report.pdf` and `prompts.json` from each audit (see `.dockerignore`). It starts
uvicorn on `$PORT`. `railway.toml` builds from the Dockerfile and health-checks `/health`. You can set
these optional variables: `SHOW_FIXTURE=1` and `AUDITS_DIR`.

To check the image locally:

```
docker build -t searchaudit .
docker run --rm -p 8000:8000 -e SHOW_FIXTURE=1 searchaudit
curl localhost:8000/health
```
