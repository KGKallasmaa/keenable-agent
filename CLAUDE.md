# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Search Audit (`searchaudit` package): runs one company's prompts through several web-search providers
with the same pinned agent model, system prompt and budget, has a judge model grade each answer against
a reference, and serves the result as a report (FastAPI + Jinja). `README.md` is the operator manual
(audit steps, querygen stages, routes, deploy).

## Commands

```
uv sync                                   # install (dev group includes pytest, ruff, playwright)
uv run pytest -q                          # all tests; offline, with fake OpenRouter/Keenable transports
uv run pytest tests/engine/test_agent.py::test_name -q   # a single test
uv run ruff check .                       # lint (line length 110)

uv run python -m searchaudit {probe|run|judge|assemble|querygen} audits/<slug>/audit.yaml [--limit N] [--providers exa,keen]
uv run uvicorn searchaudit.web.app:app --reload      # report server; SHOW_FIXTURE=1 lists audits/fixture
uv run python scripts/make_pdf.py <slug>             # PDF of /r/<slug>/print (needs playwright chromium)
uv run python scripts/fake_results.py                # regenerate audits/fixture/results.json
uv run python scripts/second_judge.py audits/<slug>/audit.yaml   # second-judge agreement -> second_judge.json
```

`run`, `judge` and `querygen` call paid APIs (OpenRouter, Keenable) using keys from `.env`. Don't run
them without being asked; tests never need them. `tests/report/test_js_parity.py` needs `node` and is
skipped without it.

## Architecture

**Two halves joined by one file.** The engine (laptop only, needs API keys) produces
`audits/<slug>/results.json`. The report server (Railway, no keys) only reads it. `searchaudit/models.py`
(`AuditResults`: meta, providers, prompts, runs, judgments) is that contract; change it with both sides
in mind. The audit's directory name is its URL slug (`/r/<slug>`).

**Pipeline, all resumable and append-only.** `cli.py` steps read `audit.yaml` and write into the same
directory: `run` appends to `runs.jsonl` and skips (prompt, provider) pairs already present
(`latest_by_pair`; `--retry-errors` / `--retry-tool-failures` re-run them); `judge` appends to
`judgments.jsonl`; `assemble` folds both (plus `pinned.json`, `second_judge.json`) into `results.json`.
`openrouter/auto` is resolved once and pinned in `pinned.json` so every provider uses the same model.
`CostCap` (`cost.py`) stops launching runs at `cost_cap_usd`. Do not overwrite `prompts.json` of an audit
that already has runs — scored runs would stop matching prompts (querygen refuses without `--force`).

**Providers** (`searchaudit/providers/`). `base.py` defines `Provider.open()` → `ProviderSession` per run.
A session supplies tools in one of two ways: OpenRouter *server tools* (`server_tools` +
`account_response`, used by exa/parallel/grok via `server_tools.py`) or *function tools* our loop executes
(`function_tools` + `call_tool` returning `ToolOutcome`, used by keen/select/routed). `none` is the
no-search baseline (zero tools, extra `instructions()`). Adding a provider = a module + a `register` call
in `providers/__init__.py:default_registry` + adding its id to `ProviderId` in `models.py`.

**Agent loop** (`agent.py`): one OpenRouter chat loop identical for every provider — same
`SYSTEM_PROMPT`, `max_tool_calls` / `max_seconds` budget, forced final answer when the budget runs out.
`run_jobs` handles global and per-provider concurrency (`concurrency:` in `audit.yaml`). Tool results are
trimmed into `evidence` the judge sees.

**Judge** (`judge.py`): grades c/p/w with four rubric checks (`RUBRIC` in `models.py`) plus a failure
mode (`FAILURE_MODES`). `notes.py` writes methodology caveats into `meta.notes`.

**Report** (`report/model.py` `build_view_model`): turns `AuditResults` into the template view model —
summary, ties, per-type routing guide, head-to-head, economics, sources, judge agreement. Statistics live
in `stats.py` (Wilson, exact McNemar + Holm, paired bootstrap, value scores). The browser's weight slider
re-implements the scoring in `web/static/report.js`; keep it in step with `stats.py` (enforced by
`test_js_parity.py`).

**Baseline rules.** The `none` provider appears in rankings and charts but must never set the cost/speed
bar of the value score, be named in the headline or leader facts, or feed the implementation guide. It
drives the "Search added" / "Search lift" figures.

**Other modules.** `querygen.py`: five-stage prompt-set generator (see README); it uses OpenRouter's
`native` search engine and refuses `exa`/`parallel` so the set isn't biased toward an audited provider.
`gap/`: gap report from a customer log (Model Diplomat export via `MONGO_URI`/pymongo, which is not in
`pyproject.toml`). `report/pitch.py` + `_spacex_*.html` templates: the SpaceX pitch page at
`/r/spacex-pitch`, which only shows scores once `audits/spacex-jobs/` is fully judged.

## Tests

`tests/engine/` uses `fakes.py` (scripted OpenRouter completions; Keenable served from probe snapshots in
`tests/engine/fixtures/`). `tests/report/conftest.py` provides `make_results(...)` for small synthetic
audits and loads `audits/fixture/results.json`. pytest runs in `asyncio_mode = "auto"`.

## Deploy

Railway builds the `Dockerfile`, which copies `searchaudit/` and only each audit's `results.json`,
`report.pdf` and `prompts.json` (see `.dockerignore`); health check is `/health`. Publishing an audit
means committing those three files.
