# Gap report Andrey opens

Status: plan, 2026-10-07. Replaces the scoreboard as the artifact he sees.
The three audits stay in the repo. They are not the opening screen.

Decision: he opens the full gap report first (stratified log, dollars per correct row, downloadable loss file). A curated prompt set where Keenable wins was considered and dropped. Elicit was already that bet, and Exa leads every type there.

## What he should see

One report, built from a real customer log. Model Diplomat is the first file, in the same columns any later customer would use: task, queries, provider, dollars, timestamp.

Each task is one row:

```
Exa loop (what the agent did)          SELECT (what replaces it)
N searches + fetches + $               one SQL query the agent wrote
the rows that loop returned            the table SELECT returned
                                       miss label on every row Exa had
                                       that SELECT did not
```

The headline is not a leaderboard. It is, by task class: how often SELECT is correct when Exa was correct, and dollars per correct row. The loss file is a download of the misses, each marked `index gap` (the page is not in Keenable) or `extraction miss` (the page was there and the value was wrong).

## What to stop

- Sending him the Elicit, StackAI, or SpaceX scoreboard. On those audits Exa is never behind on correct answers and is cheaper than Keenable search. SELECT and routed are behind on all three. StackAI wide is the opposite of a SELECT win: Exa 20/22, SELECT 10/22.
- Writing a new prompt set so Keenable shines. The prompts have to be his tasks. A set built for a win is the bakeoff he already ignores.
- The shadow SQL writer, and passing a keyword string into SELECT. `select.keenable.ai` parses `query` as SQL. The agent that can see the tool writes it. Current `SelectSession.function_tools` hides that schema and sends the question through. That call returns `SQL parse error`. Put the discovered MCP schema back in front of the agent. The router does not translate keywords into SQL.
- A classifier that picks an arm and forwards a keyword. The audit router is a skill: the agent sees Keenable search and SELECT, uses search when a lookup should match Exa, and writes one SELECT when that replaces a loop. The Model Diplomat production router stays deferred in `TODOS.md`.

## What to build

Reuse the judge, the SELECT MCP client, and the cost split. Do not start a second app.

```
log.jsonl  ->  sample by class  ->  Exa $ from the trace
                                  ->  agent with the research skill + SELECT (SQL tool)
                                  ->  judge both
                                  ->  label misses
                                  ->  /r/<slug>  headline + loss file
```

| Piece | Where | New? |
|---|---|---|
| Log columns | `searchaudit/gap/log.py` | yes |
| Stratified sample, weighted back to the log | `searchaudit/gap/sample.py` | yes |
| SELECT arm | `searchaudit/providers/select.py` | the agent sees the SQL tool and a research skill (`research_skill.py`) so it writes the query. The tasks stay the customer's. |
| Exa dollars | the trace's own search, rerank, and context cost, not a new Exa run | read from the log |
| Judge | `searchaudit/judge.py` | reuse |
| Miss label | `searchaudit/gap/loss.py` | yes. `index gap` if the URL is absent from SELECT evidence, `extraction miss` if the URL is present and the value is wrong |
| Report | one page on the existing report app, plus `loss.csv` | yes |
| First log | `audits/diplomat/log.jsonl` | yes, and it is the blocker |

Six task classes, tagged on the log, with a spot-check of 30 tags before the sample is frozen. Sample about 30 tasks per class. One class with fewer than 10 tasks gets no rate.

## How we know it is not a bakeoff

All of these are true, or the report does not go to him:

1. The first screen is a task from `audits/diplomat/log.jsonl`, not a generated prompt from `audits/elicit`, `audits/stackai`, or `audits/spacex`.
2. The headline is match rate and dollars per correct row by class. It does not say which provider won a leaderboard.
3. Every miss in `loss.csv` is `index gap` or `extraction miss`, and a URL-only row with no label fails the check.
4. The SELECT call stored on that task is SQL the agent sent (`WEB_SEARCH` / `SEM_EXTRACT`), not the raw task sentence. A stored `SQL parse error` fails the check.
5. Exa dollars on the row equal the trace total, not a fresh search-API price.

## Not in this plan

- Rerunning the three scoreboard audits.
- A prompt set chosen because Keenable wins it.
- Live editing of SELECT in the room.
- The Model Diplomat production router, kill switch, and Exa shadow sample.
- Production-readiness, compliance, and migration sections. Those stay in `TODOS.md`.

## Blocker

There is no Model Diplomat export in this repo. Nothing in this plan renders until `audits/diplomat/log.jsonl` exists with the five columns. Generating more company prompts does not unblock it.
