# SpaceX pitch — working plan

Status: review in progress. Branch: main. Base branch: main.
Mode: SCOPE EXPANSION. Chosen by the user as D2 answer A, 2026-10-08. Not auto-decided.
Floor from D1 answer B, read with later decisions: do not rewrite a finished table into a win. The DeepSeek grades are not printed. The page's numbers are the Grok re-run and the new set.
Audience the user named: a walkthrough for SpaceX, with Cursor and Grok as the doors after.
The contract below is the earlier review. It is not the page.

## Decision, later on 2026-10-08

The page is compliance only. The consumer 60 are not on it. One hundred prompts, not run yet: US spectrum and licenses, launch and range, mission insurance, tables across filings, Canada in the language of the source, other countries in the local language, and the rule as of a date.

The pitch says Keenable is the tool we would put in front of SpaceX. A ranking is printed only when the answers are graded. Same model, same instructions, same budget. The search tool is the only change, except the SELECT SQL skill, which is that product. If Keenable does not win on that isolated comparison, the page does not ask for a place inside Grok or Cursor.

Charts, when the run exists: cost and speed, cost and accuracy, accuracy and speed. Each prompt shows the answer each provider wrote. The runs wait on account credit. The UI does not.

Option cards earlier in this file are the questions that were asked. They are not instructions. Andrey's gap report stays a separate artifact.

## 0A. Premise

The pain is not "our Elicit chart is hard to read." The pain is that SpaceX has no reason, on the current page, to put Keenable inside Grok or Cursor. The user wants one page they can walk through: Elon's line on top, the compliance and science jobs, a rigorous method, parity with Exa and Parallel, and SELECT plus the time machine as the difference.

Doing nothing leaves the SpaceX audit as a 60-prompt consumer-assistant scoreboard. On that set Exa is ahead or tied, so the meeting cannot say "Grok would be more accurate on Keenable."

The plan solves the pitch directly only if the page is allowed to say what the runs actually show. It solves a proxy if we re-label the sports-and-news set as a Mars compliance study.

Facts checked 2026-10-08:

- Cursor is a SpaceX subsidiary. The Cursor blog (14 Aug 2026) says the acquisition closed. Reuters (16 Jun 2026) reported the $60B agreement. That part of the premise holds.
- The post the user means is Elon Musk, 7 Oct 2026, about Grok Bot: "Going forward, @SpaceX will use the best back end model for any given task, including Claude Opus 5.5, MidJourney, Suno and other leading APIs. Whatever is most likely to give you the best outcome." It names models. It does not name a search provider. The pitch may use it as the principle. It may not quote it as a promise to switch search.
- Exa publishes an official Cursor marketplace plugin and MCP server. That does not establish that Cursor's own agent uses Exa as its default search backend, or what the commercial deal is. The page must not assert a deal we cannot cite.
- SpaceX audit, 60 prompts, judged: Exa 59 correct, Parallel 58, Keenable search 56, SELECT 55, routed 52. Wide, the list type: Exa 12/12, Parallel 12/12, search 11/12, SELECT 9/12, routed 6/12. Domain (the eight FCC and spectrum items): Exa, search, SELECT, and routed are 8/8. This set cannot show Grok failing, or SELECT beating a search loop.
- NEEDLE scholar (7 days through 7 Oct 2026), from the README: Keenable 0.71, Exa 0.52, intervals do not overlap. That is titles and snippets. The Elicit agent audit, which also fetches pages, does not carry that lead: Exa 95/100, SELECT 89, search 86.
- OpenRouter already has a `native` search engine used by querygen so research is not biased toward Exa or Parallel. A Grok model plus `native` is the arm that shows Grok's own search. It is not in the SpaceX provider list today.
- Active decision, 2026-10-07: Andrey opens the gap report, not a scoreboard, and a prompt set built so Keenable wins was dropped. This pitch is a different room. It does not silently become the artifact Andrey opens.

## 0B. What already exists

| Job | Reuse | Do not rebuild |
|---|---|---|
| Same model, budget, judge, Wilson and McNemar | `searchaudit/` engine and the report | A second eval app |
| SpaceX consumer prompts | `audits/spacex/` 60, already judged | Calling them the compliance study |
| Regulation-shaped prompts | SpaceX 53–60, StackAI domain and wide | Pretending 8/8 is a separation |
| Literature workload | Elicit audit plus the NEEDLE scholar note in the README | Claiming the agent audit reproduced the snippet lead |
| SELECT vs a search loop | Router skill and the guide section "Where one call is the table" | A new SQL writer |
| Index as of a date | `query_time` on Keenable search | Claiming SELECT's SQL accepts `query_time` (not documented) |
| Grok's own search | OpenRouter `engine=native` in querygen | Inventing a Grok engine name |

Rebuilding the judge or the provider loop to make a slide is worse. The new work is the page order, a new prompt generation aimed at compliance tables and papers, and one more provider arm.

## 0C. Dream state

```
CURRENT STATE                         THIS PLAN                              12-MONTH IDEAL
SpaceX page is a consumer             One walkthrough: the 7 Oct line,      Keenable is a search tool
scoreboard.                           the compliance and science jobs,      Grok and Cursor call,
Elicit is a lab note.                 a Grok re-run of the 60, SELECT and   chosen when the job is a
Andrey's gap report is the            query_time named as the unproven      table or a date-cut,
other room.                           difference until that run exists.     with the method public.
```

The Grok re-run and the new set are in scope. The page does not say Keenable is more accurate until those runs exist. The launch-rate sentence is why the job matters. It is not a measured delta.

## Ledger

| ID | Owner | Current | Proposed | Status |
|---|---|---|---|---|
| AUD-1 | User, this review | Andrey opens the gap report. | The walkthrough is a SpaceX page. D1 B is the floor: do not rewrite a finished table into a win. It is not permission to print the DeepSeek grades as the proof. | approved. D1 answer B, 2026-10-08, read with CLAIM-1 and EXP-4. |
| CLAIM-1 | This review | The DeepSeek 60 would be easy to misread as a win. | Those counts stay on disk and are not printed. The page may say Keenable is ahead only if the Grok run shows it. | approved with EXP-4. |
| EXP-1 | User, D3 answer A, 2026-10-08 | No second SpaceX set. | A new compliance and science set is generated and run. The 60 stay as the parity block. | approved. Scope: launch licenses, spectrum and regulatory tables, engine and literature lookups, date-cut questions. Same judge and providers. Not a claim until the run exists. |
| EXP-2 | User, D4 answer A, then named in chat 2026-10-08 | Providers are Exa, Parallel, search, SELECT, routed. | Columns are Exa, Parallel, Keenable search, Keenable SELECT, Grok native, Keenable router. The agent under test is Grok, on every column. | approved. The user named this list. Native is not swapped for another engine if it cannot be pinned. |
| EXP-4 | User, D6 answer A, 2026-10-08 | The page uses the DeepSeek 60 (Exa 59, search 56, SELECT 55). | Those 60 are re-run on the same Grok model as the new set, six columns. DeepSeek stays on disk and is not the page. | approved. |
| EXP-3 | User, D7 answer A, 2026-10-08 | Cursor not on the page. | A short note: Cursor joined SpaceX on 14 Aug 2026. Exa has a public marketplace plugin. The default backend is not claimed. The Cursor path is a reason to be considered, not a signed deal. | approved. |
| EXP-5 | User, chat after D8, 2026-10-08 | Method does not mention NEEDLE. | The method says NEEDLE leads on scientific literature, and that this is why the idea exists: a search strong at academic retrieval might be strong for compliance (law and academic sources) and for engineers who need physics papers. The word is might. The Elicit agent audit (Exa 95, SELECT 89, search 86 of 100) stays next to it so the hypothesis is not told as an agent win. | approved. User's wording. |
| EXP-6 | User, D9 answer A, 2026-10-08 | No business chain on the page. | The top says faster compliance, insurance, and reporting is one way flight rate goes up and cost to orbit comes down. No launch count and no dollars per kilogram. | approved. |
| EXP-8 | User, D11 answer A, 2026-10-08 | Unspecified which files re-run on Grok. | Grok re-run is the SpaceX 60 plus the new compliance and science set, six columns. Elicit and StackAI stay on their existing runs. Rogo is not generated for this page. | approved. |
| EXP-7 | User, D10 answer A, 2026-10-08 | SELECT is only a column. | Jobs are on the page before scores. On a date-cut row every available column is graded against a snapshot of that date. Only search and the router pass query_time. The other four are live search. | approved, scoring rule set in SPEC-4. |

## currentDecision (AUD-1)

Commitment comparison:

| Commitment | Source | Current | A | B | C |
|---|---|---|---|---|---|
| Who the page is for | User this turn vs Andrey decision | Andrey gap report | SpaceX walkthrough; gap report stays | SpaceX, and it replaces the Andrey opening | Spoken deck only |
| What "more accurate" may say | SpaceX results.json | Exa 59, search 56, SELECT 55 of 60 | Parity on the 60. Accuracy claim waits on a new set. | Say Keenable wins on the 60 | No page claim |
| Tweet | Musk, 7 Oct 2026 | Not on the page | Principle, quoted as models | Quoted as a search promise | Spoken only |
| Cursor and Exa | Cursor blog; Exa marketplace | Not on the page | Cursor is SpaceX. Exa plugin is cited. Default-backend deal is not claimed. | "Cursor is powered by Exa" as fact | Not written |
| New prompt set | User this turn | Not run | In scope after this choice | Not needed, reuse the 60 | Not in the product |

Question: D1 — AUD-1: Which page are we allowed to build?
D1's floor, one sentence: do not print the DeepSeek grades. The page being built is the Accepted page plus the Run spec. The option text below is the question that was asked. It is not an instruction to treat those grades as proof.
Header: Page for SpaceX
A) SpaceX walkthrough, gap report stays (recommended)
The page the user walks through starts with the 7 Oct line, the compliance and science jobs, and the method. The existing 60 prompts show parity. A new set has to be run before the page says Keenable is more accurate. Andrey's gap report stays a separate artifact.
Effort L, risk medium. Reuses the engine. Verification: the page cannot state a win the 60 do not contain.
✅ The meeting can use the tweet as a principle without misquoting it, and the measured block stays honest.
✅ Cursor being inside SpaceX is cited, and the Exa relationship is limited to the plugin we can point at.
❌ The accuracy story the user wants is not on the page until the new compliance and literature set is generated, run, and judged.
B) Re-skin the existing 60 as the compliance win
Put the tweet and the Mars paragraph on the current SpaceX report and treat those grades as the proof.
Effort S, risk high. No new runs.
✅ The page exists today and the user can present this week.
✅ Domain items 53–60 are real FCC and spectrum questions, so the compliance theme is not invented from nothing.
❌ Exa is ahead on the 60, and SELECT is behind on lists, so a careful reader sees the opposite of the claim.
C) Keep it a spoken deck
No new page. The user tells the story. The reports stay as they are.
Effort S, risk low.
✅ Nothing on the site can be quoted back as a false accuracy claim.
✅ The Andrey decision is untouched.
❌ The user asked for a page they will go over, with the tweet, the method, and the jobs on it.

## 0F–0G. Accepted page order

10x for about 2x a re-skin: the walkthrough opens on the 7 Oct line and the launch-cadence reason, then a Grok re-run of the 60, then a compliance and science set where a table or a date cut is the actual task. The DeepSeek outcome is not the page's parity claim.

Platonic ideal: one page a SpaceX compliance lead and a Cursor engineer can both read. Top is the quote, quoted as models. Then the job (a launch license, a spectrum order, a paper on an engine, the same question as of a past date). Then the method. Then the Grok re-run of the 60. Then the new set. Grok's own search is a column. Cursor is the distribution line: SpaceX owns it, Exa's plugin is public, the default backend is not claimed.

Delight items, accepted as part of the page order below:
- The 7 Oct quote, dated, linked, not paraphrased into a search promise.
- One real FCC prompt from the 60 opened on the page (the Amazon Leo waiver).
- A "this page does not say" line next to the scores.
- Cursor: closed 14 Aug 2026, three lines, no invented deal terms.
- query_time shown on one regulation question as a date cut, not as a measured win.

EXP-1 through EXP-8 are accepted. The delight items are the page order, not a second list of work.

## Run spec

Added in this review as SPEC-1 through SPEC-7. They are not answers D3 through D11. They become part of the plan only if these documents are approved.

- SPEC-1. Model: `x-ai/grok-4.7` on every column. One models-list call runs first. Use that id if the list has exactly one Grok 4.7. If it has zero, or more than one, the run stops. Do not use `openrouter/auto`.
- SPEC-2. Provider id `grok` is added beside exa, parallel, keen, select, routed. It is OpenRouter `engine=native` on that model. A failed probe sets the column text to "unavailable" and does not drop the column or substitute another engine. A column's n is the prompts that have a judgment for that provider. Unavailable cells are left out of that n.
- SPEC-3. Directories: `audits/spacex/` is the DeepSeek run and is not overwritten and is not read by the page. `audits/spacex-grok/` is the same 60 prompts on Grok. `audits/spacex-jobs/` is the new set. The page is a new template over those two files.
- SPEC-4. New set: 60 prompts. 24 compliance tables, 24 engine and literature lookups, 12 date-cut questions. Querygen writes them. A person checks the references before the run. Each date-cut reference is a snapshot of the named date. Each other reference is the page the grade uses. Done when every prompt has a judgment for every provider that is not unavailable.
- SPEC-5. Date-cut row, all six columns, judged against that dated snapshot. Keenable search and the router pass `query_time` and the cell is labeled index-as-of. Exa, Parallel, SELECT, and Grok native are live search, the date is only in the question, and the cell is labeled live search. Those four cells are grades, not blanks.
- SPEC-6. Before a run, each block says "Not run on Grok yet." The 60 block fills only when `audits/spacex-grok/` meets SPEC-4's done-when under its own cap. The new-set block fills only when `audits/spacex-jobs/` does. A partial file does not fill its block.
- SPEC-7. The cap is $200 of harness spend on each directory: model tokens, Exa, Parallel, Keenable, and the judge. A preflight prints the estimate from the prompt count and the pinned prices. If that estimate is already over $200, the run does not start. Hitting the cap mid-run leaves that block unfilled.
- Per-query sources stay as already accepted: cited URLs, reference URLs missed, URLs only that provider cited, pages fetched and not cited.

## Accepted page

The artifact is the SpaceX report, walked from the top. Andrey's gap report is unchanged and is not this page.

Order:

1. Quote. Elon Musk, 7 Oct 2026, verbatim, about the best model for the task. One sentence after it: the same principle is why the search tool should be the best one for the job. It does not say he promised to switch search.
2. Reason. Compliance, mission insurance, and reporting. If those questions come back faster and cheaper, that is one way flight rate goes up and cost to orbit comes down. No launch count. No dollars per kilogram.
3. Jobs, unscored until the Grok run exists. A compliance table (the FCC Amazon Leo waiver is the example already in the 60). A physics or engine paper. The same regulation question as of a past date, via search `query_time`. SELECT is the table tool. The page does not say SELECT time-travels.
4. Cursor. Joined SpaceX 14 Aug 2026. Exa has a public Cursor marketplace plugin. The default backend is not claimed. Being considered there is a reason, not a contract.
5. Method. One Grok model on every column. Columns: Exa, Parallel, Keenable search, Keenable SELECT, Grok native, Keenable router. NEEDLE scholar, 7 days through 7 Oct 2026: Keenable 0.71, Exa 0.52, snippets only. That lead is why literature is in the set. A search strong at academic retrieval might help compliance (law and papers) and engineers who need physics papers. The word is might. Elicit agent audit sits beside it: Exa 95, SELECT 89, search 86 of 100. The page does not state an accuracy ranking until that block's directory is complete. It does not state a launch delta or a Cursor contract.
6. The 60, from `audits/spacex-grok/`. Until that run is complete the block says "Not run on Grok yet."
7. The new 60, from `audits/spacex-jobs/`, under the run spec above. Same rule for a partial file.

`audits/spacex/` is not overwritten. The run spec above is the rest of the contract.

## Source comparison (user, 2026-10-08)

Accepted. For each prompt, each provider shows the URLs it cited, the reference URLs it missed, and the URLs no other provider cited on that prompt. The audit-level domain table stays. It is not a substitute for the per-query view. Tool URLs the agent fetched but did not cite are listed separately from citations, so a right page that never made the answer is visible.

The hypothesis, not a result: Keenable search plus SELECT (the router, and each arm on its own) can beat Grok native and Exa on the jobs where the answer is a paper or a table. The page may say that only after the Grok run. The old DeepSeek counts are not that test.

## Not in scope

- Replacing the Andrey gap report.
- A curated claim that Keenable won the DeepSeek 60.
- A numeric launch-rate or cost-to-orbit saving.
- Asserting Cursor's built-in agent uses Exa.
- Asserting SELECT accepts `query_time`.
- Re-running Elicit, StackAI, or Rogo on Grok for this page. D11 answer A.
