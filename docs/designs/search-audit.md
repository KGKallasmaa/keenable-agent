# Search Audit — design and architecture

Status: APPROVED architecture (2026-10-07): Python async backend, Next.js + TypeScript frontend, openrouter/auto pinned per audit, deployed to the existing Railway project. Supersedes the benchmark-only plan in
`~/.gstack/projects/KGKallasmaa-keenable-agent/impress-andrey-plan.md` (bench/ + app/ layout).
Remaining open items: SELECT tool shape (Stage 0 probe), StackAI funding stage (verify before the memo).

## 1. What it is

An SEO-style audit, but for search. You enter a company. The tool researches
the company with Keenable, works out how that company's AI agents use web search,
generates the queries that matter to its business, runs the same agent against each
search provider, judges the answers, and publishes a report at a public, unguessable URL.

- **Buyer persona:** VP Engineering / CTO at an AI company that ships agents.
- **First report:** StackAI (enterprise agent builder on Exa Search, Answer and Websets).
- **Positioning vs Needle:** Needle measures Keenable's index on a fixed set. A Search Audit
  measures every provider on *your* workload, with queries you can read, edit and re-run.
  That makes it testable, and so believable.
- **Honesty is a feature:** the report can conclude "Keenable is not the right fit for you",
  for example when SELECT loses on a freshness lookup like "who is Tesla's current CTO".
  A sales tool that can say no is one a CTO trusts.
- **Internal tool:** Keenable's business team (Artur) runs one per prospect and sends the link.

## 2. User flow

```
 /new  ──▶  [1 Company]  ──▶  [2 Profile]  ──▶  [3 Providers]  ──▶  [4 Queries]  ──▶  [5 Run]  ──▶  /r/<id>
            name + domain     Keenable fetch      Exa, Parallel,      ~50–100 across     background    report
                              + synthesis,        Keenable search,    categories;        job, live     + /r/<id>/q/<n>
                              cited, editable     SELECT, routed      edit/delete/add    progress,     + PDF
                                                  (defaults from                         email when
                                                  detected stack)                        done
```

1. **Company:** name (+ optional domain). Resolved via Keenable search, and the user confirms the right entity.
2. **Profile (with citations):** what they sell, who their customers are, which agent features they ship,
   detected search providers (public evidence like case studies, docs, job posts), and likely search
   workloads. Built with Keenable search + fetch + SELECT; every claim links its source. The user can
   correct it, because a wrong profile produces wrong queries.
3. **Providers:** checkboxes from the registry, with defaults = the detected provider(s) + Keenable search + SELECT + Routed.
4. **Queries:** the LLM generates N queries from the profile, tagged by category:
   `lookup`, `freshness`, `multi-hop`, `wide/list` (SELECT territory), `domain-specific`, `long-tail`.
   The user edits, deletes or adds them. Each query keeps its origin (generated / edited / added).
5. **Run:** a background job with progress per provider, an optional email when done, and a cost estimate shown before starting.
6. **Report** (§5).

## 3. Architecture

```
                         ┌──────────────────────── apps/web (Next.js, TS) ─────────────────────────┐
 browser ───────────────▶│ /new wizard · /r/[id] report · /r/[id]/q/[n] · /r/[id]/pdf (print view)  │
                         └───────────────┬──────────────────────────────────────────────────────────┘
                                         │ typed client (generated from OpenAPI)
                         ┌───────────────▼──────────── apps/api (FastAPI, Python 3.12) ─────────────┐
                         │ audits · profile · queries · runs · report JSON · PDF trigger            │
                         └───────────────┬───────────────────────────────┬──────────────────────────┘
                                         │ defer job                     │ SQL
                         ┌───────────────▼──────── worker (same package) ┐  ┌──────────▼──────────┐
                         │ procrastinate: profile_job, run_audit_job,    │  │ Postgres            │
                         │ judge_job, report_job, pdf_job, email_job     │──▶ audits, queries,    │
                         └───────────────┬───────────────────────────────┘  │ runs, tool_calls,   │
                                         │                                  │ judgments, jobs     │
          ┌──────────────────────────────▼─────────────────────────────┐  └─────────────────────┘
          │ searchaudit core (pure Python, no web deps)                  │
          │  providers/  base · exa · parallel · keenable · select ·    │──▶ MCP servers (Exa, Parallel,
          │              routed(jev)  — one file each, MCP clients      │    Keenable, Keenable SELECT)
          │  agent/      loop: OpenRouter chat + tools, budget, trace   │──▶ OpenRouter (agent model,
          │  judge/      rubric bundle, Claude judge, Jev 2nd/taxonomy  │    judge, Jev decisions)
          │  profile/ querygen/ stats/ report/                          │
          │  limits/     per-provider token buckets, global cap, $ cap  │
          └─────────────────────────────────────────────────────────────┘
```

**Boundaries.** `searchaudit` (the core) has no FastAPI or DB imports: it takes queries and config
and returns results. That gives a CLI (`python -m searchaudit run audit.yaml`), lets Keenable reuse it,
and makes it testable with fake MCP servers. The API and worker are thin shells around it.

**Providers (plugin interface).** Each provider declares its MCP endpoint, auth, a search tool and an
optional fetch tool (parity rule), rate limit, and how to extract cost from a response.

| Provider | Endpoint | Search / fetch tools | Limit / cost source |
|---|---|---|---|
| Exa | `https://mcp.exa.ai/mcp?tools=web_search_exa,web_fetch_exa` (`x-api-key`) | web_search_exa / web_fetch_exa | 10 QPS (/search); cost from response if present, else list price "~est" |
| Parallel | `https://search.parallel.ai/mcp` (Bearer optional) | web_search (objective + search_queries) / web_fetch | key for higher limits; list price |
| Keenable search | `https://api.keenable.ai/mcp` (`X-API-Key`) | search_web_pages / fetch_page_content | `_meta["keenable/usage"]` |
| Keenable SELECT | `https://select.keenable.ai/mcp` (Bearer) | tool TBD in Stage 0 probe / Keenable fetch | TBD in probe, else "~est" |
| Keenable routed | in-process | `search` → Jev (`typesafe/jev-1.13`) picks SELECT vs search per call | sum of the underlying calls + Jev |

Adding a provider (Codex/Claude Code harnesses later, Tavily, etc.) means one file plus a registry
entry, verified by a test that registers a fake provider.

**Agent.** A small loop of our own (about 150 lines, no framework): OpenRouter chat completions with
tool calls, and MCP tools via the Python SDK `Client` + `streamable_http_client`. Each query/provider
pair gets a fresh session. Budget: 15 tool calls and 180s; when it runs out, the agent must answer with
what it has, and the run is flagged. Every tool call is stored (args, latency, result size, cost, error).

**Model.** (decided: auto, pinned per audit) `openrouter/auto` gives a "realistic production" feel, but it can pick a
*different* model per request, which confounds the comparison (provider A might get Claude while
provider B gets a weaker model). Proposal: resolve auto **once per audit** (a calibration call;
record the `model` it returns) and pin that model for every agent, every provider. The report shows
"Agent model: openrouter/auto → anthropic/… (pinned for fairness)".

**Concurrency.** asyncio, with a global semaphore (default 16 in-flight agents), a token bucket per
provider (aiolimiter; Exa 10 QPS), retries with jitter on 429/5xx (2 tries), a per-audit $ cap, and
query-major ordering so a query's providers run minutes apart (freshness fairness). One worker
instance is enough for now; procrastinate can scale to more workers later without code changes.

**Judge.** A pinned bundle (rubric.md, schema.json, model id, temperature 0, version). For each answer
it grades correct / partial / wrong and whether the sources support the answer. Page text goes in a
delimited data block; canary pages test injection resistance; Jev tags failure modes (wrong answer,
unsupported citation, index gap, extraction miss, over-searching, tool error, empty, query-writing
error). Karl's hand labels on ~30 StackAI queries give the judge-agreement number shown in the report.

## 4. Data model (Postgres, Alembic migrations)

```
audits(id uuid pk, slug_token text unique, company text, domain text, profile jsonb,
       providers text[], agent_model_requested text, agent_model_pinned text,
       status enum(draft, profiling, ready, running, judging, done, failed, cancelled),
       email text null, usd_cap numeric, usd_spent numeric, created_at, finished_at)
queries(id pk, audit_id fk, position int, text, category enum, origin enum(generated, edited, added))
runs(id pk, query_id fk, provider text, status, answer text, citations jsonb,
     tool_calls int, usd numeric, usd_is_estimate bool, latency_ms int, budget_hit bool,
     error_type text null, model_used text, started_at, finished_at)
tool_calls(id pk, run_id fk, seq int, tool text, args jsonb, latency_ms, result_bytes, usd, error text null)
judgments(run_id pk/fk, grade enum, sources_support bool, reason text, judge_model, bundle_version)
failures(run_id fk, type enum)   -- many per run
```
State machine: `draft → profiling → ready → running → judging → done`; any state can go `→ failed`
(the error is stored and shown); `running → cancelled` by the user. No backward transitions;
editing a finished audit creates a new audit with the same queries ("fork and re-run").

## 5. The report (/r/<id>) — for a CTO

1. **Verdict sentence** with numbers + the recommendation (including "not a fit" if so).
2. **Providers table:** % correct (CI), sources-support %, $/query, $/correct, median (p90) latency,
   mean tool calls, errors.
3. **By category:** which provider wins where. This is the "where it fits" section (e.g. SELECT
   wins wide/list, search wins freshness lookups, routed gets both).
4. **Sources:** domains cited per provider, source types (gov, news, docs, forums, vendor),
   freshness (published-date spread), overlap between providers.
5. **Failure modes** matrix → filtered query list.
6. **Queries:** each one opens `/r/<id>/q/<n>` with every provider side by side (answer, sources,
   tool trace, $, latency, grade + reason, raw responses collapsed).
7. **Method & config:** pinned model, provider versions/tool lists, judge bundle + agreement,
   budget, dates, cost of the audit itself. "Fork this audit" re-runs it with edited queries.
8. **PDF:** `/r/<id>/pdf` is the same page with print CSS, rendered by Playwright in the worker,
   so there is one template, not two.

Public URLs: `/r/<token>` uses a 128-bit random token (not the DB id), `noindex`, and links
can be revoked. No login for now; auth can be added later without changing URLs.

## 6. Repo layout

```
.github/workflows/   ci.yml (api: ruff, mypy, pytest · web: eslint, tsc, vitest, playwright smoke) · deploy.yml
apps/api/            FastAPI app, routes, settings, Alembic, worker entrypoint
apps/api/searchaudit/  core: providers/ agent/ judge/ profile/ querygen/ limits/ stats/ report/
apps/api/tests/      unit (fake MCP servers in-memory), integration (Postgres testcontainer)
apps/web/            Next.js App Router (TS), typed API client from OpenAPI, design tokens from DESIGN.md
docs/designs/        this file, DESIGN.md, decision memo
```
Python tooling: uv, ruff, mypy --strict on core, pytest-asyncio. Web: pnpm, Next.js, vitest,
Playwright. Deploy on Railway: web, api, worker and Postgres services.

## 7. Stages

| Stage | Delivers | Exit check |
|---|---|---|
| **0 Foundations** | monorepo skeleton, CI green, Postgres + migrations; key probes: tools/list for Exa, Parallel, Keenable, SELECT (schema, prompt vs WQL, usage fields); Jev call; auto-router calibration | probe snapshots saved; CI runs on PR |
| **1 Engine + CLI** | providers (5), agent loop, limits, judge bundle, stats; `searchaudit run stackai.yaml` with ~60 hand-reviewed StackAI queries | JSON results for StackAI; judge agreement on Karl's labels |
| **2 Report** | `/r/<token>` + `/q/<n>` + PDF from stored results | **StackAI report shareable → send to Andrey** |
| **3 Audit wizard** | company → profile (Keenable, cited) → providers → query generation/edit → background run → email | end-to-end audit for a 2nd company with no code changes |
| **4 Polish + story** | sources analysis, routing recommendation, decision memo, video walkthrough | memo + video + report link sent |
| **Later** | Codex / Claude Code harness providers, auth, more providers, multi-worker | one-file additions |

Stages 0–2 are the interview-critical path. Stage 3 is what makes it an internal tool for Artur.

## 8. Risks

- SELECT MCP tool shape unknown → Stage 0 probe; WQL-writer fallback (decision R2).
- Auto-router non-determinism → pin per audit (above).
- Judge credibility → rubric + agreement number + canaries + raw evidence on every query.
- Provider ToS/rate limits → per-provider limiter; keys from env only; audit $ cap.
- Profile hallucination → every profile claim cited; user confirms before queries are generated.

## 9. Additions (2026-10-07, user)
- **Methodology page** at `/methodology`, also summarized live on the running page ("what's happening now").
- **Upload your own queries:** .csv/.txt, one query per line (dedupe, skip empty, cap of 200 on hosted). Jev classifies each into the categories with a confidence score, and the user can override the label. Origin `uploaded`. Generated queries stay as the other option and are labeled a guess.
- **Agent harness choice:** the mini agent (OpenRouter, pinned), or Claude Code or Codex in headless mode with only the provider's MCP server attached. Harnesses are plugins like providers; the coding-agent ones are a later stage.
- **Clickable prototype:** `docs/prototype/index.html` (illustrative data). The flow is approved there before Stage 0 coding.

---

# CEO review: the report (2026-10-07)

User ask: "make sure the report we generate is perfect and also the homepage and ui (the favicons are off) and style looks bad. i could live with a shitty homepage, but not with a shitty and useless report."
Review depth: implementation-ready for the report (content model, sections, data each needs, states); design-level for the homepage and style.
System audit: no commits; prototype `docs/prototype/index.html` (536 lines, hash-routed, illustrative data). Learning applied: commit before rewriting (keenable-agent-no-commits).
Landscape: vendor evaluation guides (You.com, Parallel) judge on accuracy, freshness, latency, cost and failure patterns, and compare only within one latency class. None produce a customer-specific decision with $ impact and a pilot plan. That is the differentiator. [EUREKA] Compare SELECT and search per query type, never on one leaderboard: they are different latency classes and different jobs.

## 0A Premise
(Amended after spec review 1. D1 chose A, a polished scoreboard, so the remedy below is scope A + E1–E4, not a decision document.)
The real problem: the StackAI CTO (or any reader) must be able to decide "switch, split, or stay" from the report alone. Today's report is a leaderboard. It overclaims (85% vs 81% ±6–7 is a tie), shows no absolute dollars, has no production-readiness data, no feature-parity map, no migration or pilot plan, and nothing on compliance or vendor risk. Do-nothing cost: Andrey sees the benchmark format he sees every week, and a CTO can't act on it.

## 0B Existing leverage
Reuse the prototype routing, data shapes (ARMS, SUMMARY, CATS, QUERIES), the query-detail page (approved), methodology page and design tokens. Rebuild the report view and the visual system (type scale, spacing, favicons).

## 0C Dream state
```
CURRENT                         THIS PLAN (scope A + E1–E4)                12-MONTH IDEAL
Leaderboard report;             Honest, readable scoreboard with $ at      Keenable's sales team sends an audit
overclaims; no $; ugly UI;      the customer's volume (E1), a pilot (E2),  per prospect; prospects re-run it on
broken favicons                 share card (E3), copy (E4), clean style    their logs; pilots convert from it
```

## Ledger (CEO review, report)
| ID and owner | Contract and evidence | Current | Proposed | Status | Exact approval and scope |
|---|---|---|---|---|---|
| APPROACH / Karl | Report type | Leaderboard prototype | A polished scoreboard / B decision doc / C decision doc + what-if | approved: A | D1 answer "A) Polished scoreboard", confirmed "Yes, A: keep it simple" (2026-10-07). Scope: better style, honest headline, fixed favicons, same structure. Excludes production readiness, parity map, compliance and migration (deferred to TODOS.md). $ at volume and the pilot were later added by D3/D4 (E1/E2), which override this exclusion. |
| MODE / Karl | Review posture | — | — | approved: SELECTIVE EXPANSION | D2 answer (2026-10-07); recommended was HOLD SCOPE. |

## 0F/0G (SELECTIVE EXPANSION)
Hold checks, all inside approved scope A (no question needed):
1. Complexity: a single prototype file. No new services.
2. Minimum to fix: (a) honest headline and labels ("no clear difference" when not significant after Holm, per the paired test); (b) readable type scale, spacing and hierarchy; (c) favicons for providers and cited domains from real domains, plus a monogram fallback for companies (StackAI's own favicon renders blank); (d) homepage list styling.
3. Invariants: no claim stronger than the data supports; every provider shows with its brand favicon; the report reads top-down in under 60 seconds.

Cherry-pick candidates, asked one at a time: E1 "$ at your volume" line; E2 three-line pilot plan; E3 link preview card for sharing; E4 "copy summary" for Slack/email.
| E1-DOLLARS / Karl | "$ at your volume" line | none | optional monthly-volume field in the wizard; report line "At N questions/month: $X → $Y"; if blank, 3 labeled example volumes | approved: add | D3 (2026-10-07) |
| E2-PILOT / Karl | next step | none | 3-line pilot under the recommendation: shadow 5% for 2 weeks; watch quality + p95; roll back per the CI-bound rule in "Spec review 1 fixes" | approved: add | D4 (2026-10-07) |
| E3-PREVIEW / Karl | link sharing | bare URL | Open Graph tags + 1200×630 preview image rendered by the PDF worker (company, verdict, key number) | approved: add | D5 (2026-10-07) |
| E4-COPY / Karl | forwarding | none | "Copy summary" button: 5 plain-text lines (verdict, 3 numbers, recommendation, link) | approved: add | D6 (2026-10-07) |

## Report spec (scope A + E1–E4)

**Honesty rules (part of A, "honest headline"):**
- Compare providers with a *paired* test on the same questions: McNemar on correct/incorrect, plus a bootstrap CI on the paired difference. If p ≥ 0.05, the label is "no clear difference", and the headline must not say better or worse.
- Every number gets a unit and a basis ("per correct answer", "median of 80").
- Estimated numbers get a "~" and a footnote. A section with too little data (n < 10) says so instead of showing a %.
- Headline template: "<Provider> gives <Company>'s agents <no clearly different | better | worse> answers than <baseline> at <ratio> the cost per correct answer<, except on <types>>." "Same" is never used, because not significant does not mean equal. Its words are computed from the test results.

**Anatomy (11 parts, top to bottom, one column, max width 920px, left-aligned):**
1. Header line: company mark · "Search Audit" · date · actions: PDF, Copy summary (E4), Fork & re-run.
2. Verdict headline (serif, 36–40px), then a one-sentence lede on what was run.
3. Four key numbers, separated by rules (no cards): answer quality ("85% vs 81%, no clear difference"), $ per correct answer, median latency, $ at your volume (E1).
4. Scoreboard: one row per provider, sorted by quality then cost. Columns: provider (favicon + name), quality bar with a CI whisker and a "tied for best" badge when not significantly different from the leader, sources-support %, $ per correct, median latency, tool calls, errors. The leader row gets a quiet ink outline; no colored backgrounds.
5. Where each wins, by query type: a compact matrix, with the winner per row marked, or "no clear winner" when not significant after Holm; "too few questions" when n < 10.
6. Three example moments: the question, then two short side-by-side answers and one sentence on why.
7. Recommendation (2–3 lines), then the pilot (E2).
8. What they cite: the most-cited domains per provider (favicons) and source-type shares.
9. How each provider fails: the failure-mode counts, linking to the filtered questions.
10. All questions: grade dots per provider, with column headers showing favicon + short name (not hidden text).
11. Method in 3 sentences + a link to /methodology. Audit cost.

**Visual system:**
- Type: display serif 40/28/20; body sans 16/1.6, 17 in the report; numbers mono with tabular figures.
- 8px spacing grid. Sections are separated by 56px plus a hairline. One accent per provider, used only for its name, bar and dot.
- Favicons:
  - Providers: exa.ai, parallel.ai, keenable.ai. The three Keenable variants share the Keenable favicon plus a text suffix (search / SELECT / routed). Their colour is never the only signal.
  - Companies: their domain favicon, with `onerror` falling back to a monogram. Fixes StackAI's blank icon.
  - Cited domains: the real domain favicon.
  - Production: favicons fetched and cached server-side, never hotlinked.
- Homepage: hero line, "+ New audit", then the public reports list (favicon, company, computed verdict, meta) in one clean ruled list.


## Spec review 1 fixes (6/10). Clarifications inside approved scope
- **Grades in tests:** partial counts as incorrect for McNemar. A sensitivity line reruns it with partial counted as correct, and the report shows the result if the verdict flips.
- **Baseline:** the detected current provider (StackAI → Exa). If several were detected, the most-cited one; if none, the cheapest. Tests compare the leader against each other provider and the baseline against each Keenable variant, with Holm correction. "Tied for best" means not significantly different from the leader after Holm.
- **One method everywhere:** the category matrix uses the same paired test (Holm within a category). Categories with n < 10 show "too few questions" instead of a winner.
- **Runs that fail or hit the budget** count as incorrect for quality (a failure is a failure for the user) and are listed in the errors column. They are never dropped (zero silent failures, a prime directive of this review; earlier plan decision ERROR-POLICY).
- **Decision table** (computed; recommendation and pilot are templates filled from the results, never hand-written). Rows are evaluated top to bottom, and the first match wins. <K> = the qualifying Keenable variant with the lowest $ per correct answer. If the detected baseline is itself Keenable, the baseline becomes the cheapest non-Keenable provider.

| Situation | Headline | Recommendation | Pilot (E2) |
|---|---|---|---|
| Overall n < 30, or one provider failed > 50% of runs | "Not enough evidence yet: …" with the reason | Re-run with more queries, or fix that provider | hidden |
| Mixed: Keenable significantly wins at least one query type and significantly loses at least one | "…, except on <type1>, <type2>" (up to 2, then "and N more") | Route by query type: <K> for <types>, keep <baseline> for <types> | shown, scoped to the winning types |
| A Keenable variant is better, or no clearly different, and cheaper (≥10% lower $ per correct) | "<K> gives … <better/no clearly different> answers … at <r> the cost per correct answer" | Switch the default to <K> | shown |
| A Keenable variant is better but not cheaper | "<K> gives … better answers than <baseline> at <r> the cost per correct answer" | Switch for the types where <K> significantly wins after Holm (every type if none qualify); weigh the price difference | shown |
| Keenable is worse overall | "<baseline> stays ahead for <Company>: …" | Stay with <baseline>; re-audit in 90 days | hidden |
| No clear difference overall and Keenable is not ≥10% cheaper | "No search provider stands out for <Company> on these questions" | Choose on price, latency or terms; upload real logs for a sharper answer | hidden |

- **E1 formula:** $/month = volume × total cost per question (search + agent model + tools) for each provider. Any "~" estimated input makes the output "~". Stage 2 (StackAI) shows three labeled example volumes (100k / 1M / 10M), and the wizard field arrives in Stage 3.
- **E2 threshold:** roll back if quality in the pilot falls below the lower bound of the audit's CI for that provider (not a fixed 2 points). Measured in production by sampling 5% of pilot answers through the same judge bundle.
- **E4 summary:** the headline; quality (leader vs baseline); $ per correct (leader vs baseline); $ at the middle example volume; the recommendation; the link.
- **Moments (anatomy 6):** the 3 questions with the largest disagreement between the recommended provider and the baseline, at most one per query type, preferring one win, one loss and one router decision.
- **E3 on revoke:** revoking a link deletes the preview image and the OG tags return a generic card.
- **Structure kept (scope A):** Sources ("What they cite", with cited-domain favicons) and Failure modes come back as sections 8 and 9 before "All questions". Sources-support % and errors are columns in the scoreboard.
- **PUBLIC-LIST (D7, user: "Unlisted by default, opt-in"):** reports are unlisted by default. A "Show this report on the public list" checkbox in the wizard opts in. There is no login, so changing it later is internal-only (CLI/admin), with no owner action on the public page. The homepage gallery shows only opted-in reports. Empty state: "No public reports yet. Run an audit and tick 'Show on the public list' to add one."
- **Work split:** the prototype (`docs/prototype/index.html`) carries the content, layout, favicons with monogram fallback, the decision table, E1 (example volumes), E2, E4 and illustrative per-question paired data (n=80, seeded) with the stats computed in-page. Stage 2 carries server-side favicon caching, the E3 preview image (PDF worker), OG tags and real stats in `stats/`.
- **TODOS.md:** the decision-document parts not chosen in D1 (production readiness, parity map, compliance fit, migration) are recorded as deferred.

## Answered: 0H document approval
User: "Approve and continue (recommended)" (2026-10-07). The spec as of spec review 3 is approved.

## 0I Temporal interrogation (prototype rebuild, then Stage 2)
```
HOUR 1:   seeded per-question paired data (n=80, 5 providers); stats in-page: McNemar exact, Holm, Wilson CI, bootstrap
HOUR 2-3: decision table → headline, recommendation, pilot; 11-part report; favicon helper with onerror → monogram
HOUR 4:   homepage list (opt-in only + empty state), header + New audit, Copy summary; E1 example volumes
HOUR 5+:  screenshots at 1280 and 500 px, a check of the copy against the honesty rules
Surprise to plan for: with n=80 and close providers, most pairs will be "no clear difference". That is the honest
headline, and the illustrative data must not be tuned to manufacture a win.
```
Effort: human ~1 day / CC ~1.5 h for the prototype. Stage 2 production parts (E3 rendering, favicon cache) follow the stage plan.

# Review sections (HOLD within approved scope)
1. **Architecture:** the report is a pure function of the stored results → view model (stats + decision table) → template. The same view model feeds the HTML page, PDF, OG card (E3) and Copy summary (E4), so they never disagree. No issues.
2. **Error & rescue:** favicon load fails → monogram (`onerror`); stats with n<10 → "too few questions"; provider >50% failed → "not enough evidence" row; clipboard API unavailable (non-https) → fallback that selects the text with a "Press ⌘C" hint; revoked link → 410 page + generic OG card. No unrescued paths.
3. **Security:** production favicons are fetched server-side and cached, so viewer IPs don't leak to Google, and no third-party hotlinking. The prototype hotlinks, and that is labeled. Public list is opt-in (D7). Report pages are noindex. No issues beyond the plan.
4. **Data flow & edge cases:** Copy-summary double click is idempotent. Mixed results with more than 2 losing types → "and N more". Ties in the leader's sort order → by $ per correct, then name. Covered.
5. **Code quality:** one `buildReportModel(results)` function plus small renderers; decision table as data, not if/else chains. No issues.
6. **Tests:** decision-table cases (each row plus the first-match order), Holm/McNemar against known values, favicon fallback, the n<30 path, the Keenable-as-baseline swap, E1 "~" propagation. In Stage 2: snapshot of the view model for a fixture audit.
7. **Performance:** stats are computed once per report at build time and stored. Bootstrap uses 2,000 resamples (about 10ms for n=80). No issues.
8. **Observability:** log the report-model build with the decision-table row chosen. Log copy-summary and PDF clicks as product analytics. No gaps.
9. **Deploy:** prototype is static. In Stage 2 the report template ships with the API; the OG image is rendered on the first request and cached. No risk.
10. **Trajectory:** the decision-document parts are in TODOS.md, and the view model has room for them. Reversibility 5/5.
11. **Design & UX:** the anatomy and visual system above. States: generating (link to the live run page), partial, revoked. Mobile: key numbers 2×2, scoreboard rows stack, matrix scrolls in its frame. Run /plan-design-review on the rebuilt prototype if desired.

## Outside voice
Codex: model_unusable (configured gpt-6-luna); native fallback unavailable (no TaskOutput tool). Coverage unavailable.

## Approval readiness: PASS
APPROACH (D1, confirmed), MODE (D2), E1 (D3), E2 (D4), E3 (D5), E4 (D6), PUBLIC-LIST (D7), 0H approval. Clarifications from the 3 spec reviews are within these approvals.

## Implementation tasks (report)
- [ ] **R1 (P1, human ~1d / CC ~1.5h)** — prototype — Rebuild report + homepage to the spec with seeded paired data and in-page stats. Verify: screenshots at 1280/500px; every headline phrase traceable to a decision-table row.
- [ ] **R2 (P1, human ~4h / CC ~30min)** — Stage 2 — `stats/` (McNemar, Holm, Wilson, bootstrap) + `report/model.py` decision table, with unit tests per row.
- [ ] **R3 (P2, human ~3h / CC ~20min)** — Stage 2 — server-side favicon cache + E3 OG image via the PDF worker.

## Completion summary
Mode SELECTIVE EXPANSION · Step 0: approach A + E1–E4 + D7 · spec review 6→7→8/10 · Sections 1–11: 0 critical gaps · NOT in scope: decision-document parts (TODOS.md) · outside voice unavailable · Lake Score 0/0 (no coverage-scored question was answered with its 10/10 option; D1 picked A).

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR | 5 proposals, 5 accepted, 0 deferred (report: A + E1–E4, D7) |
| Outside Review | codex | Independent 2nd opinion | 1 | unavailable | model_unusable; no completed external review |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 0 | — | — (this doc not yet eng-reviewed) |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **OUTSIDE COVERAGE:** codex, plan-review phase, unavailable (configured model unusable; no native fallback tool).
- **VERDICT:** CEO CLEARED for the report spec. eng review required.

NO UNRESOLVED DECISIONS

## Report reframe after prototype feedback (2026-10-07, user)
User feedback on the scoreboard prototype: "we don't only compare against [Exa]… the goal is: given all the search providers out there, tell me which one is the best… it should not show up on top because we generated the report… On a query level, I want to see the analysis, and then I can delve deeper… show me the answers… the real difference between the providers… right now 1 out of 10."
Applied to the prototype (supersedes the anatomy above; the honesty rules and stats are kept):
- **Neutral framing:** the headline ranks all providers, with no "vs baseline". When several tie on correct answers (paired test + Holm), it says "no single winner" and names the cheapest of the tied group. The baseline only appears as a "StackAI uses today" tag.
- **The comparison KPI box is removed.** It is replaced by three neutral facts: most correct (tied group), cheapest of the best, fastest.
- **Tabs:** Questions (default) · Providers · Cost · How we measured.
- **Questions tab (core):** every question shows a ✓/◐/✗ chip per provider. Filters: providers disagree (default), all, nobody right, plus question type. Expanding a question shows "Why they differ", then every provider's answer, grade, sources (bold = cited by more than one provider) and searches/$/time, with a link to the shareable per-question page.
- **Plain-language numbers:** "65 of 80 correct", "sources backed it: N of M", with a one-line definition under each column header.
- E1 lives in the Cost tab (all providers × 3 volumes). E2 is a generic "try a cheaper provider safely" box in the Cost tab. E4 copies the ranked list.

## Report v4 (2026-10-07, user feedback round 3)
User asks, all applied in `docs/prototype/index.html`:
- **Header:** company + category only; actions are **Share** and **Download**; Methodology only in the top nav.
- **Wording:** "questions" are renamed **prompts**. Grades are icons (✓ ◐ ✗) with a legend, not words.
- **Colours:** Keenable search blue, SELECT red, routed purple, Exa orange, Parallel slate. Result colours are never red, so SELECT stays unambiguous.
- **Scoring:** a value score of accuracy × (accuracy, cost, speed weights), with wrong = 0, normalized per prompt. Sliders let the reader set the weights. A Pareto chart plots accuracy vs cost, with dot size = median time and rings on the Pareto front. "Tied" means a McNemar + Holm accuracy tie.
- **Prompt drill-down:** inline and table-first. One row per provider: result, 4 judge-check icons, failure mode, score, time, cost, searches, sources (ringed = cited only by that provider). Clicking a row opens the answer, judge note, tool trace and a reproduce command. The "why they differ" prose is removed.
- **Keenable routed:** derived from the search and SELECT results via Jev's per-prompt choice, so it is not an independent draw.
- **Tabs:** Prompts · Failure modes (taxonomy × provider, by type, click-through) · Implementation guide (computed routes, honest notes where another provider beats Keenable, router config code, cost at volume including "Recommended router", rollout) · Judge (model, human agreement, confusion matrix, manipulation test, judge disagreements).
- **Homepage:** company + category + meta per row.
