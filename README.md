# Search Audit

Search Audit runs one company's real prompts through several web-search providers, with **the same
pinned agent model, system prompt and budget** for each. A judge model grades every answer against a
checked reference answer. The result is a neutral report that ranks the providers on accuracy, cost
and speed.

The main audit is **SpaceX** (`audits/spacex-jobs`): 100 regulatory research prompts across three types,
regulation, list-building and multi-hop, with Grok as the native baseline and Exa and Parallel as the
providers it is compared against.

The comparison set is three companies, each aimed at 100 prompts (one run per prompt). Sixty prompts
can show that search beats model memory. It cannot reliably separate providers that differ by about
10 points. The [NEEDLE](https://keenableai.github.io/needle/) standings (7 days through 7 Oct 2026)
picked the workloads, because that is where a better index would change the product. The prompts are
the company's real jobs, not items written so one provider wins. Rankings go stale as models and
indexes change, so rerun before using one.

| Company | Workload | Why it is in the set |
|---|---|---|
| SpaceX | Consumer assistant (Grok) | The customer decision. The finished report is 60 prompts; a 100-prompt rerun is a new generation, not an overwrite. |
| Rogo | Investment-banking research | Head to head with Exa. On NEEDLE finance, Exa is 0.91 of the pooled best and Keenable is 0.89, and the intervals overlap. |
| Elicit | Scientific literature | The wide gap. On NEEDLE scholar, Keenable is 0.71 and Exa is 0.52, and the intervals do not overlap. That is a snippet benchmark, so this audit checks whether the lead survives an agent that also fetches pages. |

| Provider | id | How it is called |
|---|---|---|
| Exa | `exa` | OpenRouter server tool `openrouter:web_search` (engine=exa) |
| Parallel | `parallel` | OpenRouter server tool (engine=parallel) |
| Keenable search | `keen` | Keenable REST search and fetch as function tools in our own agent loop |
| Keenable SELECT | `select` | Keenable SELECT MCP. The agent writes the SQL the tool describes |
| Keenable routed | `routed` | Jev chooses search or SELECT once, then the agent has only that tool |
| No search (baseline) | `none` | Same agent, model, prompt and budget with zero tools: the model answers from memory |

### The no-search baseline

`none` is a control, not a provider. It runs the same agent loop with no tools and is told to say when
it cannot know something; the judge grades its claims against the reference without requiring sources.
Add it to an audit with one line, `- {id: none}` (label, colour and "model tokens only" price note
default). The report shows it in the ranking, chart and prompts like any provider, but it never sets the
cost/speed bar of the value score (so adding it cannot reorder the others), is never named in the
headline or the "most correct / cheapest / fastest" facts, and is ignored by the implementation guide.
It adds a **Search added** fact (prompts the best provider got right that no-search got wrong, and
whether that gain is within chance) and a **Search lift** line per provider (split by routine/hard when
both tiers exist). If no-search scores like the providers, the prompt set measures the model, not search.


## Layout

```
searchaudit/
  models.py        shared contract: AuditResults (meta, providers, prompts, runs, judgments)
  cli.py           probe / run / judge / assemble / querygen
  querygen.py      prompt-set generation pipeline (use cases -> tasks -> closed-book filter -> verify -> pilot)
  agent.py         the agent loop, concurrency limits, cost cap
  judge.py         judge prompt, JSON schema, rule checks
  notes.py         methodology caveats written into meta.notes
  providers/       exa, parallel (server tools), keen, select, routed, none (no-search baseline)
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
A=audits/spacex-jobs/audit.yaml

# 1. Probe: one snapshot per provider of its tool list and response shape -> audits/spacex-jobs/probes/
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

# 6. Render the PDF (starts the app itself) -> audits/spacex-jobs/report.pdf
uv run python scripts/make_pdf.py spacex-jobs
```

The audit's directory name is its URL slug, so `audits/spacex-jobs/` is served at `/r/spacex-jobs`.

Settings live in `audit.yaml`: agent model, judge model, router model, budget (15 tool calls and 180 s
by default), cost cap, concurrency and the provider list. `KEENABLE_USD_PER_REQUEST` sets the
Keenable list price used for cost (default $0.004).

## Generating the prompt set (querygen)

The prompt set decides what an audit measures. If the agent model can answer a prompt from memory, every
provider gets it right and the audit measures the model. `querygen` builds prompts that need search:

```
uv run python -m searchaudit querygen audits/<slug>/audit.yaml --target 100
uv run python -m searchaudit querygen audits/<slug>/audit.yaml --target 100 --pilot exa,keen   # + pilot stage
```

| Stage | What it does | Output |
|---|---|---|
| 1. Use cases | An LLM with web search researches the company (`company`, `category`, `company_url`, `use_case_hints`) and lists 6-10 real agent workflows with the persona who runs them | `querygen/use_cases.json` |
| 2. Tasks | Per use case, tasks phrased as an end user instructs an agent, spread over the five types, each with `expected_searches` (1, 2-3, 4+) and a draft answer; prefers fresh (last `fresh_days`), long-tail, multi-step and list tasks; `oversample` x target | `prompts.candidates.jsonl` |
| 3. Closed book | Every candidate runs through the pinned agent model with no tools (`none`) and is judged against the draft. Known ones are dropped, except `routine_share` (10%) kept as `tier: routine` controls | `querygen/closed_book.jsonl` |
| 4. Verify | A verifier with web search writes the reference (answer, partly-correct rule, wrong-answer traps, >= 2 sources on different domains, `as_of`) or rejects (ambiguous, sources disagree, unverifiable). The closed-book answer is re-judged against the verified reference | `querygen/verified.jsonl` |
| 5. Pilot (optional) | Runs the `--pilot` providers; drops prompts every provider gets wrong when a review finds the reference is the problem | `querygen/pilot.jsonl`, `pilot_review.jsonl` |
| Select | N prompts balanced by type and use case, ids 1..N, with `tier`, `use_case`, `expected_searches` | `prompts.json`, `PROMPTS.md`, `querygen/funnel.json` |

Every stage logs its in/out counts and each drop reason (also in `funnel.json` and `PROMPTS.md`); if
too few prompts survive it says so instead of padding. Stages resume: rerunning skips finished work, so
a run stopped by `querygen.max_usd` (default $5, or `--max-usd`) continues where it stopped. It refuses
to overwrite `prompts.json` of an audit that already has `runs.jsonl` unless you pass `--force`.

Research and verification use OpenRouter's `openrouter:web_search` with engine `native` (the model
provider's own search, Anthropic's for the default `anthropic/claude-sonnet-5.5`), so the prompt set is
not biased toward an audited provider; `exa` and `parallel` are refused as `querygen.search_engine`.
Settings live under `querygen:` in `audit.yaml` (`model`, `search_engine`, `oversample`,
`routine_share`, `fresh_days`, `max_usd`, `concurrency`).

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
